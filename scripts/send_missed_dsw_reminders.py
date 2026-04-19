#!/usr/bin/env python3
"""One-off catch-up: send reminders for pending DSW Solar tasks that fell
through the reminder-system gap while the general loop was excluding the
DSW Solar category.

Criteria:
  - status = 'pending'
  - category = 'DSW Solar'
  - due_date <= today (AEST)
  - lead_status NOT IN (null, 'new_lead')   — new_lead is owned by the
    DSW 24h/3d loop; everything else is what we lost.
  - reminder_sent_at IS NULL OR more than 1 hour ago

Each match gets a DSW-formatted reminder via send_dsw_reminder_for_task
and reminder_sent_at is stamped after the send.
"""
import os
import sys
import time
from datetime import datetime, timedelta
from dateutil import parser as dp
import pytz
from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from dsw_lead_poller import send_dsw_reminder_for_task

sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

aest = pytz.timezone('Australia/Brisbane')
today_iso = datetime.now(aest).date().isoformat()
now_utc = datetime.now(pytz.UTC)
one_hour_ago = now_utc - timedelta(hours=1)

# Step 1: fetch candidates (DB-side filters). Finish filtering in Python
# because Supabase-py OR expressions are awkward for "null OR < timestamp".
result = sb.table('tasks')\
    .select('id, title, description, client_name, lead_status, due_date, due_time, reminder_sent_at, status')\
    .eq('status', 'pending')\
    .eq('category', 'DSW Solar')\
    .lte('due_date', today_iso)\
    .execute()
raw = result.data or []

eligible = []
for t in raw:
    ls = t.get('lead_status')
    if not ls or ls == 'new_lead':
        continue  # owned by check_and_send_dsw_reminders
    rem = t.get('reminder_sent_at')
    if rem is None:
        eligible.append(t)
        continue
    try:
        if dp.isoparse(rem) < one_hour_ago:
            eligible.append(t)
    except Exception:
        eligible.append(t)  # unparseable — treat as stale

print(f"Candidates with due_date <= {today_iso}: {len(raw)}")
print(f"Eligible after lead_status + 1h-throttle filters: {len(eligible)}")
print()

sent = 0
for t in eligible:
    try:
        print(f"  → {t.get('client_name') or t['title'][:40]}  "
              f"lead_status={t.get('lead_status')}  due={t['due_date']} {t.get('due_time') or ''}")
        send_dsw_reminder_for_task(t, 'overdue')
        sb.table('tasks').update({
            'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
        }).eq('id', t['id']).execute()
        sent += 1
        time.sleep(0.3)
    except Exception as e:
        print(f"    ! error: {e}")

print(f"\nSent {sent} reminder(s).")
