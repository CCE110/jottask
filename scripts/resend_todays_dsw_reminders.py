#!/usr/bin/env python3
"""One-off cleanup after the initial DSW reminder blast.

1. Stamps reminder_sent_at on every pending DSW Solar task so the
   scheduler's check_and_send_dsw_reminders() treats all of them as
   already done (skipped forever). Uses created_at + 4 days so the
   offset always clears the 72h "done" threshold.

2. Queries pending DSW Solar tasks with due_date = today (AEST) and
   lead_status = 'new_lead' — the only leads that are actionable
   today.

3. Calls send_dsw_reminder_for_task(task, '24h') for each, giving
   Rob one clean REMINDER email per actionable lead. No further
   reminders will fire afterwards because step 1 already stamped
   every task into the done bucket.
"""
import os
import sys
from datetime import datetime, timedelta
from dateutil import parser as dp
import pytz
from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()
sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

aest = pytz.timezone('Australia/Brisbane')
today_aest = datetime.now(aest).date().isoformat()

# ---- Step 1: stamp every pending DSW Solar task into the done bucket ----
print("=" * 60)
print("Step 1: stamping every pending DSW Solar task to done bucket")
print("=" * 60)

all_result = sb.table('tasks')\
    .select('id, client_name, created_at, reminder_sent_at')\
    .eq('status', 'pending')\
    .eq('category', 'DSW Solar')\
    .execute()
all_tasks = all_result.data or []
print(f"Found {len(all_tasks)} pending DSW Solar task(s)")

stamped = 0
already = 0
for t in all_tasks:
    created = dp.isoparse(t['created_at'])
    existing = t.get('reminder_sent_at')
    if existing:
        if (dp.isoparse(existing) - created) >= timedelta(hours=72):
            already += 1
            continue
    new_rem = (created + timedelta(days=4)).isoformat()
    sb.table('tasks').update({'reminder_sent_at': new_rem}).eq('id', t['id']).execute()
    stamped += 1

print(f"Stamped {stamped}; {already} already in done bucket.\n")

# ---- Step 2: query today-due new_lead DSW tasks ----
print("=" * 60)
print(f"Step 2: pending DSW Solar tasks due today ({today_aest} AEST) with lead_status='new_lead'")
print("=" * 60)

today_result = sb.table('tasks')\
    .select('id, title, description, client_name, lead_status, due_date, due_time, created_at')\
    .eq('status', 'pending')\
    .eq('category', 'DSW Solar')\
    .eq('lead_status', 'new_lead')\
    .eq('due_date', today_aest)\
    .order('due_time')\
    .execute()
today_tasks = today_result.data or []
print(f"Found {len(today_tasks)} task(s):")
for t in today_tasks:
    print(f"  - {t.get('due_time') or '(no time)':9s}  {t.get('client_name') or '(no name)':30s}  {t['title'][:60]}")
print()

# ---- Step 3: resend REMINDER (24h) for each ----
print("=" * 60)
print(f"Step 3: sending REMINDER (24h) for {len(today_tasks)} lead(s)")
print("=" * 60)

from dsw_lead_poller import send_dsw_reminder_for_task
import time

for t in today_tasks:
    print(f"  → {t.get('client_name') or t['title'][:40]}")
    send_dsw_reminder_for_task(t, '24h')
    time.sleep(0.3)

print(f"\nDone. {len(today_tasks)} reminder email(s) sent.")
