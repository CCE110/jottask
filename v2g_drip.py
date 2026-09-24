"""V2G Charger Follow-up drip — 3/day weekday supplemental follow-up campaign.

15 V2G-Ready-tagged leads receive one "⚡ V2G CHARGER FOLLOW-UP" email each
across 5 weekdays (Mon 28 Sep → Fri 2 Oct 2026), warmest first. Each email is
a supplemental nudge on top of the normal reminder cadence — the real
lead_status stays untouched in the DB (Andrew Tan stays customer_deciding,
Orazio stays customer_deciding, etc.). The badge override is achieved by
monkeypatching STATUS_LABELS['v2g_followup'] into the module and passing that
value in a copy of the task dict — no schema, no data write.

State model — no new table:
  system_events{event_type='v2g_drip_sent',  metadata.task_id=<uuid>}
      one row per successful send. Sum tells us how many are done.
  system_events{event_type='v2g_drip_tick'}
      one row per day the drip ran, so a mid-window recovery tick doesn't
      double-fire. Same pattern as _squad_tuesday_already_sent_today.

Schedule — Mon-Fri 08:00-11:59 AEST window. If the worker blips at 08:00, the
next tick inside the window sends the day's 3 (or however many remain in the
queue). Once all 15 have sent rows, run_v2g_drip() returns 'done' and does
nothing.

Rate-limit: reminders bypass the circuit breaker (send_email sets
_rl_bypass = bool(reminder_tag) or bool(appointment)). 3 sends/day across 3
different contacts never approaches any threshold anyway.

Completed-task safety: 8 of the 15 tasks have status='completed'
(customer_deciding leads Rob's already worked). send_dsw_reminder_for_task
only calls send_email — neither function reads or writes tasks.status, so
the completed task's lifecycle isn't touched. The scheduler's reminder loops
filter on status='pending' so the completed task doesn't get double-reminded
either.
"""

from datetime import datetime, date, timezone, timedelta
import pytz

ROB_USER_ID = 'e515407e-dbd6-4331-a815-1878815c89bc'

# Warmest first. task_id prefixes are matched against tasks.id UUID.
V2G_DRIP_ORDER = [
    # Mon 28 Sep — first 3 customer_deciding
    ('23a296a5', 'Andrew Tan'),
    ('71ebd1bb', 'Frank Huxley'),
    ('137db8e2', 'Sam Huang'),
    # Tue 29 Sep — next 3 customer_deciding
    ('7fdac63b', 'Donna Douglas'),
    ('6764bd08', 'Lindsay Chang'),
    ('059712ed', 'Timothy De Jersey'),
    # Wed 30 Sep — last 2 customer_deciding + 1 pending
    ('78bbeae8', 'Johnson Chan'),
    ('3fadb8b7', 'Tony Neech'),
    ('2557154f', 'Orazio Liggieri'),
    # Thu 1 Oct — next 3 pending
    ('c0200ded', 'Cyrus Taraporewalla'),
    ('59e7658b', 'Bernardo Tobias'),
    ('567f5a14', 'Catherine Philpot'),
    # Fri 2 Oct — last 3 pending (nurture)
    ('b8be1d8b', 'Michael Lord'),
    ('1ccc9844', 'Ben Konarov'),
    ('a7b33b2d', 'Greg & Kathy Cunningham'),
]

V2G_START_DATE = date(2026, 9, 28)   # Monday
V2G_PER_DAY    = 3
V2G_LABEL      = '⚡ V2G CHARGER FOLLOW-UP'


def _sb():
    from task_manager import TaskManager
    return TaskManager().supabase


def _sent_task_ids():
    """Set of task_ids that already have a v2g_drip_sent event."""
    r = _sb().table('system_events').select('metadata')\
        .eq('event_type', 'v2g_drip_sent').execute().data or []
    ids = set()
    for row in r:
        md = row.get('metadata') or {}
        tid = md.get('task_id') if isinstance(md, dict) else None
        if tid: ids.add(tid)
    return ids


def _already_ran_today(now_aest):
    """True if a v2g_drip_tick row exists for today (AEST calendar day)."""
    today_start = now_aest.replace(hour=0, minute=0, second=0, microsecond=0)\
                          .astimezone(timezone.utc)
    r = _sb().table('system_events').select('id')\
        .eq('event_type', 'v2g_drip_tick')\
        .gte('created_at', today_start.isoformat())\
        .limit(1).execute().data or []
    return bool(r)


def _resolve_task(prefix, all_tasks=None):
    """Find Rob's DSW task whose UUID starts with prefix."""
    if all_tasks is None:
        all_tasks = _sb().table('tasks').select('*')\
            .eq('user_id', ROB_USER_ID).eq('category', 'DSW Solar').execute().data or []
    return next((t for t in all_tasks if t['id'].startswith(prefix)), None)


def _install_v2g_label():
    """Monkeypatch STATUS_LABELS in-memory so the badge renders correctly.
    Idempotent — safe to call every tick."""
    from dsw_lead_poller import STATUS_LABELS
    STATUS_LABELS['v2g_followup'] = V2G_LABEL


def run_v2g_drip(dry_run=False, now_override=None):
    """Fire up to V2G_PER_DAY V2G follow-up emails if the window + gates pass.

    dry_run=True     — simulate, don't send, don't write events.
    now_override     — pytz-aware datetime; substitute for datetime.now(aest).
                       Used by the dry-run harness to simulate a specific day.

    Returns a dict describing what happened:
      {'action': 'skipped'|'done'|'fired', ...}
    """
    aest = pytz.timezone('Australia/Brisbane')
    now_aest = now_override or datetime.now(aest)
    today = now_aest.date()

    # Weekend skip
    if now_aest.weekday() >= 5:
        return {'action': 'skipped', 'reason': 'weekend',
                'day': now_aest.strftime('%a %d %b %Y')}

    # Time gate — 08:00-11:59 AEST catch-up window (same pattern as squad)
    if now_aest.hour < 8 or now_aest.hour >= 12:
        return {'action': 'skipped', 'reason': 'outside_time_window',
                'time': now_aest.strftime('%H:%M AEST')}

    # Pre-start gate
    if today < V2G_START_DATE:
        return {'action': 'skipped', 'reason': 'pre_start_date',
                'today': today.isoformat(),
                'starts': V2G_START_DATE.isoformat()}

    # Already ran today?
    if not dry_run and _already_ran_today(now_aest):
        return {'action': 'skipped', 'reason': 'already_ran_today',
                'today': today.isoformat()}

    # Build remaining queue
    sent_ids = _sent_task_ids()
    all_tasks = _sb().table('tasks').select('*')\
        .eq('user_id', ROB_USER_ID).eq('category', 'DSW Solar').execute().data or []

    remaining = []
    missing = []
    for pfx, name in V2G_DRIP_ORDER:
        task = _resolve_task(pfx, all_tasks)
        if not task:
            missing.append((pfx, name))
            continue
        if task['id'] in sent_ids:
            continue
        remaining.append((pfx, name, task))

    if not remaining:
        return {'action': 'done', 'reason': 'all_sent',
                'sent_count': len(sent_ids),
                'missing_from_db': missing}

    to_fire = remaining[:V2G_PER_DAY]

    _install_v2g_label()

    from dsw_lead_poller import send_dsw_reminder_for_task

    fired = []
    for pfx, name, task in to_fire:
        task_copy = dict(task)
        task_copy['lead_status'] = 'v2g_followup'  # in-memory only, DB unchanged

        if dry_run:
            fired.append({
                'status': 'DRY-RUN would send',
                'task_id': task['id'],
                'name': name,
                'db_lead_status': task.get('lead_status'),
                'db_status': task.get('status'),
                'badge_will_render': V2G_LABEL,
            })
            continue

        try:
            ok, err = send_dsw_reminder_for_task(task_copy, 'v2g-followup')
        except Exception as e:
            ok, err = False, f'exc: {e!r}'

        if ok:
            _sb().table('system_events').insert({
                'event_type': 'v2g_drip_sent',
                'category': 'v2g_drip',
                'status': 'success',
                'message': f'V2G Charger Follow-up sent: {name} [{task["id"][:8]}]',
                'metadata': {'task_id': task['id'], 'client_name': name,
                             'day_index': V2G_DRIP_ORDER.index((pfx, name)) + 1},
                'user_id': ROB_USER_ID,
            }).execute()
            fired.append({'status': 'sent', 'task_id': task['id'], 'name': name})
        else:
            fired.append({'status': 'failed', 'task_id': task['id'],
                          'name': name, 'err': err})

    # Daily tick row so a second run inside the window is a no-op
    if not dry_run:
        try:
            _sb().table('system_events').insert({
                'event_type': 'v2g_drip_tick',
                'category': 'v2g_drip',
                'status': 'info',
                'message': f'V2G drip ran {today.isoformat()}: {len(fired)} attempted',
                'user_id': ROB_USER_ID,
            }).execute()
        except Exception as e:
            print(f'[v2g_drip] tick insert failed (non-fatal): {e}')

    return {'action': 'fired',
            'date': today.isoformat(),
            'fired': fired,
            'remaining_after': len(remaining) - len(fired),
            'missing_from_db': missing}
