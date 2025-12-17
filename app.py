"""
Flask Web Service - Task Action Handlers
Updated: November 28, 2025

Handles button clicks from reminder emails:
- Complete task
- Delay task (+1 hour, +1 day, custom)
- Change project status (next/previous)
"""

import os
from datetime import datetime, timedelta
from flask import Flask, request, redirect, render_template_string
import pytz

from task_manager import TaskManager

app = Flask(__name__)
tm = TaskManager()
aest = pytz.timezone('Australia/Brisbane')


# ========================================
# HTML TEMPLATES
# ========================================

SUCCESS_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0;
            padding: 20px;
        }
        .card {
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        .icon {
            font-size: 64px;
            margin-bottom: 20px;
        }
        h1 {
            color: #111827;
            margin: 0 0 10px 0;
            font-size: 24px;
        }
        .message {
            color: #6b7280;
            font-size: 16px;
            margin-bottom: 30px;
            line-height: 1.6;
        }
        .task-title {
            background: #f3f4f6;
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
            font-weight: 600;
            color: #374151;
        }
        .status-badge {
            display: inline-block;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: 600;
            color: white;
            margin: 10px 0;
        }
        .close-btn {
            display: inline-block;
            padding: 12px 30px;
            background: #374151;
            color: white;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            transition: background 0.2s;
        }
        .close-btn:hover {
            background: #1f2937;
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{{ icon }}</div>
        <h1>{{ title }}</h1>
        <div class="task-title">{{ task_title }}</div>
        <p class="message">{{ message }}</p>
        {% if status_name %}
        <div class="status-badge" style="background: {{ status_color }};">
            {{ status_emoji }} {{ status_name }}
        </div>
        {% endif %}
        <br><br>
        <a href="javascript:window.close();" class="close-btn">Close Window</a>
    </div>
</body>
</html>"""

ERROR_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0;
            padding: 20px;
        }
        .card {
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        .icon { font-size: 64px; margin-bottom: 20px; }
        h1 { color: #111827; margin: 0 0 10px 0; }
        .message { color: #6b7280; font-size: 16px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">❌</div>
        <h1>Error</h1>
        <p class="message">{{ message }}</p>
    </div>
</body>
</html>"""

CUSTOM_DELAY_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reschedule Task</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0;
            padding: 20px;
        }
        .card {
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        h1 {
            color: #111827;
            margin: 0 0 10px 0;
            font-size: 24px;
        }
        .task-title {
            background: #f3f4f6;
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
            font-weight: 600;
            color: #374151;
        }
        label {
            display: block;
            margin: 15px 0 5px;
            font-weight: 600;
            color: #374151;
        }
        input, select {
            width: 100%;
            padding: 12px;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            font-size: 16px;
            box-sizing: border-box;
        }
        button {
            width: 100%;
            padding: 15px;
            background: #3b82f6;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 20px;
        }
        button:hover {
            background: #2563eb;
        }
    </style>
</head>
<body>
    <div class="card">
        <h1>📅 Reschedule Task</h1>
        <div class="task-title">{{ task_title }}</div>
        
        <form method="POST" action="/action/custom_delay">
            <input type="hidden" name="task_id" value="{{ task_id }}">
            
            <label>New Date</label>
            <input type="date" name="new_date" value="{{ default_date }}" required>
            
            <label>New Time</label>
            <input type="time" name="new_time" value="{{ default_time }}" required>
            
            <button type="submit">✅ Update Due Date</button>
        </form>
    </div>
</body>
</html>"""


# ========================================
# ROUTES
# ========================================

@app.route('/')
def home():
    """Health check endpoint"""
    return {'status': 'ok', 'service': 'Rob CRM Task Actions', 'version': '2.0'}


@app.route('/action')
def handle_action():
    """Main action handler for email button clicks"""
    action = request.args.get('action')
    task_id = request.args.get('task_id')
    
    if not task_id:
        return render_template_string(ERROR_TEMPLATE, 
            message="Missing task ID")
    
    # Get task info
    task = tm.get_task(task_id)
    if not task:
        return render_template_string(ERROR_TEMPLATE, 
            message="Task not found")
    
    task_title = task.get('title', 'Unknown Task')
    
    # Route to appropriate handler
    if action == 'complete':
        return handle_complete(task_id, task_title)
    
    elif action == 'delay_1hour':
        return handle_delay(task_id, task_title, hours=1)
    
    elif action == 'delay_1day':
        return handle_delay(task_id, task_title, days=1)
    
    elif action == 'delay_custom':
        return handle_custom_delay_form(task_id, task_title, task)
    
    elif action == 'next_status':
        return handle_next_status(task_id, task_title)
    
    elif action == 'prev_status':
        return handle_prev_status(task_id, task_title)
    
    else:
        return render_template_string(ERROR_TEMPLATE, 
            message=f"Unknown action: {action}")


@app.route('/action/custom_delay', methods=['POST'])
def handle_custom_delay_submit():
    """Handle custom delay form submission"""
    task_id = request.form.get('task_id')
    new_date = request.form.get('new_date')
    new_time = request.form.get('new_time')
    
    if not all([task_id, new_date, new_time]):
        return render_template_string(ERROR_TEMPLATE, 
            message="Missing required fields")
    
    task = tm.get_task(task_id)
    if not task:
        return render_template_string(ERROR_TEMPLATE, 
            message="Task not found")
    
    try:
        # Update task
        result = tm.supabase.table('tasks')\
            .update({
                'due_date': new_date,
                'due_time': f"{new_time}:00"
            })\
            .eq('id', task_id)\
            .execute()
        
        if result.data:
            # Add note
            tm.add_note(
                task_id=task_id,
                content=f'Rescheduled to {new_date} {new_time}',
                source='system'
            )
            
            return render_template_string(SUCCESS_TEMPLATE,
                icon="📅",
                title="Task Rescheduled",
                task_title=task.get('title', 'Task'),
                message=f"New due date: {new_date} at {new_time}",
                status_name=None,
                status_color=None,
                status_emoji=None
            )
        else:
            return render_template_string(ERROR_TEMPLATE, 
                message="Failed to update task")
            
    except Exception as e:
        return render_template_string(ERROR_TEMPLATE, 
            message=f"Error: {str(e)}")


# ========================================
# ACTION HANDLERS
# ========================================

def handle_complete(task_id, task_title):
    """Mark task as completed"""
    success = tm.complete_task(task_id)
    
    if success:
        return render_template_string(SUCCESS_TEMPLATE,
            icon="✅",
            title="Task Completed!",
            task_title=task_title,
            message="Great work! This task has been marked as complete.",
            status_name=None,
            status_color=None,
            status_emoji=None
        )
    else:
        return render_template_string(ERROR_TEMPLATE, 
            message="Failed to complete task")


def handle_delay(task_id, task_title, hours=0, days=0):
    """Delay task by specified time"""
    success = tm.delay_task(task_id, hours=hours, days=days)
    
    if success:
        delay_text = f"{hours} hour(s)" if hours else f"{days} day(s)"
        return render_template_string(SUCCESS_TEMPLATE,
            icon="⏰",
            title="Task Delayed",
            task_title=task_title,
            message=f"Pushed back by {delay_text}",
            status_name=None,
            status_color=None,
            status_emoji=None
        )
    else:
        return render_template_string(ERROR_TEMPLATE, 
            message="Failed to delay task")


def handle_custom_delay_form(task_id, task_title, task):
    """Show custom delay form"""
    now = datetime.now(aest)
    default_date = (now + timedelta(days=1)).strftime('%Y-%m-%d')
    default_time = task.get('due_time', '09:00:00')[:5]
    
    return render_template_string(CUSTOM_DELAY_TEMPLATE,
        task_id=task_id,
        task_title=task_title,
        default_date=default_date,
        default_time=default_time
    )


def handle_next_status(task_id, task_title):
    """Move task to next project status"""
    success, result = tm.move_task_to_next_status(task_id)
    
    if success:
        # Get updated task with new status info
        task = tm.get_task(task_id)
        status = task.get('project_statuses', {})
        
        return render_template_string(SUCCESS_TEMPLATE,
            icon="➡️",
            title="Status Updated",
            task_title=task_title,
            message=f"Moved to next stage",
            status_name=status.get('name', result),
            status_color=status.get('color', '#3b82f6'),
            status_emoji=status.get('emoji', '📋')
        )
    else:
        return render_template_string(ERROR_TEMPLATE, 
            message=result)


def handle_prev_status(task_id, task_title):
    """Move task to previous project status"""
    success, result = tm.move_task_to_previous_status(task_id)
    
    if success:
        # Get updated task with new status info
        task = tm.get_task(task_id)
        status = task.get('project_statuses', {})
        
        return render_template_string(SUCCESS_TEMPLATE,
            icon="⬅️",
            title="Status Updated",
            task_title=task_title,
            message=f"Moved to previous stage",
            status_name=status.get('name', result),
            status_color=status.get('color', '#6b7280'),
            status_emoji=status.get('emoji', '📋')
        )
    else:
        return render_template_string(ERROR_TEMPLATE, 
            message=result)


# ========================================
# ENTRY POINT
# ========================================

if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
