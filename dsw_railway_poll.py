"""
Railway-side DSW lead poll.

Replaces the Mac-cron `dsw_sms_poller.py` path. Pulls last-7d PipeReply
contacts (tag-filtered via dsw_lead_poller.get_contacts) and runs
dsw_lead_poller.process() on any that don't already have a DSW Solar task
in Supabase.

The dedup is *Supabase-backed* rather than file-backed (~/.dsw_processed_leads.json
doesn't survive Railway deploys) AND it's our own pre-check, so we never call
process() for a lead that has any existing DSW Solar task in the 7-day window.
This is belt-and-braces above process()'s internal 2h dedup + migrate logic,
which would otherwise re-fire the lead email every poll cycle after 2h.

Match keys (in priority order):
  1. normalised phone (strong signal, survives name typos / renames)
  2. lowercased client_name (fallback for phoneless leads)

Anything that matches either key is skipped. Anything else gets processed.

Exposes:
  poll_dsw_pipereply(dry_run=False) -> dict with counts + (in dry-run) plans.

Designed to be invoked from saas_scheduler.py every 10 minutes.
"""

import os
import traceback
from datetime import datetime, timedelta, timezone


def _normalize_phone_for_match(p):
    """Mirror dsw_lead_poller._normalize_phone — strip non-digits, drop +61."""
    import re
    if not p:
        return ''
    d = re.sub(r'\D', '', str(p))
    if d.startswith('61') and len(d) == 11:
        d = '0' + d[2:]
    return d


def _existing_dsw_keys(tm, days=7):
    """Build the dedup key sets from Supabase tasks (DSW Solar, last `days`).

    Returns (phones:set[str], names:set[str]).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    res = tm.supabase.table('tasks').select(
        'id, client_name, client_phone, status, created_at'
    ).eq('category', 'DSW Solar').gte('created_at', cutoff).execute()

    phones, names = set(), set()
    for t in (res.data or []):
        ph = _normalize_phone_for_match(t.get('client_phone') or '')
        if ph:
            phones.add(ph)
        nm = (t.get('client_name') or '').lower().strip()
        if nm:
            names.add(nm)
    return phones, names, len(res.data or [])


def _contact_keys(c):
    """Extract (phone, name_lower) match keys from a PipeReply contact dict."""
    phone = _normalize_phone_for_match(c.get('phone', '') or '')
    name = (
        c.get('contactName')
        or f"{c.get('firstName','')} {c.get('lastName','')}".strip()
        or ''
    ).lower().strip()
    return phone, name


def poll_dsw_pipereply(dry_run=False, lookback_minutes=30):
    """Pull DSW leads from PipeReply and process any without an existing task.

    Args:
      dry_run: if True, don't call process() — just report what would happen.
      lookback_minutes: only consider PipeReply contacts whose dateAdded is
        within this window. Default 30 (well clear of the 10-min cadence,
        small enough to never re-fire a deluge of historical leads on the
        first deploy). Pass a much larger number to backfill old leads
        explicitly — see scripts/dsw_railway_backfill (TBD) for one-shot use.

    Returns:
      dict with keys: ok, contacts_seen, contacts_in_window, skipped_dedup,
                      skipped_too_old, processed, errors, existing_tasks,
                      plan (only in dry_run).
    """
    if not os.getenv('PIPEREPLY_TOKEN'):
        msg = '[dsw_poll] PIPEREPLY_TOKEN missing — skipping'
        print(msg)
        return {'ok': False, 'reason': 'PIPEREPLY_TOKEN missing'}

    try:
        import dsw_lead_poller as dsw
        from task_manager import TaskManager
    except Exception as e:
        print(f'[dsw_poll] could not import deps: {e}')
        return {'ok': False, 'reason': f'import failed: {e}'}

    tm = TaskManager()
    started = datetime.now(timezone.utc)

    try:
        phones, names, total_existing = _existing_dsw_keys(tm, days=7)
    except Exception as e:
        print(f'[dsw_poll] Supabase dedup-key fetch failed: {e}')
        return {'ok': False, 'reason': f'dedup fetch failed: {e}'}

    print(f'[dsw_poll] {total_existing} DSW Solar task(s) in last 7d '
          f'→ phones={len(phones)} names={len(names)}')

    try:
        contacts = dsw.get_contacts() or []
    except Exception as e:
        print(f'[dsw_poll] get_contacts failed: {e}')
        return {'ok': False, 'reason': f'pipereply fetch failed: {e}'}

    # Window cutoff: only consider PipeReply contacts created in the last
    # `lookback_minutes`. Anything older is treated as "backlog" — we don't
    # touch it from the recurring poll so a deploy can't fire a deluge of
    # historical lead emails to Rob.
    window_cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)

    processed = 0
    errors = 0
    skipped_dedup = 0
    skipped_too_old = 0
    plan = []

    for c in contacts:
        phone, name = _contact_keys(c)
        cid = (c.get('id') or '')[:8]

        # Window filter first — cheap and the dominant reason to skip.
        date_added_raw = c.get('dateAdded') or ''
        try:
            date_added = datetime.fromisoformat(date_added_raw.replace('Z', '+00:00'))
        except Exception:
            date_added = None
        if date_added and date_added < window_cutoff:
            skipped_too_old += 1
            if dry_run:
                plan.append({'action': 'skip_old', 'cid': cid, 'name': name,
                             'phone': phone, 'date_added': date_added_raw})
            continue

        match_reason = None
        if phone and phone in phones:
            match_reason = f'phone {phone}'
        elif name and name in names:
            match_reason = f'name {name!r}'

        if match_reason:
            skipped_dedup += 1
            if dry_run:
                plan.append({'action': 'skip_dedup', 'cid': cid, 'name': name,
                             'phone': phone, 'reason': match_reason})
            continue

        if dry_run:
            plan.append({'action': 'process', 'cid': cid, 'name': name,
                         'phone': phone, 'date_added': date_added_raw})
            continue

        try:
            print(f'[dsw_poll] processing new lead: cid={cid} name={name!r} phone={phone!r}')
            dsw.process(c)
            processed += 1
        except Exception as e:
            errors += 1
            print(f'[dsw_poll] process error for {name!r} (cid={cid}): {e}')
            traceback.print_exc()

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    summary = {
        'ok': True,
        'dry_run': dry_run,
        'lookback_minutes': lookback_minutes,
        'contacts_seen': len(contacts),
        'existing_tasks_7d': total_existing,
        'skipped_too_old': skipped_too_old,
        'skipped_dedup': skipped_dedup,
        'processed': processed,
        'errors': errors,
        'elapsed_sec': round(elapsed, 1),
    }
    if dry_run:
        summary['plan'] = plan

    print(f'[dsw_poll] DONE {summary}')

    # Log to system_events for visibility from /health and the daily digest.
    try:
        from monitoring import log_event
        status = 'success' if errors == 0 else 'warning'
        log_event(
            'dsw_poll',
            f"Railway DSW poll: seen={len(contacts)} processed={processed} "
            f"skipped_old={skipped_too_old} skipped_dedup={skipped_dedup} "
            f"errors={errors} dry_run={dry_run}",
            status=status,
            category='dsw_poll',
            metadata=summary,
        )
    except Exception as e:
        print(f'[dsw_poll] system_events log failed (non-fatal): {e}')

    return summary


if __name__ == '__main__':
    import sys, json
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
    dry = '--dry-run' in sys.argv or '--dry' in sys.argv
    # Optional override: --lookback=<minutes>. Default 30.
    lookback = 30
    for arg in sys.argv[1:]:
        if arg.startswith('--lookback='):
            try:
                lookback = int(arg.split('=', 1)[1])
            except ValueError:
                print(f'bad --lookback value: {arg}')
                sys.exit(2)
    result = poll_dsw_pipereply(dry_run=dry, lookback_minutes=lookback)
    print('\n' + json.dumps(result, indent=2, default=str))
