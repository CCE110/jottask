#!/usr/bin/env python3
"""
Create a manual DSW Solar lead — for the Home Show or any at-the-booth
scenario where Rob captures a lead on paper and needs to enter it into
Jottask with an immediate call reminder.

Defaults are tuned for that flow:
  - lead_status='intro_call' (NOT new_lead) — routes into the standard
    reminder path (saas_scheduler.check_and_send_reminders). new_lead +
    DSW Solar is skipped by that path per line 606-608, and
    check_and_send_dsw_reminders only fires at 24h+3d — so a same-day-due
    reminder would silently miss. This default fixes the trap that bit
    Dorothy Dawid and Joe Cincotta.
  - assignedTo=ROB_UID on the PipeReply contact so the dsw_poll won't
    treat it as company-pool.
  - category='DSW Solar', priority='high', status='pending'
  - OpenSolar project auto-created with structured address (STC-zone
    lookup works via the parse-before-send fix from f8260f1).

Pre-flight abort if a Jottask task OR PipeReply contact already matches
the name / address — never duplicates.

Usage:

  python3 scripts/create_manual_dsw_lead.py \\
      --name "Dorothy Dawid" \\
      --address "15 Baguette St, Carina QLD 4152" \\
      --note "6.6kW solar, NO battery. Wants quote. Hot water timer." \\
      --due-time "08:00"

  # Optional:
  --due-date  "today" | "tomorrow" | "YYYY-MM-DD"   default: today
  --phone     "+61..."                              default: empty
  --email     "..."                                 default: empty
  --title     "Custom task title"                   default: auto
  --source    "Home Show"                           default: Home Show
  --dry-run                                         no writes, print plan
"""

from __future__ import annotations

import os
import re
import sys
import argparse
from datetime import datetime, date, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Env — needs SUPABASE_URL + SUPABASE_KEY (service_role) + PIPEREPLY_TOKEN.
# .env fills them. If Supabase calls return RLS errors, your local .env's
# SUPABASE_KEY is likely stale — export the correct service_role key
# inline before running:  SUPABASE_KEY=<key> python3 scripts/create_manual_dsw_lead.py …
from dotenv import load_dotenv
load_dotenv(os.path.join(REPO, '.env'))

if not os.getenv('SUPABASE_URL') or not os.getenv('SUPABASE_KEY'):
    raise SystemExit(
        'SUPABASE_URL and SUPABASE_KEY must be set (via .env or env vars). '
        'PIPEREPLY_TOKEN must also be set for PR contact create + OS project.'
    )

import pytz  # noqa: E402
import requests  # noqa: E402
from supabase import create_client  # noqa: E402


# ── Constants (Rob's single-tenant identity — SaaS-refactor will change these) ──
ROB_SB_UID = 'e515407e-dbd6-4331-a815-1878815c89bc'   # Supabase user_id
ROB_PR_UID = 'zK43HKCu06NAFEbitnJW'                   # PipeReply user_id
PR_BASE    = 'https://services.leadconnectorhq.com'
LOC        = '0k6Ix1hW5QoHuUh2YSru'
CRM_BASE   = f'https://app.pipereply.com/v2/location/{LOC}/contacts'

# THE FIX — was 'new_lead' (default via DB), which routed manual same-day-due
# tasks into check_and_send_dsw_reminders (24h+3d cadence) instead of the
# standard reminder loop. Now defaults to 'intro_call' — accurate label for
# a fresh lead Rob needs to call, AND routes into the standard path.
DEFAULT_LEAD_STATUS = 'intro_call'


def _parse_due_date(s: str) -> str:
    """'today' / 'tomorrow' / 'YYYY-MM-DD' → ISO date string in AEST."""
    aest = pytz.timezone('Australia/Brisbane')
    today_aest = datetime.now(aest).date()
    s = (s or 'today').strip().lower()
    if s == 'today':
        return today_aest.isoformat()
    if s == 'tomorrow':
        return (today_aest + timedelta(days=1)).isoformat()
    # Explicit date
    try:
        return datetime.strptime(s, '%Y-%m-%d').date().isoformat()
    except ValueError:
        raise SystemExit(f'--due-date {s!r} — use "today", "tomorrow", or "YYYY-MM-DD"')


def _split_address(address: str):
    """Split "15 Baguette St, Carina QLD 4152" → (street, city, state, postcode).

    Returns (street, city, state, postcode). Any component that can't be
    parsed comes back as ''. Rob can then pass explicit --city / --state /
    --postcode overrides to fill gaps.

    Handles common patterns:
      "15 Baguette St, Carina QLD 4152"
      "15 Baguette St, Carina, QLD, 4152"
      "15 Baguette Street Carina QLD 4152"
    """
    a = (address or '').strip().rstrip(',')
    # Try: capture 4-digit postcode + preceding AU state, then split off
    m = re.search(r'\b([A-Z]{2,3})\s+(\d{4})\b\s*$', a)
    if not m:
        # Fallback: trailing 4-digit only
        m2 = re.search(r'\b(\d{4})\b\s*$', a)
        if m2:
            postcode = m2.group(1)
            head = a[:m2.start()].rstrip(', ').strip()
            state = ''
        else:
            return (a, '', '', '')
    else:
        state = m.group(1).upper()
        postcode = m.group(2)
        head = a[:m.start()].rstrip(', ').strip()
    # `head` should now be "15 Baguette St, Carina" or "15 Baguette St Carina"
    if ',' in head:
        parts = [p.strip() for p in head.split(',') if p.strip()]
        street = parts[0] if parts else ''
        city   = parts[-1] if len(parts) > 1 else ''
    else:
        # No comma — take last space-separated token(s) as city (single word only)
        tokens = head.split()
        if len(tokens) >= 2:
            street = ' '.join(tokens[:-1])
            city   = tokens[-1]
        else:
            street = head
            city   = ''
    return (street, city, state, postcode)


def _pr_headers():
    return {
        'Authorization': f'Bearer {os.getenv("PIPEREPLY_TOKEN")}',
        'Content-Type':  'application/json',
        'Version':       '2021-07-28',
    }


def main():
    ap = argparse.ArgumentParser(description='Manual DSW Solar lead insert.')
    ap.add_argument('--name',     required=True, help='"Firstname Lastname"')
    ap.add_argument('--address',  required=True, help='Full address string, parsed into components')
    ap.add_argument('--note',     required=True, help='Task note body (customer requirement / context)')
    ap.add_argument('--due-time', default='08:00', help='HH:MM AEST (default 08:00)')
    ap.add_argument('--due-date', default='today', help='today | tomorrow | YYYY-MM-DD (default today)')
    ap.add_argument('--phone',    default='',     help='Phone (E.164 preferred)')
    ap.add_argument('--email',    default='',     help='Email')
    ap.add_argument('--title',    default='',     help='Task title override (default auto)')
    ap.add_argument('--source',   default='Home Show', help='Source badge text (default: Home Show)')
    ap.add_argument('--city',     default='',     help='Override parsed city')
    ap.add_argument('--state',    default='',     help='Override parsed state (QLD / NSW / VIC etc)')
    ap.add_argument('--postcode', default='',     help='Override parsed postcode')
    ap.add_argument('--dry-run',  action='store_true', default=False,
                    help='No writes — print the plan')
    args = ap.parse_args()

    name  = args.name.strip()
    if not name or ' ' not in name:
        raise SystemExit('--name must be "Firstname Lastname"')
    first, *rest = name.split()
    last  = ' '.join(rest)
    due_date = _parse_due_date(args.due_date)
    due_time = args.due_time if len(args.due_time) == 8 else f'{args.due_time}:00'

    # Address parsing (with explicit overrides winning)
    p_street, p_city, p_state, p_postcode = _split_address(args.address)
    street   = p_street
    city     = args.city     or p_city
    state    = args.state    or p_state
    postcode = args.postcode or p_postcode

    aest = pytz.timezone('Australia/Brisbane')
    now_aest = datetime.now(aest)

    print('═' * 78)
    print(f' Manual DSW Solar lead insert')
    print(f' Mode: {"DRY-RUN" if args.dry_run else "LIVE"}')
    print(f' Now AEST:      {now_aest.isoformat(timespec="seconds")}')
    print(f' Target due:    {due_date} @ {due_time} AEST')
    print(f' lead_status:   {DEFAULT_LEAD_STATUS!r}  ← routes into standard reminder path')
    print('═' * 78)

    print(f'\n── Parsed lead ──')
    print(f'  name:     {name!r}    (first={first!r}  last={last!r})')
    print(f'  phone:    {args.phone!r}')
    print(f'  email:    {args.email!r}')
    print(f'  address:  {args.address!r}')
    print(f'            → street={street!r}  city={city!r}  state={state!r}  postcode={postcode!r}')
    print(f'  source:   {args.source!r}')
    print(f'  note:     {args.note!r}')

    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

    # ── 1. Pre-flight abort — existing? ──
    print(f'\n── 1. Pre-flight — check for existing task ──')
    tasks_by_name = sb.table('tasks').select('id, client_name, status, created_at')\
          .ilike('client_name', f'%{last}%').limit(5).execute().data or []
    dup = [t for t in tasks_by_name if name.lower() in (t.get('client_name','') or '').lower()]
    print(f'  matching name: {len(dup)}')
    for t in dup:
        print(f'    ⚠ {t["id"][:8]}  {t["created_at"][:19]}  {t["client_name"]!r}  status={t.get("status")}')
    if dup:
        raise SystemExit('Existing task found — refusing to create duplicate. '
                         'Update by hand or cancel the old one first.')

    tasks_by_addr = sb.table('tasks').select('id, client_name, status, created_at')\
              .ilike('description', f'%{street}%').execute().data or []
    if tasks_by_addr:
        print(f'  matching address "{street}": {len(tasks_by_addr)}')
        for t in tasks_by_addr:
            print(f'    ⚠ {t["id"][:8]}  {t["created_at"][:19]}  {t["client_name"]!r}  status={t.get("status")}')
        raise SystemExit('Existing task at this address — refusing to create duplicate.')

    # PipeReply search (informational — we reuse if found)
    print(f'\n── PipeReply search ──')
    existing_pr = None
    for q in (name, last, street):
        try:
            resp = requests.get(f'{PR_BASE}/contacts/', headers=_pr_headers(),
                                params={'locationId': LOC, 'query': q, 'limit': 5}, timeout=15)
            if not resp.ok:
                continue
            for c in resp.json().get('contacts', []):
                nm = (c.get('contactName') or f"{c.get('firstName','')} {c.get('lastName','')}").lower()
                if last.lower() in nm or street.lower() in (c.get('address1','') or '').lower():
                    existing_pr = c
                    print(f'  match: cid={c.get("id","")[:16]}  {nm!r}  addr={c.get("address1")!r}  assignedTo={c.get("assignedTo")!r}')
                    break
            if existing_pr:
                break
        except Exception:
            continue
    if existing_pr:
        print(f'  → will REUSE this cid, not create a new PR contact')
    else:
        print(f'  no match → will create fresh PR contact')

    # ── 2. Find or create PR contact ──
    print(f'\n── 2. Find/create PipeReply contact ──')
    if args.dry_run:
        cid, is_new = 'DRY_RUN_CID', True
        print(f'  DRY-RUN: would call find_or_create_pipereply_contact(...)')
    else:
        import dsw_lead_poller as dsw
        cid, is_new = dsw.find_or_create_pipereply_contact(
            name=name, phone=args.phone, email=args.email,
            address=args.address, src='referral',
        )
    print(f'  cid={cid}   is_new={is_new}')
    if not cid:
        raise SystemExit('No cid returned — aborting.')

    # ── 3. Patch assignedTo → Rob ──
    print(f'\n── 3. Patch assignedTo → ROB_UID ──')
    if args.dry_run:
        print(f'  DRY-RUN: would PATCH /contacts/{cid} assignedTo={ROB_PR_UID}')
    else:
        r = requests.put(f'{PR_BASE}/contacts/{cid}', headers=_pr_headers(),
                         json={'assignedTo': ROB_PR_UID}, timeout=15)
        print(f'  {"✓" if r.ok else "⚠"} HTTP {r.status_code}')

    # ── 4. OpenSolar project ──
    print(f'\n── 4. OpenSolar project (structured address → STC-zone lookup) ──')
    if args.dry_run:
        os_pid, os_url = 'DRY_PID', 'https://app.opensolar.com/#/projects/DRY_PID/info'
    else:
        try:
            import dsw_lead_poller as dsw
            os_pid, os_url = dsw.make_opensolar(
                name=name, phone=args.phone, email=args.email,
                address=street, city=city, state=state, postcode=postcode,
                first_name=first, last_name=last,
            )
        except Exception as e:
            print(f'  ⚠ make_opensolar raised: {e}')
            os_pid, os_url = None, None
    if os_url:
        print(f'  ✓ OS project: id={os_pid}  url={os_url}')
    else:
        print(f'  ⚠ OS project NOT created — task will show OpenSolar: pending')

    # ── 5. Direct task INSERT ──
    print(f'\n── 5. Direct task INSERT — lead_status={DEFAULT_LEAD_STATUS!r} (THE FIX) ──')
    crm_url = f'{CRM_BASE}/detail/{cid}'
    title   = args.title or f'Call {name} — {args.source} lead'
    desc = (
        f"Phone: {args.phone or '(none)'}\n"
        f"Email: {args.email or '(none)'}\n"
        f"Address: {args.address}\n"
        f"Source: 🎪 {args.source}\n"
        f"CRM: {crm_url}\n"
        f"OpenSolar: {os_url or 'pending'}\n\n"
        f"CUSTOMER REQUIREMENTS\n"
        f"* {args.note}\n"
    )
    task_data = {
        'user_id':      ROB_SB_UID,
        'title':        title,
        'description':  desc,
        'client_name':  name,
        'client_phone': args.phone or None,
        'client_email': args.email or None,
        'category':     'DSW Solar',
        'status':       'pending',
        'priority':     'high',
        'due_date':     due_date,
        'due_time':     due_time,
        'lead_status':  DEFAULT_LEAD_STATUS,   # ← the fix
    }

    if args.dry_run:
        print(f'  DRY-RUN: task_data =')
        for k in sorted(task_data.keys()):
            v = task_data[k]
            vs = repr(v) if not isinstance(v, str) or len(v) < 80 else repr(v[:80] + '...')
            print(f'    {k:15} = {vs}')
        # Verify the scheduler filter would NOT skip this task
        is_dsw_newlead = (task_data['category'] == 'DSW Solar' and
                          (task_data['lead_status'] or 'new_lead') == 'new_lead')
        print(f'\n  scheduler DSW-new_lead-skip check: '
              f'{"SKIPPED ✗" if is_dsw_newlead else "PROCEEDS ✓"}')
        print(f'  → standard reminder would fire at '
              f'due_time - user.reminder_minutes_before')
        return

    resp = sb.table('tasks').insert(task_data).execute()
    if not (resp.data and resp.data[0]):
        raise SystemExit('INSERT returned no data')
    created = resp.data[0]
    tid = created['id']
    print(f'  ✓ task {tid[:8]}   full id={tid}')

    # ── 6. Task note ──
    n = sb.table('task_notes').insert({
        'task_id': tid,
        'content': args.note,
        'created_by': 'system',
        'source': 'manual',
    }).execute()
    print(f'  ✓ task_note attached: {n.data[0]["id"][:8]}' if n.data else '  ⚠ note insert failed')

    # ── 7. Reminder timing summary ──
    users = sb.table('users').select('reminder_minutes_before, timezone')\
              .eq('id', ROB_SB_UID).execute().data[0]
    minutes_before = users.get('reminder_minutes_before') or 30
    user_tz = pytz.timezone(users.get('timezone') or 'Australia/Brisbane')
    due_dt = user_tz.localize(datetime.combine(
        datetime.strptime(due_date, '%Y-%m-%d').date(),
        datetime.strptime(due_time, '%H:%M:%S').time()))
    reminder_at = due_dt - timedelta(minutes=minutes_before)
    now_utc = datetime.now(pytz.UTC)
    minutes_until = int((reminder_at.astimezone(pytz.UTC) - now_utc).total_seconds() / 60)

    print(f'\n' + '═' * 78)
    print(f' ✓ {name} task created — lead_status={DEFAULT_LEAD_STATUS!r}')
    print(f'   Task URL:  https://www.jottask.app/task/{tid}')
    print(f'   PR CRM:    {crm_url}')
    print(f'   OpenSolar: {os_url or "(pending)"}')
    print(f'   Due:       {due_date} {due_time} AEST')
    if minutes_until >= 0:
        print(f'   Reminder:  {reminder_at.strftime("%A %d %b at %H:%M AEST")} '
              f'(fires in {minutes_until}min)')
    else:
        print(f'   Reminder:  {reminder_at.strftime("%A %d %b at %H:%M AEST")} '
              f'(OVERDUE by {-minutes_until}min — will fire on next scheduler tick)')
    print('═' * 78)


if __name__ == '__main__':
    main()
