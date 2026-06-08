"""
Railway-side DSW appointment poll.

Pulls PipeReply appointments for the operator user (Rob) and mirrors them
into Jottask DSW Solar tasks so the reminder system sees structured
appointment times instead of free-text in the description.

Scope (two layers + fail-closed):
  1. /contacts/?locationId=…&assignedTo=ROB_UID — only operator's contacts
     (paginated up to MAX_PAGES).
  2. /contacts/{cid}/appointments — per-contact event list.
  3. Each event is kept only if event.assignedUserId == ROB_UID AND startTime
     parses AND startTime > now. Missing/empty assignedUserId → skip.
     Missing contactId → skip. Unparseable startTime → skip. Every skip is
     logged to system_events for visibility (skipped in dry-run mode).

Action (per kept appointment):
  - Linked task exists (matched first by cid in description, then by
    find_existing_task_by_client on the contact's name):
      · same due_date+due_time → NOOP
      · different → UPDATE due_date/due_time, set lead_status='intro_call',
        reset reminder_sent_at to None so a fresh reminder fires, append a
        task_note recording the reschedule with old → new
  - No linked task exists → CREATE (mirrors handle_dsw_appointment's
    else-branch: title 'Call <name> - DSW Appointment', category 'DSW Solar',
    lead_status 'intro_call', priority 'high', description carries the
    PipeReply CRM link).

Dry-run mode (default): no writes anywhere — no task insert/update, no
system_events log, no task_note insert. Returns a plan.
"""

import os
import re
from collections import Counter
from datetime import datetime, timezone, timedelta

import pytz
import requests as rq

ROB_UID = 'zK43HKCu06NAFEbitnJW'
BASE = 'https://services.leadconnectorhq.com'
AEST = pytz.timezone('Australia/Brisbane')
MAX_PAGES = 10              # safety cap: 10 pages * 100 = 1000 contacts
PAGE_LIMIT = 100

# CRM link prefix used by dsw_lead_poller.make_task. We use this to find the
# task linked to a PipeReply contact id by description scan, more reliable
# than a name match.
CRM_BASE = "https://app.pipereply.com/v2/location/0k6Ix1hW5QoHuUh2YSru/contacts"


def _headers():
    return {
        'Authorization': f"Bearer {os.getenv('PIPEREPLY_TOKEN')}",
        'Content-Type': 'application/json',
        'Version': '2021-07-28',
    }


def _parse_aest(dt_str):
    """PipeReply returns startTime as tz-naive 'YYYY-MM-DD HH:MM:SS'. Treat
    as AEST. Returns aware datetime in AEST, or None on parse failure."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    try:
        naive = datetime.strptime(dt_str.strip(), '%Y-%m-%d %H:%M:%S')
        return AEST.localize(naive)
    except Exception:
        return None


def _list_rob_contacts(loc_id, max_pages=MAX_PAGES):
    """Paginated GET /contacts/?assignedTo=ROB_UID. Yields contact dicts."""
    H = _headers()
    start_after = None
    start_after_id = None
    seen_ids = set()
    for page in range(1, max_pages + 1):
        params = {
            'locationId': loc_id,
            'assignedTo': ROB_UID,
            'limit': PAGE_LIMIT,
        }
        if start_after is not None:
            params['startAfter'] = start_after
        if start_after_id is not None:
            params['startAfterId'] = start_after_id
        r = rq.get(f'{BASE}/contacts/', headers=H, params=params, timeout=20)
        if not r.ok:
            print(f"  /contacts/ page {page} → HTTP {r.status_code}: {r.text[:200]}")
            return
        j = r.json() or {}
        page_contacts = j.get('contacts') or []
        if not page_contacts:
            return
        new_this_page = 0
        for c in page_contacts:
            cid = c.get('id')
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                new_this_page += 1
                yield c
        meta = j.get('meta') or {}
        start_after = meta.get('startAfter')
        start_after_id = meta.get('startAfterId') or meta.get('startAfter_id')
        # Done when fewer than the page limit returned OR no cursor advance OR no new ids
        if len(page_contacts) < PAGE_LIMIT or new_this_page == 0 or not start_after_id:
            return


def _list_appointments(cid):
    """GET /contacts/{cid}/appointments. Returns list of event dicts."""
    r = rq.get(f'{BASE}/contacts/{cid}/appointments',
               headers=_headers(), timeout=15)
    if not r.ok:
        return []
    return (r.json() or {}).get('events') or []


def _find_linked_task(supabase, cid, contact_name, user_id):
    """Find the existing pending DSW Solar task for this PipeReply contact.

    Match order:
      1. description ilike '%/contacts/detail/{cid}%' (most reliable).
      2. find_existing_task_by_client on the contact's display name.

    Returns the task row (dict) or None.
    """
    if cid:
        cid_like = f"%/contacts/detail/{cid}%"
        r = supabase.table('tasks').select(
            'id, client_name, due_date, due_time, lead_status, status, '
            'description, category, reminder_sent_at'
        ).eq('category', 'DSW Solar').eq('status', 'pending')\
         .ilike('description', cid_like).order('created_at', desc=True)\
         .limit(1).execute()
        if r.data:
            return r.data[0]
    if contact_name:
        from task_manager import TaskManager
        tm = TaskManager()
        tm.supabase = supabase  # share the connection
        cand = tm.find_existing_task_by_client(client_name=contact_name,
                                               user_id=user_id)
        if (cand and cand.get('category') == 'DSW Solar'
                and cand.get('status') == 'pending'):
            return cand
    return None


def _log_skip(supabase, reason, event, contact, dry_run):
    """Record a skip in system_events (suppressed in dry-run)."""
    msg = f"[appt_poll] skip: {reason}"
    meta = {
        'reason': reason,
        'event_id': (event or {}).get('id'),
        'contact_id': (contact or {}).get('id') or (event or {}).get('contactId'),
        'event_assignedUserId': (event or {}).get('assignedUserId'),
        'event_startTime': (event or {}).get('startTime'),
        'event_appointmentStatus': (event or {}).get('appointmentStatus'),
    }
    if dry_run:
        print(f"  [dry-run skip log] {msg} meta={meta}")
        return
    try:
        from monitoring import log_event
        log_event('appt_poll', msg, status='warning',
                  category='appt_poll', metadata=meta)
    except Exception as e:
        print(f"  [appt_poll] system_events log failed: {e}")


def poll_appointments(dry_run=True):
    """Returns a dict with the plan and a 'actions' list of decisions."""
    loc_id = os.getenv('PIPEREPLY_LOCATION_ID')
    token = os.getenv('PIPEREPLY_TOKEN')
    if not (loc_id and token):
        print("  [appt_poll] missing PIPEREPLY_TOKEN / PIPEREPLY_LOCATION_ID")
        return {'ok': False, 'reason': 'pipereply env missing'}

    # Supabase (we still read in dry-run — read-only is fine).
    from supabase import create_client
    sb = create_client(os.getenv('SUPABASE_URL'),
                       os.getenv('SUPABASE_SERVICE_KEY')
                       or os.getenv('SUPABASE_KEY'))

    # Operator user_id (Rob's Jottask user) — needed for new task inserts.
    user_id = None
    try:
        u = sb.table('users').select('id')\
              .eq('email', 'rob@cloudcleanenergy.com.au').execute()
        if u.data:
            user_id = u.data[0]['id']
    except Exception as e:
        print(f"  [appt_poll] couldn't resolve operator user_id: {e}")

    now_aest = datetime.now(AEST)

    contacts_seen = 0
    appts_seen = 0
    actions = []
    skips = Counter()

    for c in _list_rob_contacts(loc_id):
        contacts_seen += 1
        cid = c.get('id')
        cname = (c.get('contactName') or
                 f"{c.get('firstName','')} {c.get('lastName','')}".strip())

        events = _list_appointments(cid) if cid else []
        for ev in events:
            appts_seen += 1

            # ── Belt-and-braces scope filter ─────────────────────────────
            event_assigned = ev.get('assignedUserId') or ''
            event_contact = ev.get('contactId') or ''
            start_str = ev.get('startTime')
            start_aest = _parse_aest(start_str)
            status = ev.get('appointmentStatus') or ''
            deleted = bool(ev.get('deleted'))

            if not event_assigned:
                skips['empty_assignedUserId'] += 1
                _log_skip(sb, 'empty assignedUserId', ev, c, dry_run)
                continue
            if event_assigned != ROB_UID:
                skips['assigned_to_other_rep'] += 1
                continue
            if not event_contact:
                skips['missing_contactId'] += 1
                _log_skip(sb, 'missing contactId', ev, c, dry_run)
                continue
            if start_aest is None:
                skips['unparseable_startTime'] += 1
                _log_skip(sb, 'unparseable startTime', ev, c, dry_run)
                continue
            if deleted:
                skips['deleted'] += 1
                continue
            # Past appointments: not in scope per design.
            if start_aest <= now_aest:
                skips['past_startTime'] += 1
                continue
            if status.lower() in ('cancelled', 'canceled', 'noshow'):
                skips[f'status_{status.lower()}'] += 1
                continue

            # ── Decide CREATE / UPDATE / NOOP ───────────────────────────
            # Compute the proposed DUE values (after T-60 lead offset) here so
            # NOOP detection compares like-for-like against what we'd write.
            # Without this, every poll would think the time differs (task.due_time
            # = 13:30 vs appt startTime = 14:30) and would re-fire an UPDATE +
            # task_note every tick. Idempotency lives here.
            linked = _find_linked_task(sb, cid, cname, user_id)
            offset_dt = start_aest - timedelta(minutes=DEFAULT_LEAD_OFFSET_MIN)
            proposed_due_date = offset_dt.strftime('%Y-%m-%d')
            proposed_due_time = offset_dt.strftime('%H:%M:00')
            proposed_title = _format_appt_title(cname, start_aest)

            row = {
                'contact_name': cname,
                'contact_id': cid,
                'event_id': ev.get('id'),
                'event_title': ev.get('title') or '',
                'appointment_status': status,
                'calendar_id': ev.get('calendarId'),
                'assignedUserId': event_assigned,
                'start_aest_display': start_aest.strftime('%a %d %b, %I:%M %p AEST'),
                'real_appt_date':     start_aest.strftime('%Y-%m-%d'),
                'real_appt_time':     start_aest.strftime('%H:%M:00'),
                'proposed_due_date':  proposed_due_date,  # T-60 offset
                'proposed_due_time':  proposed_due_time,
                'proposed_title':     proposed_title,
            }

            if linked:
                row['linked_task_id'] = linked['id']
                row['current_due_date']    = linked.get('due_date')
                row['current_due_time']    = linked.get('due_time')
                row['current_lead_status'] = linked.get('lead_status')
                row['current_title']       = linked.get('title')
                same_date   = str(linked.get('due_date')) == proposed_due_date
                same_time   = str(linked.get('due_time')) == proposed_due_time
                same_status = (linked.get('lead_status') or '') == 'intro_call'
                same_title  = (linked.get('title') or '') == proposed_title
                if same_date and same_time and same_status and same_title:
                    row['action'] = 'NOOP'
                else:
                    row['action'] = 'UPDATE'
            else:
                row['linked_task_id'] = None
                row['current_due_date'] = None
                row['current_due_time'] = None
                row['current_lead_status'] = None
                row['action'] = 'CREATE'

            actions.append(row)

    return {
        'ok': True,
        'dry_run': dry_run,
        'contacts_seen': contacts_seen,
        'appointments_seen': appts_seen,
        'actions': actions,
        'skip_counts': dict(skips),
        'now_aest': now_aest.isoformat(),
    }


# ── Option-1 reminder strategy: T-60 offset, true time in title ─────────────
DEFAULT_LEAD_OFFSET_MIN = 60


def _format_appt_title(name, appt_dt_aest):
    """Build the human-readable appointment title.

    Example: '📞 Call Andrew Tan — appt 2:30pm Mon 8 Jun'
    The TRUE appointment time goes here so a glance shows the right time
    even though due_time on the task is offset back by the lead window.
    """
    title_name = ' '.join(w.capitalize() for w in (name or '').split()) or 'Unknown'
    h, m = appt_dt_aest.hour, appt_dt_aest.minute
    hour12 = (h - 1) % 12 + 1
    ampm = 'am' if h < 12 else 'pm'
    time_str = f"{hour12}:{m:02d}{ampm}"
    day_no_zero = int(appt_dt_aest.strftime('%d'))
    date_str = f"{appt_dt_aest.strftime('%a')} {day_no_zero} {appt_dt_aest.strftime('%b')}"
    return f"📞 Call {title_name} — appt {time_str} {date_str}"


def _compute_due_offset(appt_dt_aest, offset_min=DEFAULT_LEAD_OFFSET_MIN):
    """appointment_time − offset_min → (due_date_str, due_time_str) in AEST."""
    new_dt = appt_dt_aest - timedelta(minutes=offset_min)
    return new_dt.strftime('%Y-%m-%d'), new_dt.strftime('%H:%M:00')


def _execute_plan(supabase, plan, lead_offset_min=DEFAULT_LEAD_OFFSET_MIN):
    """Apply UPDATE / CREATE actions to Supabase. Uses the pre-computed proposed
    values from poll_appointments() so NOOP detection there matches reality
    here (idempotency)."""
    from task_manager import TaskManager
    tm = TaskManager()
    results = []
    for r in plan['actions']:
        action = r['action']
        if action == 'NOOP':
            continue
        new_date  = r['proposed_due_date']      # already T-60 offset
        new_time  = r['proposed_due_time']      # already T-60 offset
        new_title = r['proposed_title']         # already includes true appt time

        if action == 'UPDATE':
            tid = r['linked_task_id']
            old_due = f"{r.get('current_due_date')} {r.get('current_due_time')}"
            update_fields = {
                'due_date': new_date,
                'due_time': new_time,
                'title': new_title,
                'lead_status': 'intro_call',
                'reminder_sent_at': None,
            }
            supabase.table('tasks').update(update_fields).eq('id', tid).execute()
            note = (
                f"Appointment poll: linked to PipeReply event {r['event_id']}.\n"
                f"  Actual appointment: {r['start_aest_display']}\n"
                f"  Old due (Jottask): {old_due}\n"
                f"  New due (Jottask, T-{lead_offset_min}min lead offset): "
                f"{new_date} {new_time}\n"
                f"  lead_status: → intro_call, reminder_sent_at reset"
            )
            try:
                tm.add_note(task_id=tid, content=note, source='appt_poll')
            except Exception as e:
                print(f"  [appt_poll] add_note failed for {tid[:8]}: {e}")
            results.append({
                'action': 'UPDATE', 'task_id': tid,
                'new_title': new_title, 'new_due_date': new_date,
                'new_due_time': new_time,
            })

        elif action == 'CREATE':
            users = supabase.table('users').select('id')\
                .eq('email', 'rob@cloudcleanenergy.com.au').execute()
            user_id = users.data[0]['id'] if users.data else None
            if not user_id:
                print(f"  [appt_poll] CREATE skipped — no user_id for operator")
                continue
            task_data = {
                'user_id': user_id,
                'title': new_title,
                'description': (
                    f"Phone: {r.get('phone') or 'N/A'}\n"
                    f"CRM: {CRM_BASE}/detail/{r['contact_id']}\n"
                    f"OpenSolar: pending\n\n"
                    f"Appointment confirmed via PipeReply event {r['event_id']} — "
                    f"{r['start_aest_display']}.\n"
                    f"Jottask due_at offset to T-{lead_offset_min}min for prep window."
                ),
                'due_date': new_date,
                'due_time': new_time,
                'priority': 'high',
                'status': 'pending',
                'category': 'DSW Solar',
                'lead_status': 'intro_call',
                'client_name': ' '.join(w.capitalize() for w in (r['contact_name'] or '').split()),
            }
            ins = supabase.table('tasks').insert(task_data).execute()
            tid = ins.data[0]['id'] if ins.data else None
            results.append({
                'action': 'CREATE', 'task_id': tid,
                'new_title': new_title, 'new_due_date': new_date,
                'new_due_time': new_time,
            })

    return results


def _print_plan(plan):
    """Pretty-print the dry-run plan."""
    print(f"\n=== dsw_appt_poll dry-run ===")
    print(f"now (AEST):               {plan['now_aest']}")
    print(f"contacts scanned:         {plan['contacts_seen']}")
    print(f"appointments seen:        {plan['appointments_seen']}")
    print(f"skip counts:              {plan['skip_counts']}")
    print(f"actions queued:           {len(plan['actions'])}")
    actions = plan['actions']
    if not actions:
        print("\n(no actions to take — nothing future for this operator)")
        return

    print(f"\n{'ACTION':8}  {'contact':24}  {'scheduled (AEST)':28}  {'assignedUserId':22}  "
          f"{'linked_task':10}  {'current due':24}  {'proposed due'}")
    print("-" * 160)
    by_action = Counter()
    for r in actions:
        by_action[r['action']] += 1
        cur = (f"{r['current_due_date']} {r['current_due_time']}"
               if r['current_due_date'] else '-')
        prop = f"{r['proposed_due_date']} {r['proposed_due_time']}"
        ltid = (r['linked_task_id'] or '-')[:8]
        print(f"{r['action']:8}  {r['contact_name'][:24]:24}  "
              f"{r['start_aest_display'][:28]:28}  "
              f"{r['assignedUserId'][:22]:22}  {ltid:10}  "
              f"{cur:24}  {prop}")

    print(f"\nSummary: {dict(by_action)}")


def _reminder_timing_analysis():
    """Quick analysis of when the existing reminder system would fire."""
    from supabase import create_client
    sb = create_client(os.getenv('SUPABASE_URL'),
                       os.getenv('SUPABASE_SERVICE_KEY')
                       or os.getenv('SUPABASE_KEY'))
    u = sb.table('users').select(
        'reminder_minutes_before, daily_summary_time, timezone'
    ).eq('email', 'rob@cloudcleanenergy.com.au').execute()
    cfg = (u.data or [{}])[0]
    rmb = cfg.get('reminder_minutes_before') or 30
    dst = cfg.get('daily_summary_time') or '08:00:00'
    print(f"\n=== Reminder timing analysis (operator: rob@cloudcleanenergy.com.au) ===")
    print(f"  reminder_minutes_before: {rmb} min  ← per-task reminder fires within this window")
    print(f"  daily_summary_time:      {dst} AEST  ← morning digest covers today's tasks")
    print(f"  scheduler tick cadence:  every 60s, 4h floor between re-fires per task")
    print(f"\n  For Andrew Tan due 2026-06-08 14:30 AEST:")
    print(f"    - Morning digest at 08:00 AEST today would list it (date match).")
    print(f"    - Per-task reminder email would fire at 14:00 AEST (30 min before).")
    print(f"      That's tight for a sales prep window.")
    print(f"\n  Recommendation: appointment tasks should have a configurable lead offset")
    print(f"  (e.g. 60–90 min before, or a morning-of nudge in addition to the digest).")
    print(f"  Current behaviour would still fire ONCE at T-30, no earlier ping besides")
    print(f"  the morning digest.")


# ── Throttled wrapper for the worker tick ───────────────────────────────────
# Called from saas_email_processor's while True loop (the REAL worker tick —
# NOT saas_scheduler.run_scheduler, which is the never-invoked phantom).
# 30-minute throttle by default; fail-closed wrapper, never raises.

APPT_POLL_INTERVAL_SEC = int(os.getenv('APPT_POLL_INTERVAL_SEC', '1800'))
_LAST_APPT_POLL_TS = 0.0


def _maybe_run_appt_poll():
    """30-min throttled poll, called from the worker tick.

    Always returns silently — exceptions are caught and logged so a poll
    failure can't kill the worker loop. Successful runs log a single
    summary line to stdout (visible in `railway logs --service
    jottask-worker`) and a system_events row with metadata.
    """
    global _LAST_APPT_POLL_TS
    import time as _time
    now_ts = _time.time()
    if now_ts - _LAST_APPT_POLL_TS < APPT_POLL_INTERVAL_SEC:
        return
    _LAST_APPT_POLL_TS = now_ts
    try:
        # dry_run=False — let skip events reach system_events for visibility.
        # The function itself doesn't write tasks; _execute_plan does that.
        plan = poll_appointments(dry_run=False)
        if not plan.get('ok'):
            print(f"[appt_poll] plan failed: {plan.get('reason')}")
            return

        from supabase import create_client
        from db_keys import get_admin_key
        sb = create_client(os.getenv('SUPABASE_URL'), get_admin_key())
        results = _execute_plan(sb, plan)

        n_update = sum(1 for r in results if r.get('action') == 'UPDATE')
        n_create = sum(1 for r in results if r.get('action') == 'CREATE')
        n_actions = len(plan.get('actions') or [])
        n_noop = sum(1 for r in plan.get('actions') or [] if r.get('action') == 'NOOP')
        msg = (f"appt_poll: contacts={plan.get('contacts_seen')} "
               f"appts={plan.get('appointments_seen')} "
               f"actions={n_actions} "
               f"updates={n_update} creates={n_create} noops={n_noop} "
               f"skips={plan.get('skip_counts')}")
        print(f"[appt_poll] {msg}")
        try:
            from monitoring import log_event
            log_event('appt_poll', msg, status='success', category='appt_poll',
                      metadata={
                          'contacts_seen': plan.get('contacts_seen'),
                          'appointments_seen': plan.get('appointments_seen'),
                          'updates': n_update,
                          'creates': n_create,
                          'noops': n_noop,
                          'skip_counts': plan.get('skip_counts'),
                      })
        except Exception as e:
            print(f"[appt_poll] system_events log failed: {e}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"⚠️ appt_poll error (non-fatal): {e}")


if __name__ == '__main__':
    import sys
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
    execute = '--execute' in sys.argv
    dry = not execute   # safe default: dry-run unless --execute is explicit
    plan = poll_appointments(dry_run=dry)
    _print_plan(plan)
    if execute:
        print("\n=== EXECUTE MODE — writing to Supabase ===")
        from supabase import create_client
        sb = create_client(os.getenv('SUPABASE_URL'),
                           os.getenv('SUPABASE_SERVICE_KEY')
                           or os.getenv('SUPABASE_KEY'))
        results = _execute_plan(sb, plan)
        for res in results:
            print(f"  ✓ {res['action']}  task_id={res['task_id']}")
            print(f"     title:    {res['new_title']!r}")
            print(f"     due_date: {res['new_due_date']}  due_time: {res['new_due_time']}")
    _reminder_timing_analysis()
