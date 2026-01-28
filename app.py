"""
Rob CRM Task Actions - Web Service
Version: 2.2.1-fixed
Handles button clicks from reminder emails and checklist management
"""

import os
from flask import Flask, request, redirect, url_for
from task_manager import TaskManager
from enhanced_task_manager import EnhancedTaskManager
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# Initialize managers
tm = TaskManager()
etm = EnhancedTaskManager()

# Load project statuses on startup
try:
    statuses = tm.supabase.table('project_statuses').select('*').order('display_order').execute()
    PROJECT_STATUSES = statuses.data if statuses.data else []
    print(f"📊 Loaded {len(PROJECT_STATUSES)} project statuses")
except Exception as e:
    print(f"⚠️ Could not load project statuses: {e}")
    PROJECT_STATUSES = []

# Get action URL from environment
ACTION_URL = os.getenv('TASK_ACTION_URL', 'https://www.jottask.app/action')

# ============================================
# HTML TEMPLATES
# ============================================

SUCCESS_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>✅ {title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        .icon {{ font-size: 64px; margin-bottom: 20px; }}
        h1 {{ color: #1a1a2e; margin-bottom: 15px; font-size: 24px; }}
        .message {{ color: #666; margin-bottom: 25px; line-height: 1.6; }}
        .task-title {{ 
            background: #f0f0f0; 
            padding: 15px; 
            border-radius: 8px; 
            margin: 20px 0;
            font-weight: 500;
            color: #333;
        }}
        .close-note {{ color: #999; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{icon}</div>
        <h1>{title}</h1>
        <div class="task-title">{task_title}</div>
        <p class="message">{message}</p>
        <p class="close-note">You can close this window</p>
    </div>
</body>
</html>"""

ERROR_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>❌ Error</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #ff6b6b 0%, #c0392b 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        .icon {{ font-size: 64px; margin-bottom: 20px; }}
        h1 {{ color: #c0392b; margin-bottom: 15px; }}
        .message {{ color: #666; line-height: 1.6; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">❌</div>
        <h1>Something went wrong</h1>
        <p class="message">{error}</p>
    </div>
</body>
</html>"""

CUSTOM_DELAY_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🗓️ Reschedule Task</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        h1 {{ color: #1a1a2e; margin-bottom: 10px; font-size: 24px; }}
        .task-title {{ 
            background: #f0f0f0; 
            padding: 15px; 
            border-radius: 8px; 
            margin: 20px 0;
            font-weight: 500;
            color: #333;
        }}
        label {{ display: block; margin-bottom: 8px; font-weight: 500; color: #333; }}
        input {{ 
            width: 100%; 
            padding: 12px; 
            border: 2px solid #e0e0e0; 
            border-radius: 8px; 
            font-size: 16px;
            margin-bottom: 20px;
        }}
        input:focus {{ outline: none; border-color: #667eea; }}
        button {{
            width: 100%;
            padding: 15px;
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
        <h1>🗓️ Reschedule Task</h1>
        <div class="task-title">{task_title}</div>
        <form method="POST" action="{action_url}/custom_delay">
            <input type="hidden" name="task_id" value="{task_id}">
            <label>New Date:</label>
            <input type="date" name="new_date" value="{default_date}" required>
            <label>New Time:</label>
            <input type="time" name="new_time" value="{default_time}" required>
            <button type="submit">📅 Update Schedule</button>
        </form>
    </div>
</body>
</html>"""

RESCHEDULE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>✏️ Edit & Reschedule Task</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 30px;
            max-width: 550px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        h1 {{ color: #1a1a2e; margin-bottom: 8px; font-size: 22px; }}
        .current-info {{
            background: #fff3cd;
            color: #856404;
            padding: 12px 15px;
            border-radius: 8px;
            font-size: 14px;
            margin-bottom: 20px;
        }}
        .section {{
            margin-bottom: 20px;
        }}
        .section-title {{
            font-size: 13px;
            font-weight: 600;
            color: #666;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        label {{ display: block; margin-bottom: 8px; font-weight: 500; color: #333; }}
        input[type="text"], input[type="date"], input[type="time"] {{ 
            width: 100%; 
            padding: 12px; 
            border: 2px solid #e0e0e0; 
            border-radius: 8px; 
            font-size: 16px;
            margin-bottom: 10px;
        }}
        input:focus {{ outline: none; border-color: #667eea; }}
        .quick-delays {{
            background: #e8f4fd;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 20px;
        }}
        .quick-delays h3 {{
            font-size: 13px;
            color: #1976d2;
            margin-bottom: 12px;
        }}
        .delay-buttons {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .delay-btn {{
            padding: 10px 16px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            border: none;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .btn-hour {{ background: #cce5ff; color: #004085; }}
        .btn-hour:hover {{ background: #007bff; color: white; }}
        .btn-day {{ background: #fff3cd; color: #856404; }}
        .btn-day:hover {{ background: #ffc107; color: #333; }}
        .btn-week {{ background: #e2d5f1; color: #6f42c1; }}
        .btn-week:hover {{ background: #6f42c1; color: white; }}
        .btn-complete {{ background: #d4edda; color: #155724; }}
        .btn-complete:hover {{ background: #28a745; color: white; }}
        .custom-section {{
            background: #f8f9fa;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 20px;
        }}
        .custom-section h3 {{
            font-size: 13px;
            color: #495057;
            margin-bottom: 12px;
        }}
        .date-time-row {{
            display: flex;
            gap: 10px;
        }}
        .date-time-row > div {{
            flex: 1;
        }}
        .submit-btn {{
            width: 100%;
            padding: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
        }}
        .submit-btn:hover {{ opacity: 0.9; }}
        .checklist-link {{
            display: block;
            text-align: center;
            margin-top: 15px;
            color: #667eea;
            text-decoration: none;
            font-size: 14px;
        }}
        .checklist-link:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>✏️ Edit & Reschedule Task</h1>
        
        <div class="current-info">
            ⏰ Currently due: {current_due_date} at {current_due_time}
        </div>
        
        <form method="POST" action="{action_url}/reschedule_submit">
            <input type="hidden" name="task_id" value="{task_id}">
            
            <div class="section">
                <div class="section-title">📝 Task Name</div>
                <input type="text" name="task_title" value="{task_title}" required>
            </div>
            
            <div class="quick-delays">
                <h3>⚡ Quick Reschedule (from now)</h3>
                <div class="delay-buttons">
                    <button type="submit" name="quick_delay" value="1hour" class="delay-btn btn-hour">⏰ +1 Hour</button>
                    <button type="submit" name="quick_delay" value="1day" class="delay-btn btn-day">📅 +1 Day</button>
                    <button type="submit" name="quick_delay" value="1week" class="delay-btn btn-week">📆 +1 Week</button>
                    <button type="submit" name="quick_delay" value="complete" class="delay-btn btn-complete">✅ Complete</button>
                </div>
            </div>
            
            <div class="custom-section">
                <h3>🗓️ Custom Date & Time</h3>
                <div class="date-time-row">
                    <div>
                        <label>Date:</label>
                        <input type="date" name="new_date" value="{default_date}">
                    </div>
                    <div>
                        <label>Time:</label>
                        <input type="time" name="new_time" value="{default_time}">
                    </div>
                </div>
            </div>
            
            <button type="submit" name="quick_delay" value="custom" class="submit-btn">💾 Save Changes</button>
        </form>
        
        <a href="{action_url}?action=checklist&task_id={task_id}" class="checklist-link">📋 View/Edit Checklist</a>
    </div>
</body>
</html>"""

CHECKLIST_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📋 Update Checklist</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 30px;
            max-width: 600px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        h1 {{ color: #1a1a2e; margin-bottom: 8px; font-size: 22px; }}
        .task-title {{ 
            color: #666;
            font-size: 14px;
            margin-bottom: 15px;
            padding-bottom: 15px;
            border-bottom: 1px solid #eee;
        }}
        .due-info {{
            background: #fff3cd;
            color: #856404;
            padding: 10px 15px;
            border-radius: 8px;
            font-size: 14px;
            margin-bottom: 15px;
        }}
        .quick-actions {{
            background: #e8f4fd;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 20px;
        }}
        .quick-actions h3 {{
            font-size: 13px;
            color: #1976d2;
            margin-bottom: 10px;
        }}
        .action-buttons {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .action-btn {{
            padding: 8px 14px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            display: inline-block;
            transition: all 0.2s;
        }}
        .btn-complete {{ background: #d4edda; color: #155724; }}
        .btn-complete:hover {{ background: #28a745; color: white; }}
        .btn-hour {{ background: #cce5ff; color: #004085; }}
        .btn-hour:hover {{ background: #007bff; color: white; }}
        .btn-day {{ background: #fff3cd; color: #856404; }}
        .btn-day:hover {{ background: #ffc107; color: #333; }}
        .btn-week {{ background: #e2d5f1; color: #6f42c1; }}
        .btn-week:hover {{ background: #6f42c1; color: white; }}
        .btn-custom {{ background: #e2e3e5; color: #383d41; }}
        .btn-custom:hover {{ background: #6c757d; color: white; }}
        .section-title {{
            font-size: 13px;
            font-weight: 600;
            color: #333;
            margin-bottom: 10px;
        }}
        .checklist-item {{
            display: flex;
            align-items: flex-start;
            padding: 12px 0;
            border-bottom: 1px solid #f0f0f0;
        }}
        .checklist-item:last-child {{ border-bottom: none; }}
        .checklist-item input[type="checkbox"] {{
            width: 20px;
            height: 20px;
            margin-right: 12px;
            margin-top: 2px;
            cursor: pointer;
            accent-color: #667eea;
        }}
        .checklist-item label {{
            flex: 1;
            cursor: pointer;
            line-height: 1.5;
            color: #333;
        }}
        .checklist-item.completed label {{
            text-decoration: line-through;
            color: #999;
        }}
        .add-section {{
            background: #e8f5e9;
            border-radius: 12px;
            padding: 15px;
            margin: 20px 0;
        }}
        .add-section h3 {{
            font-size: 13px;
            color: #2e7d32;
            margin-bottom: 10px;
        }}
        .add-row {{
            display: flex;
            gap: 10px;
        }}
        .add-row input[type="text"] {{
            flex: 1;
            padding: 10px 14px;
            border: 2px solid #c8e6c9;
            border-radius: 8px;
            font-size: 14px;
        }}
        .add-row input[type="text"]:focus {{
            outline: none;
            border-color: #4caf50;
        }}
        .add-btn {{
            padding: 10px 20px;
            background: #4caf50;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
        }}
        .add-btn:hover {{ background: #43a047; }}
        .submit-btn {{
            width: 100%;
            padding: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 10px;
        }}
        .submit-btn:hover {{ opacity: 0.9; }}
        .no-items {{
            text-align: center;
            padding: 30px;
            color: #999;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>📋 Update Checklist</h1>
        <div class="task-title">{task_title}</div>
        
        <div class="due-info">
            ⏰ Currently due: {due_date} at {due_time}
        </div>
        
        <div class="quick-actions">
            <h3>⚡ Quick Actions</h3>
            <div class="action-buttons">
                <a href="{action_url}?action=complete&task_id={task_id}" class="action-btn btn-complete">✅ Complete</a>
                <a href="{action_url}?action=delay_1hour&task_id={task_id}" class="action-btn btn-hour">⏰ +1 Hour</a>
                <a href="{action_url}?action=delay_1day&task_id={task_id}" class="action-btn btn-day">📅 +1 Day</a>
                <a href="{action_url}?action=delay_1week&task_id={task_id}" class="action-btn btn-week">📆 +1 Week</a>
                <a href="{action_url}?action=delay_custom&task_id={task_id}" class="action-btn btn-custom">🗓️ Custom</a>
            </div>
        </div>
        
        <form method="POST" action="{action_url}/checklist_submit">
            <input type="hidden" name="task_id" value="{task_id}">
            
            <div class="section-title">📝 Checklist Items ({remaining_count} remaining)</div>
            {checklist_items}
            
            <div class="add-section">
                <h3>➕ Add New Item</h3>
                <div class="add-row">
                    <input type="text" name="new_item" placeholder="Enter new checklist item...">
                    <button type="submit" name="action" value="add" class="add-btn">Add</button>
                </div>
            </div>
            
            <button type="submit" name="action" value="save" class="submit-btn">💾 Save Changes</button>
        </form>
    </div>
</body>
</html>"""

CHECKLIST_SUCCESS_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>✅ Checklist Updated</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        .icon {{ font-size: 64px; margin-bottom: 20px; }}
        h1 {{ color: #1a1a2e; margin-bottom: 15px; font-size: 24px; }}
        .stats {{
            background: #f8f9fa;
            border-radius: 12px;
            padding: 20px;
            margin: 20px 0;
        }}
        .stat-row {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #e9ecef;
        }}
        .stat-row:last-child {{ border-bottom: none; }}
        .stat-label {{ color: #666; }}
        .stat-value {{ font-weight: 600; color: #333; }}
        .buttons {{
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }}
        .btn {{
            flex: 1;
            padding: 12px 20px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 600;
            font-size: 14px;
            text-align: center;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        .btn-success {{
            background: #28a745;
            color: white;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">✅</div>
        <h1>Checklist Updated!</h1>
        <div class="stats">
            <div class="stat-row">
                <span class="stat-label">Completed:</span>
                <span class="stat-value">{completed_count}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Remaining:</span>
                <span class="stat-value">{remaining_count}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Total Items:</span>
                <span class="stat-value">{total_count}</span>
            </div>
        </div>
        <div class="buttons">
            <a href="{action_url}?action=checklist&task_id={task_id}" class="btn btn-primary">📋 Edit Checklist</a>
            <a href="{action_url}?action=complete&task_id={task_id}" class="btn btn-success">✅ Complete Task</a>
        </div>
    </div>
</body>
</html>"""


# ============================================
# ROUTES
# ============================================

@app.route('/')
def home():
    """Health check endpoint with version"""
    return {
        "service": "Rob CRM Task Actions",
        "status": "ok",
        "version": "2.3-reschedule"
    }


@app.route('/action')
def handle_action():
    """Main action handler - processes button clicks from emails"""
    action = request.args.get('action')
    task_id = request.args.get('task_id')
    project_id = request.args.get('project_id')
    item_id = request.args.get('item_id')

    # ==========================================
    # PROJECT ACTIONS (don't require task_id)
    # ==========================================
    if action == 'view_project':
        if not project_id:
            return ERROR_TEMPLATE.format(error="Missing project_id parameter")
        return handle_view_project(project_id)

    elif action == 'complete_project_item':
        if not item_id:
            return ERROR_TEMPLATE.format(error="Missing item_id parameter")
        return handle_complete_project_item(item_id, project_id)

    elif action == 'uncomplete_project_item':
        if not item_id:
            return ERROR_TEMPLATE.format(error="Missing item_id parameter")
        tm.uncomplete_project_item(item_id)
        return f'<script>window.location.href="{ACTION_URL}?action=view_project&project_id={project_id}";</script>'

    elif action == 'add_project_item':
        if not project_id:
            return ERROR_TEMPLATE.format(error="Missing project_id parameter")
        return handle_add_project_item_form(project_id)

    elif action == 'complete_all_project':
        if not project_id:
            return ERROR_TEMPLATE.format(error="Missing project_id parameter")
        return handle_complete_all_project_items(project_id)

    # ==========================================
    # TASK ACTIONS (require task_id)
    # ==========================================
    if not task_id:
        return ERROR_TEMPLATE.format(error="Missing task_id parameter")
    
    # Get task details
    try:
        result = tm.supabase.table('tasks').select('*').eq('id', task_id).execute()
        if not result.data:
            return ERROR_TEMPLATE.format(error=f"Task not found: {task_id}")
        task = result.data[0]
        task_title = task.get('title', 'Unknown Task')
    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Database error: {str(e)}")
    
    # Route to appropriate handler
    if action == 'complete':
        return handle_complete(task_id, task_title)
    
    elif action == 'delay_1hour':
        return handle_reschedule_form(task_id, task_title, task, preset='1hour')
    
    elif action == 'delay_1day':
        return handle_reschedule_form(task_id, task_title, task, preset='1day')
    
    elif action == 'delay_1week':
        return handle_reschedule_form(task_id, task_title, task, preset='1week')
    
    elif action == 'delay_custom' or action == 'reschedule':
        return handle_reschedule_form(task_id, task_title, task)
    
    elif action == 'next_status':
        return handle_next_status(task_id, task_title, task)
    
    elif action == 'prev_status':
        return handle_prev_status(task_id, task_title, task)
    
    elif action == 'checklist':
        return handle_checklist_form(task_id, task_title, task)
    
    else:
        return ERROR_TEMPLATE.format(error=f"Unknown action: {action}")


@app.route('/action/custom_delay', methods=['POST'])
def handle_custom_delay_submit():
    """Process custom delay form submission"""
    task_id = request.form.get('task_id')
    new_date = request.form.get('new_date')
    new_time = request.form.get('new_time')
    
    if not all([task_id, new_date, new_time]):
        return ERROR_TEMPLATE.format(error="Missing required fields")
    
    try:
        # Get task title
        result = tm.supabase.table('tasks').select('title').eq('id', task_id).execute()
        task_title = result.data[0]['title'] if result.data else 'Unknown Task'
        
        # Update task
        tm.supabase.table('tasks').update({
            'due_date': new_date,
            'due_time': new_time + ':00',
            'status': 'pending'
        }).eq('id', task_id).execute()
        
        # Format for display
        aest = pytz.timezone('Australia/Brisbane')
        dt = datetime.strptime(f"{new_date} {new_time}", "%Y-%m-%d %H:%M")
        formatted_time = dt.strftime("%I:%M %p")
        formatted_date = dt.strftime("%A, %B %d")
        
        return SUCCESS_TEMPLATE.format(
            icon="📅",
            title="Task Rescheduled",
            task_title=task_title,
            message=f"Rescheduled to {formatted_date} at {formatted_time}"
        )
        
    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Failed to reschedule: {str(e)}")


@app.route('/action/reschedule_submit', methods=['POST'])
def handle_reschedule_submit():
    """Process reschedule form submission - handles title edit and delays from NOW"""
    task_id = request.form.get('task_id')
    new_title = request.form.get('task_title', '').strip()
    quick_delay = request.form.get('quick_delay', '')
    new_date = request.form.get('new_date', '')
    new_time = request.form.get('new_time', '')
    
    if not task_id:
        return ERROR_TEMPLATE.format(error="Missing task_id")
    
    try:
        aest = pytz.timezone('Australia/Brisbane')
        now = datetime.now(aest)
        
        # Get current task
        result = tm.supabase.table('tasks').select('*').eq('id', task_id).execute()
        if not result.data:
            return ERROR_TEMPLATE.format(error="Task not found")
        
        task = result.data[0]
        old_title = task.get('title', 'Unknown Task')
        
        # Handle complete action
        if quick_delay == 'complete':
            update_data = {
                'status': 'completed',
                'completed_at': datetime.now(pytz.UTC).isoformat()
            }
            if new_title and new_title != old_title:
                update_data['title'] = new_title
            
            tm.supabase.table('tasks').update(update_data).eq('id', task_id).execute()
            
            return SUCCESS_TEMPLATE.format(
                icon="✅",
                title="Task Completed!",
                task_title=new_title or old_title,
                message="Great job! This task has been marked as complete."
            )
        
        # Calculate new due time based on quick_delay
        if quick_delay == '1hour':
            new_dt = now + timedelta(hours=1)
            delay_text = "1 hour from now"
        elif quick_delay == '1day':
            new_dt = now + timedelta(days=1)
            delay_text = "1 day from now"
        elif quick_delay == '1week':
            new_dt = now + timedelta(days=7)
            delay_text = "1 week from now"
        elif quick_delay == 'custom' and new_date and new_time:
            # Use custom date/time
            new_dt = datetime.strptime(f"{new_date} {new_time}", "%Y-%m-%d %H:%M")
            new_dt = aest.localize(new_dt)
            delay_text = "custom time"
        else:
            return ERROR_TEMPLATE.format(error="Invalid delay option")
        
        # Build update data
        update_data = {
            'due_date': new_dt.date().isoformat(),
            'due_time': new_dt.strftime('%H:%M:%S'),
            'status': 'pending'
        }
        
        # Update title if changed
        if new_title and new_title != old_title:
            update_data['title'] = new_title
        
        # Update task
        tm.supabase.table('tasks').update(update_data).eq('id', task_id).execute()
        
        return SUCCESS_TEMPLATE.format(
            icon="📅",
            title="Task Updated!",
            task_title=new_title or old_title,
            message=f"Rescheduled to {new_dt.strftime('%I:%M %p')} on {new_dt.strftime('%A, %B %d')} ({delay_text})"
        )
        
    except Exception as e:
        print(f"❌ Reschedule error: {str(e)}")
        import traceback
        traceback.print_exc()
        return ERROR_TEMPLATE.format(error=f"Failed to reschedule: {str(e)}")


@app.route('/action/checklist_submit', methods=['POST'])
def handle_checklist_submit():
    """Process checklist form submission - FIXED VERSION using direct DB calls"""
    task_id = request.form.get('task_id')
    action = request.form.get('action', 'save')
    new_item = request.form.get('new_item', '').strip()
    
    if not task_id:
        return ERROR_TEMPLATE.format(error="Missing task_id")
    
    try:
        # Get task title
        result = tm.supabase.table('tasks').select('title').eq('id', task_id).execute()
        task_title = result.data[0]['title'] if result.data else 'Unknown Task'
        
        # Handle adding new item
        if new_item:
            print(f"📝 Adding new checklist item: {new_item}")
            
            # Get max display_order using direct DB call
            existing = tm.supabase.table('task_checklist_items')\
                .select('display_order')\
                .eq('task_id', task_id)\
                .order('display_order', desc=True)\
                .limit(1)\
                .execute()
            
            max_order = existing.data[0]['display_order'] if existing.data else 0
            
            # Insert new item using direct DB call
            tm.supabase.table('task_checklist_items').insert({
                'task_id': task_id,
                'item_text': new_item,
                'is_completed': False,
                'display_order': max_order + 1
            }).execute()
            
            print(f"✅ Added checklist item with order {max_order + 1}")
            
            # If just adding (not saving), redirect back to form
            if action == 'add':
                return redirect(f"{ACTION_URL}?action=checklist&task_id={task_id}")
        
        # Handle checkbox updates
        checked_ids = request.form.getlist('completed')
        print(f"📋 Checked item IDs: {checked_ids}")
        
        # Get all items for this task using direct DB call
        all_items = tm.supabase.table('task_checklist_items')\
            .select('*')\
            .eq('task_id', task_id)\
            .execute()
        
        # Update each item's completion status
        for item in all_items.data:
            item_id = item['id']
            should_be_completed = item_id in checked_ids
            
            if item['is_completed'] != should_be_completed:
                update_data = {'is_completed': should_be_completed}
                if should_be_completed:
                    update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
                else:
                    update_data['completed_at'] = None
                
                tm.supabase.table('task_checklist_items')\
                    .update(update_data)\
                    .eq('id', item_id)\
                    .execute()
        
        # Get final counts
        final_items = tm.supabase.table('task_checklist_items')\
            .select('*')\
            .eq('task_id', task_id)\
            .execute()
        
        total = len(final_items.data)
        completed = len([i for i in final_items.data if i.get('is_completed')])
        remaining = total - completed
        
        return CHECKLIST_SUCCESS_TEMPLATE.format(
            completed_count=completed,
            remaining_count=remaining,
            total_count=total,
            action_url=ACTION_URL,
            task_id=task_id
        )
        
    except Exception as e:
        print(f"❌ Checklist submit error: {str(e)}")
        import traceback
        traceback.print_exc()
        return ERROR_TEMPLATE.format(error=f"Failed to update checklist: {str(e)}")


# ============================================
# ACTION HANDLERS
# ============================================

def handle_complete(task_id, task_title):
    """Mark task as complete"""
    try:
        tm.supabase.table('tasks').update({
            'status': 'completed',
            'completed_at': datetime.now(pytz.UTC).isoformat()
        }).eq('id', task_id).execute()
        
        return SUCCESS_TEMPLATE.format(
            icon="✅",
            title="Task Completed!",
            task_title=task_title,
            message="Great job! This task has been marked as complete."
        )
    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Failed to complete task: {str(e)}")


def handle_delay(task_id, task_title, hours=0, days=0):
    """Delay task by specified time"""
    try:
        # Get current task
        result = tm.supabase.table('tasks').select('*').eq('id', task_id).execute()
        if not result.data:
            return ERROR_TEMPLATE.format(error="Task not found")
        
        task = result.data[0]
        aest = pytz.timezone('Australia/Brisbane')
        now = datetime.now(aest)
        
        # Parse current due date/time
        due_date_str = task.get('due_date')
        due_time_str = task.get('due_time')
        
        if due_date_str and due_time_str:
            # Handle microseconds in time string
            parts = due_time_str.split(':')
            h, m = int(parts[0]), int(parts[1])
            s = int(float(parts[2])) if len(parts) > 2 else 0
            
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            current_dt = datetime.combine(due_date, datetime.min.time().replace(hour=h, minute=m, second=s))
            current_dt = aest.localize(current_dt)
        else:
            current_dt = now
        
        # Calculate new time
        new_dt = current_dt + timedelta(hours=hours, days=days)
        
        # Update task
        tm.supabase.table('tasks').update({
            'due_date': new_dt.date().isoformat(),
            'due_time': new_dt.strftime('%H:%M:%S'),
            'status': 'pending'
        }).eq('id', task_id).execute()
        
        # Format message
        if hours:
            delay_text = f"{hours} hour{'s' if hours > 1 else ''}"
        else:
            delay_text = f"{days} day{'s' if days > 1 else ''}"
        
        return SUCCESS_TEMPLATE.format(
            icon="⏰",
            title=f"Delayed {delay_text}",
            task_title=task_title,
            message=f"New due time: {new_dt.strftime('%I:%M %p')} on {new_dt.strftime('%A, %B %d')}"
        )
        
    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Failed to delay task: {str(e)}")


def handle_custom_delay_form(task_id, task_title, task):
    """Show custom delay form"""
    aest = pytz.timezone('Australia/Brisbane')
    now = datetime.now(aest)
    
    # Default to tomorrow same time
    default_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    default_time = task.get('due_time', '09:00:00')[:5]  # HH:MM only
    
    return CUSTOM_DELAY_TEMPLATE.format(
        task_id=task_id,
        task_title=task_title,
        action_url=ACTION_URL,
        default_date=default_date,
        default_time=default_time
    )


def handle_reschedule_form(task_id, task_title, task, preset=None):
    """Show enhanced reschedule form with title editing"""
    aest = pytz.timezone('Australia/Brisbane')
    now = datetime.now(aest)
    
    # Get current due date/time for display
    due_date_str = task.get('due_date', '')
    due_time_str = task.get('due_time', '09:00:00')
    
    # Format current due for display
    if due_date_str:
        try:
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
            current_due_date = due_date.strftime("%A, %d %B %Y")
        except:
            current_due_date = due_date_str
    else:
        current_due_date = "Not set"
    
    # Format time for display (12-hour)
    if due_time_str:
        try:
            parts = due_time_str.split(':')
            h, m = int(parts[0]), int(parts[1])
            current_due_time = datetime(2000, 1, 1, h, m).strftime("%I:%M %p")
        except:
            current_due_time = due_time_str[:5]
    else:
        current_due_time = "Not set"
    
    # Default values for form
    default_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    default_time = due_time_str[:5] if due_time_str else "09:00"
    
    return RESCHEDULE_TEMPLATE.format(
        task_id=task_id,
        task_title=task_title,
        current_due_date=current_due_date,
        current_due_time=current_due_time,
        action_url=ACTION_URL,
        default_date=default_date,
        default_time=default_time
    )


def handle_next_status(task_id, task_title, task):
    """Move to next project status"""
    try:
        current_status_id = task.get('project_status_id')
        
        if not PROJECT_STATUSES:
            return ERROR_TEMPLATE.format(error="No project statuses configured")
        
        # Find current position and move to next
        current_idx = -1
        for i, status in enumerate(PROJECT_STATUSES):
            if status['id'] == current_status_id:
                current_idx = i
                break
        
        if current_idx < len(PROJECT_STATUSES) - 1:
            new_status = PROJECT_STATUSES[current_idx + 1]
        else:
            new_status = PROJECT_STATUSES[-1]  # Stay at last
        
        # Update task
        tm.supabase.table('tasks').update({
            'project_status_id': new_status['id']
        }).eq('id', task_id).execute()
        
        return SUCCESS_TEMPLATE.format(
            icon="⏭️",
            title="Status Updated",
            task_title=task_title,
            message=f"Moved to: {new_status.get('emoji', '')} {new_status['name']}"
        )
        
    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Failed to update status: {str(e)}")


def handle_prev_status(task_id, task_title, task):
    """Move to previous project status"""
    try:
        current_status_id = task.get('project_status_id')
        
        if not PROJECT_STATUSES:
            return ERROR_TEMPLATE.format(error="No project statuses configured")
        
        # Find current position and move to previous
        current_idx = 0
        for i, status in enumerate(PROJECT_STATUSES):
            if status['id'] == current_status_id:
                current_idx = i
                break
        
        if current_idx > 0:
            new_status = PROJECT_STATUSES[current_idx - 1]
        else:
            new_status = PROJECT_STATUSES[0]  # Stay at first
        
        # Update task
        tm.supabase.table('tasks').update({
            'project_status_id': new_status['id']
        }).eq('id', task_id).execute()
        
        return SUCCESS_TEMPLATE.format(
            icon="⏮️",
            title="Status Updated",
            task_title=task_title,
            message=f"Moved to: {new_status.get('emoji', '')} {new_status['name']}"
        )
        
    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Failed to update status: {str(e)}")


def handle_checklist_form(task_id, task_title, task):
    """Display checklist management form"""
    try:
        # Get checklist items using direct DB call
        items_result = tm.supabase.table('task_checklist_items')\
            .select('*')\
            .eq('task_id', task_id)\
            .order('display_order')\
            .execute()
        
        items = items_result.data if items_result.data else []
        
        # Build checklist HTML
        if items:
            items_html = ""
            for item in items:
                checked = "checked" if item.get('is_completed') else ""
                completed_class = "completed" if item.get('is_completed') else ""
                items_html += f"""
                <div class="checklist-item {completed_class}">
                    <input type="checkbox" name="completed" value="{item['id']}" id="item_{item['id']}" {checked}>
                    <label for="item_{item['id']}">{item['item_text']}</label>
                </div>
                """
        else:
            items_html = '<div class="no-items">No checklist items yet. Add one below!</div>'
        
        # Count remaining
        remaining = len([i for i in items if not i.get('is_completed')])
        
        # Format due date/time
        due_date = task.get('due_date', 'Not set')
        due_time_str = task.get('due_time', '')
        if due_time_str:
            parts = due_time_str.split(':')
            h, m = int(parts[0]), int(parts[1])
            due_time = datetime.strptime(f"{h}:{m}", "%H:%M").strftime("%I:%M %p")
        else:
            due_time = 'Not set'
        
        return CHECKLIST_TEMPLATE.format(
            task_id=task_id,
            task_title=task_title,
            action_url=ACTION_URL,
            checklist_items=items_html,
            remaining_count=remaining,
            due_date=due_date,
            due_time=due_time
        )
        
    except Exception as e:
        print(f"❌ Checklist form error: {str(e)}")
        import traceback
        traceback.print_exc()
        return ERROR_TEMPLATE.format(error=f"Failed to load checklist: {str(e)}")


# ============================================
# PROJECT HANDLERS
# ============================================

PROJECT_VIEW_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{project_name}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px; max-width: 600px; margin: 0 auto; background: #f9fafb; }}
        .header {{ background: linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%); color: white; padding: 20px; border-radius: 12px 12px 0 0; }}
        .content {{ background: white; padding: 20px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; }}
        .item {{ display: flex; align-items: center; padding: 12px; margin: 8px 0; background: #f9fafb; border-radius: 8px; }}
        .item.completed {{ opacity: 0.6; text-decoration: line-through; }}
        .checkbox {{ width: 24px; height: 24px; margin-right: 12px; cursor: pointer; }}
        .progress {{ background: #e5e7eb; border-radius: 4px; height: 8px; margin: 15px 0; }}
        .progress-bar {{ background: #10b981; border-radius: 4px; height: 8px; }}
        .btn {{ display: inline-block; padding: 10px 16px; margin: 5px; border-radius: 6px; text-decoration: none; font-weight: bold; }}
        .btn-primary {{ background: #8b5cf6; color: white; }}
        .btn-success {{ background: #10b981; color: white; }}
        .btn-secondary {{ background: #6b7280; color: white; }}
        input[type="text"] {{ width: 100%; padding: 12px; border: 1px solid #e5e7eb; border-radius: 8px; font-size: 16px; margin-top: 10px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1 style="margin: 0;">📁 {project_name}</h1>
        <p style="margin: 5px 0 0 0; opacity: 0.9;">{progress_text}</p>
    </div>
    <div class="content">
        <div class="progress">
            <div class="progress-bar" style="width: {progress_percent}%;"></div>
        </div>

        {items_html}

        <form action="{action_url}/project_add_item" method="POST" style="margin-top: 20px; border-top: 1px solid #e5e7eb; padding-top: 15px;">
            <input type="hidden" name="project_id" value="{project_id}">
            <label style="font-weight: bold; color: #374151;">Add new item:</label>
            <input type="text" name="new_item" placeholder="Enter new to-do item..." required>
            <button type="submit" class="btn btn-primary" style="margin-top: 10px; width: 100%; border: none; cursor: pointer;">➕ Add Item</button>
        </form>
    </div>
</body>
</html>"""


def handle_view_project(project_id):
    """Display project with all items"""
    try:
        project = tm.get_project_with_items(project_id, include_completed=True)
        if not project:
            return ERROR_TEMPLATE.format(error="Project not found")

        project_name = project.get('name', 'Project')
        items = project.get('items', [])

        # Calculate progress
        total = len(items)
        completed = len([i for i in items if i['is_completed']])
        progress_percent = int((completed / total * 100) if total > 0 else 0)
        progress_text = f"{completed}/{total} completed ({progress_percent}%)"

        # Build items HTML
        items_html = ""
        for item in items:
            item_id = item['id']
            item_text = item['item_text']
            is_completed = item['is_completed']

            if is_completed:
                items_html += f'''
                <div class="item completed">
                    <a href="{ACTION_URL}?action=uncomplete_project_item&item_id={item_id}&project_id={project_id}" class="checkbox">✅</a>
                    <span>{item_text}</span>
                </div>'''
            else:
                items_html += f'''
                <div class="item">
                    <a href="{ACTION_URL}?action=complete_project_item&item_id={item_id}&project_id={project_id}" class="checkbox">☐</a>
                    <span>{item_text}</span>
                </div>'''

        if not items:
            items_html = '<p style="color: #6b7280; text-align: center;">No items yet. Add one below!</p>'

        return PROJECT_VIEW_TEMPLATE.format(
            project_name=project_name,
            project_id=project_id,
            progress_text=progress_text,
            progress_percent=progress_percent,
            items_html=items_html,
            action_url=ACTION_URL
        )

    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Error loading project: {str(e)}")


def handle_complete_project_item(item_id, project_id):
    """Mark a project item as complete and redirect back"""
    try:
        tm.complete_project_item(item_id)
        # Redirect back to project view
        return f'<script>window.location.href="{ACTION_URL}?action=view_project&project_id={project_id}";</script>'
    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Error completing item: {str(e)}")



def handle_add_project_item_form(project_id):
    """Show form to add new project item"""
    try:
        project = tm.get_project_with_items(project_id)
        if not project:
            return ERROR_TEMPLATE.format(error="Project not found")

        return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Add Item - {project.get('name', 'Project')}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px; max-width: 500px; margin: 0 auto; }}
        .card {{ background: white; padding: 25px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        input[type="text"] {{ width: 100%; padding: 15px; border: 2px solid #e5e7eb; border-radius: 8px; font-size: 16px; margin: 10px 0; }}
        .btn {{ display: block; width: 100%; padding: 15px; margin: 10px 0; border: none; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; }}
        .btn-primary {{ background: #8b5cf6; color: white; }}
        .btn-secondary {{ background: #e5e7eb; color: #374151; }}
    </style>
</head>
<body>
    <div class="card">
        <h2 style="margin-top: 0;">➕ Add to {project.get('name', 'Project')}</h2>
        <form action="{ACTION_URL}/project_add_item" method="POST">
            <input type="hidden" name="project_id" value="{project_id}">
            <input type="text" name="new_item" placeholder="Enter new to-do item..." required autofocus>
            <button type="submit" class="btn btn-primary">Add Item</button>
            <a href="{ACTION_URL}?action=view_project&project_id={project_id}" class="btn btn-secondary" style="text-align: center; text-decoration: none;">Cancel</a>
        </form>
    </div>
</body>
</html>'''
    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Error: {str(e)}")


def handle_complete_all_project_items(project_id):
    """Mark all items in a project as complete"""
    try:
        items = tm.get_project_items(project_id, include_completed=False)
        for item in items:
            tm.complete_project_item(item['id'])

        project = tm.supabase.table('projects').select('name').eq('id', project_id).execute()
        project_name = project.data[0]['name'] if project.data else 'Project'

        return SUCCESS_TEMPLATE.format(
            icon="✅",
            title="All Items Completed!",
            task_title=project_name,
            message=f"Marked {len(items)} items as complete"
        )
    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Error: {str(e)}")


@app.route('/action/project_add_item', methods=['POST'])
def handle_project_add_item_submit():
    """Process adding a new item to a project"""
    project_id = request.form.get('project_id')
    new_item = request.form.get('new_item', '').strip()

    if not project_id or not new_item:
        return ERROR_TEMPLATE.format(error="Missing project_id or item text")

    try:
        tm.add_project_item(project_id, new_item, source='manual')

        # Redirect back to project view
        return f'<script>window.location.href="{ACTION_URL}?action=view_project&project_id={project_id}";</script>'

    except Exception as e:
        return ERROR_TEMPLATE.format(error=f"Error adding item: {str(e)}")


# ============================================
# MAIN
# ============================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
