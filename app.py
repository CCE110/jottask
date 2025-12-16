"""
Rob CRM Task Actions - Web Service
Version: 2.2-enhanced-checklist
Handles button clicks from emails and checklist management
"""

from flask import Flask, request, redirect
import os
import pytz
from datetime import datetime, timedelta

app = Flask(__name__)

# Initialize TaskManager
from task_manager import TaskManager
tm = TaskManager()

# Load project statuses
try:
    statuses_result = tm.supabase.table('project_statuses').select('*').order('display_order').execute()
    PROJECT_STATUSES = statuses_result.data if statuses_result.data else []
    print(f"📊 Loaded {len(PROJECT_STATUSES)} project statuses")
except Exception as e:
    print(f"⚠️ Could not load project statuses: {e}")
    PROJECT_STATUSES = []

# Get action URL from environment
ACTION_URL = os.getenv('TASK_ACTION_URL', 'https://rob-crm-tasks-production.up.railway.app/action')


# ============================================
# HTML TEMPLATES
# ============================================

SUCCESS_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            padding: 40px;
            text-align: center;
            max-width: 400px;
        }}
        .icon {{ font-size: 64px; margin-bottom: 20px; }}
        h1 {{ color: #1f2937; margin-bottom: 10px; }}
        p {{ color: #6b7280; margin-bottom: 20px; }}
        .task-name {{ color: #667eea; font-weight: 600; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{icon}</div>
        <h1>{title}</h1>
        <p>{message}</p>
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
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            padding: 40px;
            text-align: center;
            max-width: 400px;
        }}
        .icon {{ font-size: 64px; margin-bottom: 20px; }}
        h1 {{ color: #1f2937; margin-bottom: 10px; }}
        p {{ color: #6b7280; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">❌</div>
        <h1>Error</h1>
        <p>{message}</p>
    </div>
</body>
</html>"""

CUSTOM_DELAY_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Set New Time</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            padding: 40px;
            max-width: 400px;
            width: 100%;
        }}
        h1 {{ color: #1f2937; margin-bottom: 8px; text-align: center; }}
        .task-name {{ color: #667eea; text-align: center; margin-bottom: 24px; font-weight: 600; }}
        label {{ display: block; color: #374151; font-weight: 500; margin-bottom: 8px; }}
        input {{
            width: 100%;
            padding: 12px;
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            font-size: 16px;
            margin-bottom: 16px;
        }}
        input:focus {{ outline: none; border-color: #667eea; }}
        button {{
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
        }}
        button:hover {{ opacity: 0.9; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🗓️ Set New Time</h1>
        <div class="task-name">{task_title}</div>
        <form method="POST" action="{action_url}/custom_delay">
            <input type="hidden" name="task_id" value="{task_id}">
            <label>New Date</label>
            <input type="date" name="new_date" value="{current_date}" required>
            <label>New Time</label>
            <input type="time" name="new_time" value="{current_time}" required>
            <button type="submit">💾 Save New Time</button>
        </form>
    </div>
</body>
</html>"""

CHECKLIST_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Update Checklist</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: flex-start;
        }}
        .container {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            max-width: 600px;
            width: 100%;
            margin-top: 40px;
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 24px;
        }}
        .header h1 {{
            font-size: 24px;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .header .task-title {{
            font-size: 14px;
            opacity: 0.9;
        }}
        .content {{
            padding: 24px;
        }}
        
        /* Due Info */
        .due-info {{
            background: #fef3c7;
            color: #92400e;
            padding: 12px 16px;
            border-radius: 10px;
            font-size: 14px;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        /* Delay Buttons Section */
        .delay-section {{
            background: #f8f9ff;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 24px;
        }}
        .delay-section h3 {{
            font-size: 14px;
            color: #667eea;
            margin-bottom: 12px;
            font-weight: 600;
        }}
        .delay-buttons {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .delay-btn {{
            padding: 10px 16px;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .delay-btn.complete {{
            background: #dcfce7;
            color: #166534;
        }}
        .delay-btn.complete:hover {{
            background: #166534;
            color: white;
        }}
        .delay-btn.hour {{
            background: #e0f2fe;
            color: #0369a1;
        }}
        .delay-btn.hour:hover {{
            background: #0369a1;
            color: white;
        }}
        .delay-btn.day {{
            background: #fef3c7;
            color: #b45309;
        }}
        .delay-btn.day:hover {{
            background: #b45309;
            color: white;
        }}
        .delay-btn.week {{
            background: #f3e8ff;
            color: #7c3aed;
        }}
        .delay-btn.week:hover {{
            background: #7c3aed;
            color: white;
        }}
        .delay-btn.custom {{
            background: #f1f5f9;
            color: #475569;
        }}
        .delay-btn.custom:hover {{
            background: #475569;
            color: white;
        }}
        
        /* Checklist Items */
        .checklist-section h3 {{
            font-size: 14px;
            color: #374151;
            margin-bottom: 12px;
            font-weight: 600;
        }}
        .checklist-item {{
            display: flex;
            align-items: flex-start;
            padding: 14px 0;
            border-bottom: 1px solid #f3f4f6;
            gap: 12px;
        }}
        .checklist-item:last-child {{
            border-bottom: none;
        }}
        .checklist-item input[type="checkbox"] {{
            width: 22px;
            height: 22px;
            margin-top: 2px;
            accent-color: #667eea;
            cursor: pointer;
            flex-shrink: 0;
        }}
        .checklist-item label {{
            flex: 1;
            font-size: 15px;
            line-height: 1.5;
            cursor: pointer;
        }}
        .checklist-item.completed label {{
            color: #9ca3af;
            text-decoration: line-through;
        }}
        
        /* Add New Item Section */
        .add-item-section {{
            background: #f0fdf4;
            border-radius: 12px;
            padding: 16px;
            margin: 20px 0;
        }}
        .add-item-section h3 {{
            font-size: 14px;
            color: #166534;
            margin-bottom: 12px;
            font-weight: 600;
        }}
        .add-item-row {{
            display: flex;
            gap: 10px;
        }}
        .add-item-input {{
            flex: 1;
            padding: 12px 14px;
            border: 2px solid #d1fae5;
            border-radius: 8px;
            font-size: 14px;
            transition: border-color 0.2s;
        }}
        .add-item-input:focus {{
            outline: none;
            border-color: #10b981;
        }}
        .add-item-btn {{
            padding: 12px 20px;
            background: #10b981;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
            white-space: nowrap;
        }}
        .add-item-btn:hover {{
            background: #059669;
        }}
        
        /* Save Button */
        .save-btn {{
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            margin-top: 20px;
        }}
        .save-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }}
        
        /* Empty state */
        .empty-state {{
            text-align: center;
            padding: 24px;
            color: #9ca3af;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 Update Checklist</h1>
            <div class="task-title">{task_title}</div>
        </div>
        
        <div class="content">
            <!-- Current Due Time -->
            <div class="due-info">
                ⏰ Currently due: {due_display}
            </div>
            
            <!-- Quick Actions Section -->
            <div class="delay-section">
                <h3>⚡ Quick Actions</h3>
                <div class="delay-buttons">
                    <a href="{action_url}?action=complete&task_id={task_id}" class="delay-btn complete">✅ Complete</a>
                    <a href="{action_url}?action=delay_1hour&task_id={task_id}" class="delay-btn hour">⏰ +1 Hour</a>
                    <a href="{action_url}?action=delay_1day&task_id={task_id}" class="delay-btn day">📅 +1 Day</a>
                    <a href="{action_url}?action=delay_1week&task_id={task_id}" class="delay-btn week">📆 +1 Week</a>
                    <a href="{action_url}?action=delay_custom&task_id={task_id}" class="delay-btn custom">🗓️ Custom</a>
                </div>
            </div>
            
            <!-- Checklist Form -->
            <form method="POST" action="{action_url}/checklist_submit">
                <input type="hidden" name="task_id" value="{task_id}">
                
                <div class="checklist-section">
                    <h3>📝 Checklist Items ({remaining_count} remaining)</h3>
                    {checklist_items}
                </div>
                
                <!-- Add New Item -->
                <div class="add-item-section">
                    <h3>➕ Add New Item</h3>
                    <div class="add-item-row">
                        <input type="text" name="new_item" class="add-item-input" placeholder="Enter new checklist item...">
                        <button type="submit" name="add_only" value="1" class="add-item-btn">Add</button>
                    </div>
                </div>
                
                <button type="submit" class="save-btn">
                    💾 Save Changes
                </button>
            </form>
        </div>
    </div>
</body>
</html>"""


# ============================================
# HELPER FUNCTIONS
# ============================================

def success_page(title, message, task_id=None, icon="✅"):
    """Generate success page HTML"""
    return SUCCESS_TEMPLATE.format(title=title, message=message, icon=icon)


def error_page(message):
    """Generate error page HTML"""
    return ERROR_TEMPLATE.format(message=message)


def get_next_status(current_status_id):
    """Get the next project status in sequence"""
    if not PROJECT_STATUSES:
        return None
    
    current_order = None
    for status in PROJECT_STATUSES:
        if status['id'] == current_status_id:
            current_order = status['display_order']
            break
    
    if current_order is None:
        return PROJECT_STATUSES[0] if PROJECT_STATUSES else None
    
    for status in PROJECT_STATUSES:
        if status['display_order'] == current_order + 1:
            return status
    
    return None


def get_previous_status(current_status_id):
    """Get the previous project status in sequence"""
    if not PROJECT_STATUSES:
        return None
    
    current_order = None
    for status in PROJECT_STATUSES:
        if status['id'] == current_status_id:
            current_order = status['display_order']
            break
    
    if current_order is None or current_order <= 1:
        return None
    
    for status in PROJECT_STATUSES:
        if status['display_order'] == current_order - 1:
            return status
    
    return None


# ============================================
# ROUTES
# ============================================

@app.route('/')
def home():
    """Health check endpoint with version"""
    return {
        "service": "Rob CRM Task Actions",
        "status": "ok",
        "version": "2.2-enhanced-checklist"
    }


@app.route('/action', methods=['GET'])
def handle_action():
    """Handle task action button clicks from emails"""
    action = request.args.get('action')
    task_id = request.args.get('task_id')
    
    if not action or not task_id:
        return error_page("Missing action or task_id parameter")
    
    # Get task details
    try:
        result = tm.supabase.table('tasks').select('*, project_statuses(*)').eq('id', task_id).execute()
        if not result.data:
            return error_page(f"Task not found: {task_id}")
        task = result.data[0]
        task_title = task.get('title', 'Unknown Task')
    except Exception as e:
        return error_page(f"Database error: {e}")
    
    # Handle different actions
    if action == 'complete':
        try:
            tm.supabase.table('tasks').update({
                'status': 'completed',
                'completed_at': datetime.now(pytz.UTC).isoformat()
            }).eq('id', task_id).execute()
            return success_page("Task Completed!", f"'{task_title}' has been marked as complete", task_id)
        except Exception as e:
            return error_page(f"Failed to complete task: {e}")
    
    elif action == 'delay_1hour':
        success = tm.delay_task(task_id, timedelta(hours=1))
        if success:
            return success_page("Task Delayed!", f"'{task_title}' has been delayed by 1 hour", task_id, "⏰")
        else:
            return error_page("Failed to delay task")
    
    elif action == 'delay_1day':
        success = tm.delay_task(task_id, timedelta(days=1))
        if success:
            return success_page("Task Delayed!", f"'{task_title}' has been delayed by 1 day", task_id, "📅")
        else:
            return error_page("Failed to delay task")
    
    elif action == 'delay_1week':
        success = tm.delay_task(task_id, timedelta(days=7))
        if success:
            return success_page("Task Delayed!", f"'{task_title}' has been delayed by 1 week", task_id, "📆")
        else:
            return error_page("Failed to delay task")
    
    elif action == 'delay_custom':
        # Show custom delay form
        aest = pytz.timezone('Australia/Brisbane')
        now = datetime.now(aest)
        current_date = task.get('due_date', now.date().isoformat())
        current_time = task.get('due_time', '09:00')
        if current_time and ':' in current_time:
            current_time = current_time[:5]  # Get HH:MM only
        
        return CUSTOM_DELAY_TEMPLATE.format(
            task_title=task_title,
            task_id=task_id,
            action_url=ACTION_URL,
            current_date=current_date,
            current_time=current_time
        )
    
    elif action == 'next_status':
        current_status_id = task.get('project_status_id')
        next_status = get_next_status(current_status_id)
        
        if next_status:
            try:
                tm.supabase.table('tasks').update({
                    'project_status_id': next_status['id']
                }).eq('id', task_id).execute()
                return success_page("Status Updated!", f"'{task_title}' moved to: {next_status['name']}", task_id, "📈")
            except Exception as e:
                return error_page(f"Failed to update status: {e}")
        else:
            return error_page("Already at final status")
    
    elif action == 'prev_status':
        current_status_id = task.get('project_status_id')
        prev_status = get_previous_status(current_status_id)
        
        if prev_status:
            try:
                tm.supabase.table('tasks').update({
                    'project_status_id': prev_status['id']
                }).eq('id', task_id).execute()
                return success_page("Status Updated!", f"'{task_title}' moved to: {prev_status['name']}", task_id, "📉")
            except Exception as e:
                return error_page(f"Failed to update status: {e}")
        else:
            return error_page("Already at first status")
    
    elif action == 'checklist':
        return handle_checklist_form(task_id, task_title, task)
    
    else:
        return error_page(f"Unknown action: {action}")


@app.route('/action/custom_delay', methods=['POST'])
def handle_custom_delay():
    """Handle custom delay form submission"""
    task_id = request.form.get('task_id')
    new_date = request.form.get('new_date')
    new_time = request.form.get('new_time')
    
    if not task_id or not new_date or not new_time:
        return error_page("Missing required fields")
    
    try:
        # Get task title
        result = tm.supabase.table('tasks').select('title').eq('id', task_id).execute()
        task_title = result.data[0]['title'] if result.data else 'Task'
        
        # Update task
        tm.supabase.table('tasks').update({
            'due_date': new_date,
            'due_time': new_time + ':00',
            'status': 'pending'
        }).eq('id', task_id).execute()
        
        # Format display time
        try:
            h, m = map(int, new_time.split(':'))
            period = 'AM' if h < 12 else 'PM'
            h12 = h if h <= 12 else h - 12
            if h12 == 0:
                h12 = 12
            time_display = f"{h12}:{m:02d} {period}"
        except:
            time_display = new_time
        
        return success_page("Time Updated!", f"'{task_title}' rescheduled to {new_date} at {time_display}", task_id, "🗓️")
    
    except Exception as e:
        return error_page(f"Failed to update task: {e}")


@app.route('/action/checklist_submit', methods=['POST'])
def handle_checklist_submit():
    """Handle checklist form submission - update items and add new ones"""
    task_id = request.form.get('task_id')
    
    if not task_id:
        return error_page("No task ID provided")
    
    # Get task info for success message
    try:
        task = tm.supabase.table('tasks').select('title').eq('id', task_id).execute()
        task_title = task.data[0]['title'] if task.data else 'Task'
    except:
        task_title = 'Task'
    
    # Handle new item addition
    new_item = request.form.get('new_item', '').strip()
    add_only = request.form.get('add_only')
    
    if new_item:
        # Get current max display_order
        existing = tm.get_checklist_items(task_id, include_completed=True)
        max_order = max([i.get('display_order', 0) for i in existing], default=0)
        
        # Add new item
        tm.add_checklist_item(task_id, new_item, max_order + 1)
        
        if add_only:
            # Redirect back to checklist form to add more items
            return redirect(f"{ACTION_URL}?action=checklist&task_id={task_id}")
    
    # Update completion status
    completed_ids = request.form.getlist('completed_items')
    tm.bulk_update_checklist(task_id, completed_ids)
    
    # Get updated counts
    items = tm.get_checklist_items(task_id, include_completed=True)
    total = len(items)
    completed = len([i for i in items if i.get('is_completed')])
    remaining = total - completed
    
    # Success page with stats
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Checklist Updated</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            padding: 40px;
            text-align: center;
            max-width: 450px;
        }}
        .icon {{ font-size: 64px; margin-bottom: 20px; }}
        h1 {{ color: #1f2937; margin-bottom: 10px; font-size: 24px; }}
        .task-name {{ color: #667eea; font-weight: 600; margin-bottom: 20px; }}
        .stats {{
            background: #f3f4f6;
            padding: 20px;
            border-radius: 12px;
            margin: 20px 0;
        }}
        .stats-row {{
            display: flex;
            justify-content: space-around;
        }}
        .stat {{
            text-align: center;
        }}
        .stat-num {{
            font-size: 32px;
            font-weight: 700;
            color: #667eea;
        }}
        .stat-label {{
            font-size: 13px;
            color: #6b7280;
            margin-top: 4px;
        }}
        .buttons {{
            display: flex;
            gap: 12px;
            margin-top: 24px;
            flex-wrap: wrap;
            justify-content: center;
        }}
        .btn {{
            padding: 14px 24px;
            border-radius: 10px;
            text-decoration: none;
            font-weight: 600;
            font-size: 14px;
            transition: all 0.2s;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        .btn-secondary {{
            background: #f3f4f6;
            color: #374151;
        }}
        .btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">✅</div>
        <h1>Checklist Updated!</h1>
        <div class="task-name">{task_title}</div>
        
        <div class="stats">
            <div class="stats-row">
                <div class="stat">
                    <div class="stat-num">{completed}</div>
                    <div class="stat-label">Completed</div>
                </div>
                <div class="stat">
                    <div class="stat-num">{remaining}</div>
                    <div class="stat-label">Remaining</div>
                </div>
                <div class="stat">
                    <div class="stat-num">{total}</div>
                    <div class="stat-label">Total</div>
                </div>
            </div>
        </div>
        
        <div class="buttons">
            <a href="{ACTION_URL}?action=checklist&task_id={task_id}" class="btn btn-primary">
                📋 Edit Checklist
            </a>
            <a href="{ACTION_URL}?action=complete&task_id={task_id}" class="btn btn-secondary">
                ✅ Complete Task
            </a>
        </div>
    </div>
</body>
</html>'''


def handle_checklist_form(task_id, task_title, task):
    """Display checklist form with delay buttons and add item feature"""
    # Get checklist items
    items = tm.get_checklist_items(task_id, include_completed=True)
    
    # Build due display
    aest = pytz.timezone('Australia/Brisbane')
    due_date = task.get('due_date', 'Not set')
    due_time = task.get('due_time', '')
    
    if due_time:
        try:
            parts = due_time.split(':')
            h, m = int(parts[0]), int(parts[1])
            # Convert to 12-hour format
            period = 'AM' if h < 12 else 'PM'
            h12 = h if h <= 12 else h - 12
            if h12 == 0:
                h12 = 12
            due_display = f"{due_date} at {h12}:{m:02d} {period} AEST"
        except:
            due_display = f"{due_date} at {due_time}"
    else:
        due_display = f"{due_date} (no time set)"
    
    # Count remaining items
    remaining_count = len([i for i in items if not i.get('is_completed')])
    
    # Build checklist HTML
    if items:
        items_html = ""
        for item in items:
            checked = "checked" if item.get('is_completed') else ""
            completed_class = "completed" if item.get('is_completed') else ""
            items_html += f'''
                <div class="checklist-item {completed_class}">
                    <input type="checkbox" name="completed_items" value="{item['id']}" id="item_{item['id']}" {checked}>
                    <label for="item_{item['id']}">{item['item_text']}</label>
                </div>
            '''
    else:
        items_html = '<div class="empty-state">No checklist items yet. Add one below!</div>'
    
    # Build final HTML
    html = CHECKLIST_TEMPLATE.format(
        task_title=task_title,
        task_id=task_id,
        action_url=ACTION_URL,
        checklist_items=items_html,
        remaining_count=remaining_count,
        due_display=due_display
    )
    
    return html


# ============================================
# MAIN
# ============================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)