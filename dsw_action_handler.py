#!/usr/bin/env python3
"""
DSW Action Handler - handles task_id action button clicks from lead emails
Runs as a Flask route addition or standalone.
Actions: complete, delay_1hour, delay_1day, delay_next_day_8am, delay_next_day_9am,
         delay_next_monday_9am, set_status
"""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

def handle_action(action, task_id, status=None, lost_reason=None):
    from supabase import create_client
    sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

    now = datetime.now()
    update = {}

    if action == 'complete':
        update = {'status': 'completed'}

    elif action == 'delay_1hour':
        # Get current due time and add 1 hour
        t = sb.table('tasks').select('due_date,due_time').eq('id', task_id).execute()
        if t.data:
            due_str = t.data[0]['due_date'] + ' ' + (t.data[0]['due_time'] or '09:00')
            due_dt = datetime.strptime(due_str, '%Y-%m-%d %H:%M')
            new_dt = due_dt + timedelta(hours=1)
            update = {'due_date': new_dt.strftime('%Y-%m-%d'), 'due_time': new_dt.strftime('%H:%M')}

    elif action == 'delay_1day':
        t = sb.table('tasks').select('due_date,due_time').eq('id', task_id).execute()
        if t.data:
            due_dt = datetime.strptime(t.data[0]['due_date'], '%Y-%m-%d')
            new_dt = due_dt + timedelta(days=1)
            update = {'due_date': new_dt.strftime('%Y-%m-%d')}

    elif action == 'delay_next_day_8am':
        tomorrow = (now + timedelta(days=1)).strftime('%Y-%m-%d')
        update = {'due_date': tomorrow, 'due_time': '08:00'}

    elif action == 'delay_next_day_9am':
        tomorrow = (now + timedelta(days=1)).strftime('%Y-%m-%d')
        update = {'due_date': tomorrow, 'due_time': '09:00'}

    elif action == 'delay_next_monday_9am':
        days_ahead = 7 - now.weekday()
        if days_ahead == 0: days_ahead = 7
        monday = (now + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        update = {'due_date': monday, 'due_time': '09:00'}

    elif action == 'set_status':
        update = {'lead_status': status}
        if status == 'lost' and lost_reason:
            update['lost_reason'] = lost_reason
        if status in ['won', 'lost']:
            update['status'] = 'completed'

    if update and task_id:
        result = sb.table('tasks').update(update).eq('id', task_id).execute()
        print(f"Action {action} -> task {task_id[:8]}: {update}")
        return True, update
    return False, {}


if __name__ == '__main__':
    # Test
    import sys
    if len(sys.argv) >= 3:
        ok, u = handle_action(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        print("OK:", ok, u)
