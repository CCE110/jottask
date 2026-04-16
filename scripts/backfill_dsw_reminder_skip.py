#!/usr/bin/env python3
"""One-off backfill run before the 24h/3d DSW reminder loop went live.

Stamps reminder_sent_at on every pending DSW Solar task created before
today (AEST) with a value of created_at + 4 days. That offset always
satisfies the scheduler's "3d reminder already fired" check
((rem_at - created_at) >= 72h), so check_and_send_dsw_reminders() puts
these tasks straight into the `done` bucket and never emails them.

Overwrites reminder_sent_at on every matching row — including rows the
scheduler had already stamped with `now`. Those rows would otherwise
still fire a 3d reminder later (because (now - created_at) < 72h),
which defeats the purpose.
"""
import os
from datetime import datetime, timedelta
from dateutil import parser as dp
import pytz
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

aest = pytz.timezone('Australia/Brisbane')
today_start_utc = datetime.now(aest).replace(
    hour=0, minute=0, second=0, microsecond=0
).astimezone(pytz.UTC).isoformat()

result = sb.table('tasks')\
    .select('id, client_name, lead_status, created_at, reminder_sent_at')\
    .eq('status', 'pending')\
    .eq('category', 'DSW Solar')\
    .lt('created_at', today_start_utc)\
    .execute()
tasks = result.data or []

print(f"Cutoff (UTC):  {today_start_utc}")
print(f"Candidates:    {len(tasks)}")

updated = 0
skipped_already_done = 0
for t in tasks:
    created = dp.isoparse(t['created_at'])
    existing = t.get('reminder_sent_at')
    if existing:
        existing_dt = dp.isoparse(existing)
        if (existing_dt - created) >= timedelta(hours=72):
            skipped_already_done += 1
            continue  # already in the "done" bucket
    new_rem = (created + timedelta(days=4)).isoformat()
    sb.table('tasks').update({'reminder_sent_at': new_rem}).eq('id', t['id']).execute()
    updated += 1
    print(f"  {t['id'][:8]}  {t.get('lead_status','?'):18s}  "
          f"{(t.get('client_name') or '')[:35]:35s}  "
          f"created={t['created_at'][:10]}  stamped={new_rem[:10]}")

print(f"\nStamped {updated} task(s); {skipped_already_done} already in done bucket.")
