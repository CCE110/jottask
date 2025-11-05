#!/usr/bin/env python3
from flask import Flask, request, render_template_string
from task_manager import TaskManager
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)
tm = TaskManager()

SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Task Action Complete</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
        }
        h1 { color: #1f2937; margin-bottom: 10px; }
        p { color: #6b7280; line-height: 1.6; }
        .icon { font-size: 64px; margin-bottom: 20px; }
        .task-info {
            background: #f9fafb;
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
            color: #1f2937;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">{{ icon }}</div>
        <h1>{{ title }}</h1>
        <p>{{ message }}</p>
        {% if task_title %}
        <div class="task-info">
            <strong>Task:</strong> {{ task_title }}
        </div>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route('/action', methods=['GET'])
def handle_action():
    action = request.args.get('action')
    task_id = request.args.get('task_id')
    
    if not action or not task_id:
        return "Missing action or task_id parameter", 400
    
    try:
        # Get task details
        task_result = tm.supabase.table('tasks').select('*').eq('id', task_id).execute()
        if not task_result.data:
            return "Task not found", 404
        
        task = task_result.data[0]
        task_title = task['title']
        
            aest = pytz.timezone('Australia/Brisbane')
    now = datetime.now(aest)
    
    if action == 'delay_1hour':
        task = tm.supabase.table('tasks').select('*').eq('id', task_id).single().execute()
        current_time = task.data.get('due_time', '08:00:00')
        current_date = task.data.get('due_date')
        hour, minute = map(int, current_time.split(':')[:2])
        task_dt = datetime.combine(datetime.fromisoformat(current_date).date(), datetime.min.time().replace(hour=hour, minute=minute))
        new_dt = aest.localize(task_dt) + timedelta(hours=1)
        tm.supabase.table('tasks').update({'due_date': new_dt.date().isoformat(), 'due_time': new_dt.strftime('%H:%M:%S')}).eq('id', task_id).execute()
        return '<html><body style="padding: 40px; text-align: center;"><h1>⏰ Delayed 1 Hour</h1></body></html>'
    elif action == 'delay_1day':
        task = tm.supabase.table('tasks').select('*').eq('id', task_id).single().execute()
        current_date = task.data.get('due_date')
        new_date = (datetime.fromisoformat(current_date) + timedelta(days=1)).date().isoformat()
        tm.supabase.table('tasks').update({'due_date': new_date}).eq('id', task_id).execute()
        return '<html><body style="padding: 40px; text-align: center;"><h1>📅 Delayed 1 Day</h1></body></html>'
    elif action == 'delay_custom':
        return '<html><body style="padding: 20px;"><h2>Custom Delay</h2><form method="POST" action="/action"><input type="hidden" name="action" value="save_delay"><input type="hidden" name="task_id" value="' + task_id + '"><label>Date:</label><input type="date" name="custom_date" required><label>Time:</label><input type="time" name="custom_time" value="08:00" required><button type="submit">Set</button></form></body></html>'
    elif action == 'save_delay':
        custom_date = request.form.get('custom_date')
        custom_time = request.form.get('custom_time', '08:00')
        tm.supabase.table('tasks').update({'due_date': custom_date, 'due_time': custom_time + ':00'}).eq('id', task_id).execute()
        return '<html><body style="padding: 40px; text-align: center;"><h1>✅ Reminder Set!</h1></body></html>'
    
    if action == 'complete':
            tm.supabase.table('tasks').update({
                'status': 'completed',
                'completed_at': datetime.now().isoformat()
            }).eq('id', task_id).execute()
            
            return render_template_string(SUCCESS_TEMPLATE, 
                icon='✅',
                title='Task Completed!',
                message='Great job! This task has been marked as complete.',
                task_title=task_title
            )
        
        elif action == 'postpone':
            days = int(request.args.get('days', 1))
            current_due = datetime.fromisoformat(task['due_date'])
            new_due = current_due + timedelta(days=days)
            
            tm.supabase.table('tasks').update({
                'due_date': new_due.date().isoformat()
            }).eq('id', task_id).execute()
            
            return render_template_string(SUCCESS_TEMPLATE,
                icon='📅',
                title='Task Postponed',
                message=f'This task has been rescheduled to {new_due.strftime("%B %d, %Y")}.',
                task_title=task_title
            )
        
        elif action == 'add_followup_form':
            # Simple follow-up form
            form_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Add Follow-up</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        min-height: 100vh;
                        margin: 0;
                        padding: 20px;
                    }}
                    .container {{
                        background: white;
                        border-radius: 16px;
                        padding: 40px;
                        max-width: 500px;
                        width: 100%;
                        box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    }}
                    h1 {{ color: #1f2937; text-align: center; }}
                    .form-group {{ margin-bottom: 20px; }}
                    label {{ display: block; color: #374151; font-weight: 600; margin-bottom: 8px; }}
                    input, textarea {{ width: 100%; padding: 12px; border: 2px solid #e5e7eb; border-radius: 8px; box-sizing: border-box; }}
                    button {{ width: 100%; background: #667eea; color: white; padding: 14px; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; }}
                    .task-info {{ background: #f3f4f6; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>📌 Add Follow-up</h1>
                    <div class="task-info">
                        <strong>Original Task:</strong><br>
                        {task_title}
                    </div>
                    <form method="POST" action="/action">
                        <input type="hidden" name="action" value="add_followup">
                        <input type="hidden" name="task_id" value="{task_id}">
                        
                        <div class="form-group">
                            <label for="title">Follow-up Task Title *</label>
                            <input type="text" id="title" name="title" required 
                                   placeholder="e.g., Check if proposal was approved">
                        </div>
                        
                        <div class="form-group">
                            <label for="description">Description (optional)</label>
                            <textarea id="description" name="description" 
                                      placeholder="Additional details..."></textarea>
                        </div>
                        
                        <div class="form-group">
                            <label for="due_date">Due Date *</label>
                            <input type="date" id="due_date" name="due_date" required>
                        </div>
                        
                        <button type="submit">Add Follow-up</button>
                    </form>
                </div>
                
                <script>
                    // Set default date to 3 days from now
                    const dateInput = document.getElementById('due_date');
                    const defaultDate = new Date();
                    defaultDate.setDate(defaultDate.getDate() + 3);
                    dateInput.value = defaultDate.toISOString().split('T')[0];
                </script>
            </body>
            </html>
            """
            return form_html
        
        else:
            return "Invalid action", 400
    
    except Exception as e:
        return f"Error processing action: {e}", 500

@app.route('/action', methods=['POST'])
def handle_followup_form():
    """Handle follow-up form submission"""
    action = request.form.get('action')
    task_id = request.form.get('task_id')
    
    if action == 'add_followup':
        title = request.form.get('title')
        description = request.form.get('description', '')
        due_date = request.form.get('due_date')
        
        try:
            # Add follow-up to database
            tm.supabase.table('follow_ups').insert({
                'task_id': task_id,
                'title': title,
                'description': description,
                'due_date': due_date
            }).execute()
            
            return render_template_string(SUCCESS_TEMPLATE,
                icon='📌',
                title='Follow-up Added!',
                message=f'Your follow-up has been scheduled for {due_date}.',
                task_title=title
            )
        except Exception as e:
            return f"Error adding follow-up: {e}", 500
    
    return "Invalid request", 400

@app.route('/health')
def health():
    return {"status": "healthy", "message": "Task Management Web Endpoint"}

@app.route('/')
def home():
    return "<h1>👋 Task Management System</h1><p>This endpoint handles task actions from emails.</p>"

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

# Railway requires this exact configuration
if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
