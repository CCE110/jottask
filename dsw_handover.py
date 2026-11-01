"""DSW post-sale "Sales Handover Form" SMS -> a non-lead TODO, never a lead.

SMS shape (from DSW's trigger numbers, same as every other DSW text - no distinguishing sender):
    "Hi Rob, congrats on your sale to <name>!  To finalise your sale, please complete the Sales Handover Form: https://jetsubmit.com/f/..."

Rules (Rob, 1 Nov 2026):
  * Classified BEFORE the signal / new-lead patterns, so it can never reach poller.process (no PipeReply contact,
    no OpenSolar project, nothing superseded). Before this, "Sales Handover Form" became a junk lead (#06cbcf) and
    full-name versions (Adam Glyde, Damian Hadjinicolaou) minted duplicate PipeReply contacts + superseded tasks.
  * The sold customer is fuzzy-matched against existing DSW tasks (pending, or worked within 60 days). On a match the
    lead's task gets a note "Sale closed, handover form: <link>"; its status is never changed.
  * ONE open handover TODO per customer: a repeat SMS adds a note to it, never a second task. A lookup error raises
    DedupLookupError (fail closed: nothing is written, the poller retries next run).
  * The TODO is NOT a lead: its own category (HANDOVER_CATEGORY) so it never gets the DSW lead email / NEW LEAD badge
    / lead-status buttons, and lead_status='post_sale'.
"""
import re
from datetime import datetime, timedelta, timezone

import dsw_contact_dedup as dd

HANDOVER_CATEGORY = 'DSW Post-Sale'
LEAD_STATUS = 'post_sale'
WINDOW_DAYS = 60
AEST = timezone(timedelta(hours=10))

_SALE = re.compile(r'congrats on your sale to\s+(.+?)\s*(?:!|\n|$)', re.I)
_LINK = re.compile(r'https?://(?:www\.)?jetsubmit\.com/\S+', re.I)


def classify_handover(text):
    """-> {'name': str|None, 'link': str|None} for a post-sale handover SMS, else None."""
    t = text or ''
    sale, link = _SALE.search(t), _LINK.search(t)
    if not (sale or link or re.search(r'sales handover form', t, re.I)):
        return None
    name = dd.strip_nick(sale.group(1)).strip(' .') if sale else None
    return {'name': name or None, 'link': link.group(0).rstrip('.,)') if link else None}


def _iso(s):
    try:
        return datetime.fromisoformat((s or '').replace('Z', '+00:00'))
    except Exception:
        return None


def _query(sb, **eqs):
    try:
        q = sb.table('tasks').select('id, title, client_name, client_phone, client_email, status, category, '
                                     'lead_status, created_at, completed_at, metadata')
        for k, v in eqs.items():
            q = q.eq(k, v)
        return q.execute().data or []
    except Exception as e:
        raise dd.DedupLookupError(f'handover lookup failed: {e}')


def _same_person(name, task_name):
    a, b = dd.strip_nick(name).lower(), dd.strip_nick(task_name).lower()
    if not a or not b:
        return False
    if len(a.split()) == 1 or len(b.split()) == 1:          # first name only ("Ajay"): compare first tokens
        return a.split()[0] == b.split()[0]
    return dd._names_match(a, b)


def find_sold_task(sb, user_id, name, now):
    """Best existing DSW lead task for the sold customer, or (None, ambiguous). Pending first, then most recent."""
    if not name:
        return None, False
    cutoff = now - timedelta(days=WINDOW_DAYS)
    cands = []
    for t in _query(sb, user_id=user_id, category='DSW Solar'):
        if t.get('status') == 'cancelled' or not _same_person(name, t.get('client_name')):
            continue
        when = _iso(t.get('completed_at')) or _iso(t.get('created_at'))
        if t.get('status') == 'pending' or (when and when >= cutoff):
            cands.append(t)
    people = {dd.strip_nick(t.get('client_name')).lower() for t in cands}
    if len(people) > 1 and len(name.split()) == 1:         # "Ajay" matches two different Ajays -> do not guess
        return None, True
    cands.sort(key=lambda t: (t.get('status') == 'pending', t.get('completed_at') or t.get('created_at') or ''), reverse=True)
    return (cands[0] if cands else None), False


def find_open_handover(sb, user_id, name, linked_id):
    for t in _query(sb, user_id=user_id, category=HANDOVER_CATEGORY, status='pending'):
        if linked_id and (t.get('metadata') or {}).get('handover_for') == linked_id:
            return t
        if _same_person(name or '', t.get('client_name')):
            return t
    return None


def _due(now):
    n = now.astimezone(AEST)
    d = n + timedelta(hours=1) if n.hour < 19 else (n + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return d.strftime('%Y-%m-%d'), d.strftime('%H:%M:00')


def apply_handover(text, *, sb, tm, user_id, dry_run=False, now=None):
    """-> dict(action: 'create'|'update', task_id, linked_task_id, ambiguous). Raises DedupLookupError on any lookup error."""
    info = classify_handover(text)
    if info is None:
        return {'action': 'skip'}
    now = now or datetime.now(timezone.utc)
    name, link = info['name'], info['link']
    sold, ambiguous = find_sold_task(sb, user_id, name, now)
    linked_id = sold['id'] if sold else None
    display = (sold or {}).get('client_name') or name or 'customer'
    existing = find_open_handover(sb, user_id, display, linked_id)
    note = f"Sale closed, handover form: {link or '(link not in SMS)'}"
    res = {'action': 'update' if existing else 'create', 'task_id': (existing or {}).get('id'),
           'linked_task_id': linked_id, 'ambiguous': ambiguous}
    if dry_run:
        return res
    if existing:
        tm.add_note(existing['id'], f'Repeat handover SMS. {note}', source='dsw_sms')
        return res
    due_date, due_time = _due(now)
    row = {'user_id': user_id, 'category': HANDOVER_CATEGORY, 'lead_status': LEAD_STATUS, 'status': 'pending',
           'title': f'Post-sale: complete Sales Handover Form – {display}', 'client_name': display,
           'client_phone': (sold or {}).get('client_phone') or None, 'priority': 'high',
           'due_date': due_date, 'due_time': due_time,
           'description': (f'Sale closed for {display}. Complete the Sales Handover Form to brief the install team:\n'
                           f'{link or "(link not in SMS)"}\n\n'
                           + (f'Lead task: https://www.jottask.app/task/{linked_id}\n' if linked_id else
                              ('Could not match a single lead task (ambiguous first name) - check the name.\n' if ambiguous else
                               'No matching lead task found.\n'))),
           'metadata': {'handover_for': linked_id, 'form_link': link}}
    ins = sb.table('tasks').insert(row).execute()
    res['task_id'] = ins.data[0]['id'] if ins.data else None
    if linked_id:
        tm.add_note(linked_id, note, source='dsw_sms')
    return res
