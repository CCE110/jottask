"""Named DSW SMS signals ("New YES reply from X", "Follow Up With X") -> UPDATE the lead's task.

Rule (Rob, 29 Oct 2026): one task per lead. A signal about an EXISTING lead adds a note and re-fires
the reminder; it NEVER inserts a second task. Only an unmatched name creates one (a stub the railway
poll later adopts). Lookup errors raise DedupLookupError so the caller leaves the SMS un-done and the
15-minute cron retries (fail closed). No Mac-only imports here, so it is unit-testable.
"""
import re
from datetime import datetime, timedelta, timezone

import dsw_contact_dedup as dd

REOPEN_DAYS = 30
JUNK_TOKENS = {'new', 'yes', 'urgent', 'reminder', 'customer', 'cancelled', 'upcoming', 'follow', 'hi',
               'unstructured', 'dsw', 'review', 'appointment', 'meeting', 'confidence', 'issue', 'no', 'ok'}

_NAME_PART = r"[A-Z][A-Za-z']+(?:-[A-Z][A-Za-z']+)?(?:[A-Z][A-Za-z']+)?"
NAME_FULL = rf'{_NAME_PART}(?:\s+{_NAME_PART})+'


def is_junk_name(name):
    toks = [t.lower() for t in re.split(r'\s+', (name or '').strip()) if t]
    return not toks or toks[0] in JUNK_TOKENS or all(t in JUNK_TOKENS for t in toks)


def classify_signal(text):
    """Return the person's name if `text` is a named signal about an existing lead, else None."""
    for pat in (rf'\breply from\s+({NAME_FULL})',
                rf'({NAME_FULL})\s+replied\s+YES',
                rf'Cancelled Meeting Follow Up with\s+({NAME_FULL})',
                rf'Follow Up With\s+({NAME_FULL})'):
        m = re.search(pat, text or '')
        if m and not is_junk_name(m.group(1)):
            return re.sub(r'\s+', ' ', m.group(1)).strip()
    return None


def apply_signal(name, raw, *, sb, tm, user_id, create_stub, send_reminder=None, pr_get=None,
                 dry_run=False, now=None):
    """Update the lead's task for this signal, or create a stub when no lead matches.

    create_stub(raw, display_name, contact) -> task_id   (injected; sends the "review" email)
    send_reminder(task, tag) -> (ok, err)                 (injected dsw_lead_poller.send_dsw_reminder_for_task)
    Returns {'action': 'update'|'reopen'|'create_stub', 'task_id': ...}. Raises dd.DedupLookupError.
    """
    now = now or datetime.now(timezone.utc)
    task, contact = dd.find_existing_for_contact(sb, user_id, name=name, pr_get=pr_get)
    reopen = False
    if not task:
        task = dd.find_pending_dsw_task(
            sb, user_id, cid=(contact or {}).get('id') or '', phone=(contact or {}).get('phone') or '',
            names=[name, (contact or {}).get('contactName')], status='completed',
            completed_since=(now - timedelta(days=REOPEN_DAYS)).isoformat())
        reopen = bool(task)

    if not task:
        display = (contact or {}).get('contactName') or name
        if dry_run:
            return {'action': 'create_stub', 'task_id': None, 'display_name': display}
        tid = create_stub(raw, display, contact)
        return {'action': 'create_stub', 'task_id': tid, 'display_name': display}

    result = {'action': 'reopen' if reopen else 'update', 'task_id': task['id']}
    if dry_run:
        return result
    stamp = now.astimezone(timezone(timedelta(hours=10))).strftime('%d %b %Y %H:%M AEST')
    tm.add_note(task['id'], f'DSW SMS signal {stamp}: "{(raw or "").strip()}"'
                + (' — lead re-opened (it had been completed).' if reopen else ''), source='dsw_sms')
    fields = {'reminder_sent_at': None}
    if reopen:
        due = now.astimezone(timezone(timedelta(hours=10))) + timedelta(hours=1)
        fields.update(status='pending', completed_at=None,
                      due_date=due.strftime('%Y-%m-%d'), due_time=due.strftime('%H:%M:00'))
    sb.table('tasks').update(fields).eq('id', task['id']).execute()
    if send_reminder and not dd.is_stub(task):
        try:
            ok, err = send_reminder({**task, **fields}, 'signal')
            if ok:
                sb.table('tasks').update({'reminder_sent_at': now.isoformat()}).eq('id', task['id']).execute()
            else:
                print(f'[SMS signal] reminder send failed for {task["id"][:8]}: {err}')
        except Exception as e:
            print(f'[SMS signal] reminder send error for {task["id"][:8]} (note already saved): {e}')
    return result
