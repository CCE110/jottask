"""One PipeReply contact = one pending DSW Solar task, whichever path sees them first.

Shared by the email handlers in saas_email_processor.py (Appointment Confirmation and
New Task Assigned). The railway poll (dsw_lead_poller.process) dedups on client_phone /
client_name, so every task created here carries client_phone + the CRM contact URL.

Contract:
  * resolve_pr_contact()  READ-ONLY PipeReply lookup: phone -> email -> name. Never creates.
  * find_pending_dsw_task() matches PENDING DSW Solar tasks only (a completed/cancelled
    task must not block a fresh job) by contact id, then phone, then name.
  * Every lookup FAILS CLOSED: any error raises DedupLookupError. Callers must stop and
    must not insert (Silke/Aimee 2026-09-10: a swallowed lookup error spawned duplicates).
"""
import os
import re
from difflib import SequenceMatcher

PR_BASE = 'https://services.leadconnectorhq.com'
PR_LOCATION = os.getenv('PIPEREPLY_LOCATION_ID') or '0k6Ix1hW5QoHuUh2YSru'
CRM_CONTACT_URL = f'https://app.pipereply.com/v2/location/{PR_LOCATION}/contacts/detail/'
NAME_FUZZY = 0.85


# Marks a task created from a name-only signal (SMS "YES reply from X") before the lead itself exists.
# The poll treats a stub as "no real task yet" and adopts it (supersede) when the lead arrives.
STUB_MARKER = '<!-- DSW-SIGNAL-STUB -->'


def is_stub(task):
    return STUB_MARKER in ((task or {}).get('description') or '')


class DedupLookupError(Exception):
    """A dedup lookup failed. The caller must NOT insert."""


def phone_digits(p):
    """'+61 468 876 196' / '0468876196' -> '0468876196'. '' when not an AU mobile-length number."""
    d = re.sub(r'\D', '', str(p or ''))
    if d.startswith('61') and len(d) == 11:
        d = '0' + d[2:]
    elif len(d) == 9 and d.startswith('4'):
        d = '0' + d
    return d if len(d) == 10 else ''


def phone_variants(p):
    """Every stored form the poll / manual scripts use: 04…, +614…, 4…"""
    d = phone_digits(p)
    return {d, '+61' + d[1:], d[1:]} if d else set()


def to_e164(p):
    d = phone_digits(p)
    return '+61' + d[1:] if d else ''


def cid_from_text(text):
    m = re.search(r'/contacts/detail/([A-Za-z0-9]+)', text or '')
    return m.group(1) if m else ''


def _names_match(a, b):
    a, b = (a or '').strip().lower(), (b or '').strip().lower()
    return bool(a and b and (a == b or SequenceMatcher(None, a, b).ratio() >= NAME_FUZZY))


def _pr_get(path, params):
    import requests
    token = os.getenv('PIPEREPLY_TOKEN')
    if not token:
        raise DedupLookupError('PIPEREPLY_TOKEN not set')
    r = requests.get(PR_BASE + path, params=params, timeout=12,
                     headers={'Authorization': f'Bearer {token}', 'Version': '2021-07-28'})
    if not r.ok:
        raise DedupLookupError(f'PipeReply {path} -> HTTP {r.status_code}')
    return r.json()


def resolve_pr_contact(name='', phone='', email='', pr_get=None):
    """Find the PipeReply contact (read-only). Order: phone -> email -> name.

    Returns the contact dict or None (definitive: not found). Raises DedupLookupError if
    PipeReply could not be queried, because "unknown" must not be treated as "no contact".
    """
    pr_get = pr_get or _pr_get
    try:
        def search(q):
            return (pr_get('/contacts/', {'locationId': PR_LOCATION, 'query': q, 'limit': 10}) or {}).get('contacts') or []

        d = phone_digits(phone)
        if d:
            seen = set()
            for q in (to_e164(phone), d):
                for c in search(q):
                    if c.get('id') not in seen:
                        seen.add(c.get('id'))
                        if phone_digits(c.get('phone')) == d:
                            return c
        em = (email or '').strip().lower()
        if em:
            for c in search(em):
                if (c.get('email') or '').strip().lower() == em:
                    return c
        if (name or '').strip():
            best, best_score = None, 0.0
            for c in search(name.strip()):
                s = SequenceMatcher(None, name.strip().lower(), (c.get('contactName') or '').strip().lower()).ratio()
                if s >= NAME_FUZZY and s > best_score:
                    best, best_score = c, s
            return best
        return None
    except DedupLookupError:
        raise
    except Exception as e:
        raise DedupLookupError(f'PipeReply lookup failed: {type(e).__name__}: {e}')


_COLS = 'id, title, client_name, client_phone, client_email, status, lead_status, due_date, due_time, description, category, reminder_sent_at, completed_at, user_id'


def find_pending_dsw_task(supabase, user_id, cid='', phone='', names=(), status='pending', completed_since=None):
    """DSW Solar task (default: PENDING) for this contact: contact id -> phone -> name. None = definitive no match.

    status='completed' with completed_since=<iso> finds a recently worked lead (used to reopen on a new signal).

    Raises DedupLookupError on any query failure.
    """
    def base():
        q = (supabase.table('tasks').select(_COLS).eq('user_id', user_id)
             .eq('category', 'DSW Solar').eq('status', status))
        return q.gte('completed_at', completed_since) if completed_since else q
    try:
        if cid:
            r = base().ilike('description', f'%/contacts/detail/{cid}%').order('created_at', desc=True).limit(1).execute()
            if r.data:
                return r.data[0]
        d = phone_digits(phone)
        if d:
            for v in sorted(phone_variants(phone)):
                r = base().eq('client_phone', v).order('created_at', desc=True).limit(1).execute()
                if r.data:
                    return r.data[0]
            # Older email-created tasks only have "Phone: …" in the description
            r = base().ilike('description', f'%{d[1:]}%').order('created_at', desc=True).limit(1).execute()
            if r.data:
                return r.data[0]
        names = [n for n in dict.fromkeys(n.strip() for n in names if n and n.strip())]
        for n in names:
            r = base().ilike('client_name', n).order('created_at', desc=True).limit(1).execute()
            if r.data:
                return r.data[0]
        if names:
            r = base().order('created_at', desc=True).limit(200).execute()
            for t in r.data or []:
                if any(_names_match(n, t.get('client_name')) for n in names):
                    return t
        return None
    except DedupLookupError:
        raise
    except Exception as e:
        raise DedupLookupError(f'task lookup failed: {type(e).__name__}: {e}')


def find_existing_for_contact(supabase, user_id, name='', phone='', email='', pr_get=None):
    """Resolve the PipeReply contact first, then look for a pending task by id / phone / name.

    Returns (task_or_None, contact_or_None). Raises DedupLookupError (fail closed).
    """
    contact = resolve_pr_contact(name, phone, email, pr_get=pr_get)
    cid = (contact or {}).get('id') or ''
    task = find_pending_dsw_task(
        supabase, user_id, cid=cid,
        phone=phone or (contact or {}).get('phone') or '',
        names=[name, (contact or {}).get('contactName')],
    )
    # A phone on the PipeReply record can differ from the one in the email: check it too.
    if not task and contact and contact.get('phone') and phone and phone_digits(contact['phone']) != phone_digits(phone):
        task = find_pending_dsw_task(supabase, user_id, phone=contact['phone'])
    return task, contact


def crm_url_for(contact):
    cid = (contact or {}).get('id')
    return CRM_CONTACT_URL + cid if cid else ''
