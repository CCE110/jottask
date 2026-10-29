"""Retry/parity for OpenSolar projects: give every real pending DSW task a geocoded project.

Covers creation paths that never make one (email Appointment Confirmation, Task Assigned handler)
and transient failures that were swallowed (appointment-poll enrich, 27 Oct Tracie Burton).

Safety:
  * only PENDING DSW Solar tasks whose description still has the line `OpenSolar: pending`
  * never SMS review stubs / signal stubs; never tasks older than MAX_AGE_DAYS (force_ids overrides, by hand)
  * the PipeReply contact must still be assigned to Rob and have a street address (nothing to geocode otherwise)
  * at most LIMIT creates per run, MAX_ATTEMPTS tries per task (tasks.metadata.os_heal_attempts)
  * if OpenSolar answers with an EXISTING project that another task already uses (repeat customer, new job),
    nothing is linked; it is reported instead
  * run with dry_run=True first: it only reports what it would do
"""
import re
from datetime import datetime, timedelta, timezone

import dsw_contact_dedup as dd

MAX_AGE_DAYS = 14
MIN_AGE_MIN = 15          # give the normal creation path time to finish first
LIMIT = 3
MAX_ATTEMPTS = 3
ROB_UID = 'zK43HKCu06NAFEbitnJW'
PENDING_LINE = re.compile(r'^OpenSolar:[ \t]*pending[ \t]*$', re.M)


def find_candidates(sb, user_id, now=None, force_ids=()):
    now = now or datetime.now(timezone.utc)
    rows = (sb.table('tasks').select('id, client_name, title, description, created_at, metadata')
            .eq('user_id', user_id).eq('category', 'DSW Solar').eq('status', 'pending').execute().data) or []
    out, skipped = [], []
    for t in rows:
        d = t.get('description') or ''
        if not PENDING_LINE.search(d):
            continue
        tid = t['id']
        forced = tid in force_ids or any(tid.startswith(f) for f in force_ids)
        why = None
        if (t.get('title') or '').startswith('Review DSW SMS') or dd.is_stub(t):
            why = 'review/signal stub - not a lead'
        elif not dd.cid_from_text(d):
            why = 'no PipeReply contact link'
        else:
            age = now - datetime.fromisoformat(t['created_at'].replace('Z', '+00:00'))
            if age < timedelta(minutes=MIN_AGE_MIN):
                why = 'too new - normal path may still be creating it'
            elif age > timedelta(days=MAX_AGE_DAYS) and not forced:
                why = f'stale ({age.days}d old) - only with force'
            elif ((t.get('metadata') or {}).get('os_heal_attempts') or 0) >= MAX_ATTEMPTS and not forced:
                why = f'gave up after {MAX_ATTEMPTS} attempts'
        (skipped if why else out).append((t, why) if why else t)
    return out, skipped


def _used_elsewhere(sb, pid, this_id):
    r = sb.table('tasks').select('id').eq('category', 'DSW Solar').ilike('description', f'%/projects/{pid}/%').execute().data or []
    return [x['id'] for x in r if x['id'] != this_id]


def heal(sb, user_id, dsw, dry_run=True, force_ids=(), limit=LIMIT, now=None, log=print):
    """-> list of result dicts. `dsw` is the dsw_lead_poller module (injected for tests)."""
    cands, skipped = find_candidates(sb, user_id, now=now, force_ids=force_ids)
    results = [{'task_id': t['id'], 'name': t.get('client_name'), 'action': 'skip', 'reason': why} for t, why in skipped]
    for t in cands[:limit]:
        tid, d = t['id'], t['description']
        cid = dd.cid_from_text(d)
        r = {'task_id': tid, 'name': t.get('client_name'), 'cid': cid}
        c = dsw.get_full(cid) or {}
        if c.get('assignedTo') != ROB_UID:
            results.append({**r, 'action': 'skip', 'reason': f"contact assignedTo={c.get('assignedTo')!r}, not Rob"})
            continue
        street = (c.get('address1') or '').strip()
        if not street:
            results.append({**r, 'action': 'skip', 'reason': 'contact has no street address to geocode'})
            continue
        name = (c.get('contactName') or f"{c.get('firstName') or ''} {c.get('lastName') or ''}".strip()
                or t.get('client_name') or 'Unknown')
        r.update(address=f"{street}, {c.get('city') or ''} {c.get('state') or ''} {c.get('postalCode') or ''}".strip(),
                 os_name=name)
        if dry_run:
            results.append({**r, 'action': 'would_create'})
            continue
        attempts = ((t.get('metadata') or {}).get('os_heal_attempts') or 0) + 1
        try:
            pid, url = dsw.make_opensolar(name, c.get('phone') or '', c.get('email') or '', street, c.get('city') or '',
                                          c.get('state') or '', c.get('postalCode') or '',
                                          first_name=c.get('firstName'), last_name=c.get('lastName'))
        except Exception as e:
            pid, url = None, None
            log(f'[os_heal] {tid[:8]} make_opensolar error: {e}')
        if not url:
            sb.table('tasks').update({'metadata': {**(t.get('metadata') or {}), 'os_heal_attempts': attempts}}).eq('id', tid).execute()
            results.append({**r, 'action': 'failed', 'reason': f'make_opensolar returned nothing (attempt {attempts})'})
            continue
        clash = _used_elsewhere(sb, pid, tid)
        if clash:
            results.append({**r, 'action': 'skip', 'reason': f'OpenSolar returned existing project {pid} already used by task(s) {[x[:8] for x in clash]} - not linked'})
            continue
        new = PENDING_LINE.sub(f'OpenSolar: {url}', d, count=1)
        sb.table('tasks').update({'description': new}).eq('id', tid).execute()
        try:
            if not dsw.get_os_url_from_crm(cid):          # reminder email's OpenSolar button reads this CRM note
                dsw.save_to_crm(cid, url, f'Project created for {r["address"]}.', overwrite=False)
        except Exception as e:
            log(f'[os_heal] {tid[:8]} CRM note failed (task already linked): {e}')
        results.append({**r, 'action': 'created', 'os_url': url})
    for t in cands[limit:]:
        results.append({'task_id': t['id'], 'name': t.get('client_name'), 'action': 'deferred', 'reason': f'over per-run limit {limit}'})
    return results
