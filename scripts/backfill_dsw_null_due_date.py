#!/usr/bin/env python3
"""One-off: backfill due_date/due_time on pending DSW Solar tasks that were
created with due_date = NULL.

Null due_dates fell out of the daily-summary query (which now filters on
due_date) and out of the reminder cycle. Stamps every affected task to
now + 4 hours in AEST so it shows up in today's summary and next reminder
tick.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()
sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

aest = timezone(timedelta(hours=10))
target = datetime.now(aest) + timedelta(hours=4)
due_date = target.strftime('%Y-%m-%d')
due_time = target.strftime('%H:%M:00')

result = sb.table('tasks')\
    .select('id, client_name, title, created_at')\
    .eq('status', 'pending')\
    .eq('category', 'DSW Solar')\
    .is_('due_date', 'null')\
    .execute()
tasks = result.data or []

print(f"Target:     {due_date} {due_time} AEST")
print(f"Candidates: {len(tasks)}")

for t in tasks:
    sb.table('tasks').update({
        'due_date': due_date,
        'due_time': due_time,
    }).eq('id', t['id']).execute()
    print(f"  {t['id'][:8]}  {(t.get('client_name') or t.get('title',''))[:50]}  "
          f"created={t['created_at'][:10]}")

print(f"\nStamped {len(tasks)} task(s).")
