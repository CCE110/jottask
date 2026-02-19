from flask import Flask, request
from task_manager import TaskManager
from datetime import datetime, timedelta
import pytz
import os

app = Flask(__name__)
tm = TaskManager()

@app.route('/')
def home():
    return '<h1>Task Management System</h1><p>This endpoint handles task actions from emails.</p>'

@app.route('/action', methods=['GET', 'POST'])
def handle_action():
    """Handle all task actions from email buttons"""
    action = request.args.get('action') or request.form.get('action')
    task_id = request.args.get('task_id') or request.form.get('task_id')
    
    if not action or not task_id:
        return '<html><body style="font-family: Arial; padding: 40px; text-align: center;"><h1>❌ Invalid Request</h1><p>Missing action or task_id</p></body></html>', 400
    
    aest = pytz.timezone('Australia/Brisbane')
    now = datetime.now(aest)
    
    try:
        if action == 'complete':
            # Mark task as completed
            tm.supabase.table('tasks').update({
                'status': 'completed',
                'updated_at': now.isoformat()
            }).eq('id', task_id).execute()
            return '<html><body style="font-family: Arial; padding: 40px; text-align: center;"><h1>✅ Task Completed!</h1><p>Task has been marked as complete.</p></body></html>'
        
        elif action == 'delay_1hour':
            # Get current task
            task = tm.supabase.table('tasks').select('*').eq('id', task_id).single().execute()
            current_date = task.data.get('due_date')
            current_time = task.data.get('due_time', '08:00:00')
            
            # Parse current due datetime
            due_datetime = datetime.fromisoformat(f"{current_date} {current_time}")
            
            # Add 1 hour
            new_datetime = due_datetime + timedelta(hours=1)
            
            # Update task
            tm.supabase.table('tasks').update({
                'due_date': new_datetime.date().isoformat(),
                'due_time': new_datetime.time().isoformat(),
                'updated_at': now.isoformat()
            }).eq('id', task_id).execute()
            
            return f'<html><body style="font-family: Arial; padding: 40px; text-align: center;"><h1>⏰ Delayed 1 Hour</h1><p>Task rescheduled to {new_datetime.strftime("%I:%M %p AEST")}</p></body></html>'
        
        elif action == 'delay_1day':
            # Get current task
            task = tm.supabase.table('tasks').select('*').eq('id', task_id).single().execute()
            current_date = task.data.get('due_date')
            
            # Add 1 day
            new_date = (datetime.fromisoformat(current_date) + timedelta(days=1)).date().isoformat()
            
            # Update task
            tm.supabase.table('tasks').update({
                'due_date': new_date,
                'updated_at': now.isoformat()
            }).eq('id', task_id).execute()
            
            return '<html><body style="font-family: Arial; padding: 40px; text-align: center;"><h1>📅 Delayed 1 Day</h1><p>Task rescheduled for tomorrow at the same time.</p></body></html>'
        
        elif action == 'delay_1week':
            # Get current task
            task = tm.supabase.table('tasks').select('*').eq('id', task_id).single().execute()
            current_date = task.data.get('due_date')
            
            # Add 7 days
            new_date = (datetime.fromisoformat(current_date) + timedelta(days=7)).date().isoformat()
            
            # Update task
            tm.supabase.table('tasks').update({
                'due_date': new_date,
                'updated_at': now.isoformat()
            }).eq('id', task_id).execute()
            
            return '<html><body style="font-family: Arial; padding: 40px; text-align: center;"><h1>📅 Delayed 1 Week</h1><p>Task rescheduled for 7 days from now.</p></body></html>'
        
        elif action == 'delay_custom':
            # Show form for custom date/time
            task = tm.supabase.table('tasks').select('*').eq('id', task_id).single().execute()
            task_title = task.data.get('title', 'Task')
            
            return f'''
            <html>
            <head>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
                        padding: 40px 20px;
                        max-width: 500px;
                        margin: 0 auto;
                        background: #f5f5f5;
                    }}
                    .container {{
                        background: white;
                        padding: 30px;
                        border-radius: 8px;
                        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                    }}
                    h1 {{
                        color: #1f2937;
                        margin-top: 0;
                    }}
                    .task-title {{
                        background: #f3f4f6;
                        padding: 12px;
                        border-radius: 6px;
                        margin: 20px 0;
                        font-weight: 500;
                    }}
                    label {{
                        display: block;
                        margin-top: 20px;
                        margin-bottom: 8px;
                        font-weight: 500;
                        color: #374151;
                    }}
                    input {{
                        width: 100%;
                        padding: 12px;
                        border: 1px solid #d1d5db;
                        border-radius: 6px;
                        font-size: 16px;
                        box-sizing: border-box;
                    }}
                    button {{
                        width: 100%;
                        padding: 14px;
                        margin-top: 24px;
                        background: #3b82f6;
                        color: white;
                        border: none;
                        border-radius: 6px;
                        font-size: 16px;
                        font-weight: 500;
                        cursor: pointer;
                    }}
                    button:hover {{
                        background: #2563eb;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🗓️ Reschedule Task</h1>
                    <div class="task-title">{task_title}</div>
                    <form method="POST" action="/action">
                        <input type="hidden" name="action" value="save_delay">
                        <input type="hidden" name="task_id" value="{task_id}">
                        
                        <label>New Due Date:</label>
                        <input type="date" name="new_date" required>
                        
                        <label>New Time:</label>
                        <input type="time" name="new_time" required>
                        
                        <button type="submit">Set New Reminder</button>
                    </form>
                </div>
            </body>
            </html>
            '''
        
        elif action == 'save_delay':
            # Save custom delay from form
            new_date = request.form.get('new_date')
            new_time = request.form.get('new_time')
            
            if not new_date or not new_time:
                return '<html><body style="font-family: Arial; padding: 40px; text-align: center;"><h1>❌ Error</h1><p>Missing date or time</p></body></html>', 400
            
            # Update task
            tm.supabase.table('tasks').update({
                'due_date': new_date,
                'due_time': new_time,
                'updated_at': now.isoformat()
            }).eq('id', task_id).execute()
            
            # Format display
            display_date = datetime.fromisoformat(f"{new_date} {new_time}").strftime("%B %d, %Y at %I:%M %p")
            
            return f'<html><body style="font-family: Arial; padding: 40px; text-align: center;"><h1>✅ Reminder Set!</h1><p>Task rescheduled for {display_date} AEST</p></body></html>'
        
        else:
            return '<html><body style="font-family: Arial; padding: 40px; text-align: center;"><h1>❌ Invalid Action</h1><p>Unknown action: ' + action + '</p></body></html>', 400
    
    except Exception as e:
        return f'<html><body style="font-family: Arial; padding: 40px; text-align: center;"><h1>❌ Error</h1><p>{str(e)}</p></body></html>', 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
