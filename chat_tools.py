"""
Jottask Chat Tools — Tool handlers for AI chat interface
Each function receives (tool_input, user_id, supabase) and returns a dict result.
All queries include .eq('user_id', user_id) for data isolation.
"""

from datetime import datetime, date, timedelta
import pytz

AEST = pytz.timezone('Australia/Brisbane')


def create_task(tool_input, user_id, supabase):
    """Create a new task for the user."""
    title = tool_input.get('title')
    if not title:
        return {'success': False, 'error': 'Title is required'}

    task_data = {
        'user_id': user_id,
        'title': title,
        'description': tool_input.get('description'),
        'due_date': tool_input.get('due_date', date.today().isoformat()),
        'due_time': tool_input.get('due_time', '09:00') + ':00' if tool_input.get('due_time') else '09:00:00',
        'priority': tool_input.get('priority', 'medium'),
        'status': 'pending',
        'client_name': tool_input.get('client_name'),
    }

    try:
        result = supabase.table('tasks').insert(task_data).execute()
        if result.data:
            task = result.data[0]
            return {
                'success': True,
                'task': {
                    'id': task['id'],
                    'title': task['title'],
                    'due_date': task['due_date'],
                    'due_time': task.get('due_time', '')[:5],
                    'priority': task['priority'],
                    'client_name': task.get('client_name'),
                }
            }
        return {'success': False, 'error': 'Failed to create task'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def list_tasks(tool_input, user_id, supabase):
    """List tasks with optional filters."""
    status_filter = tool_input.get('status', 'pending')
    date_filter = tool_input.get('date_filter', 'all')
    today = date.today()

    try:
        query = supabase.table('tasks') \
            .select('id, title, due_date, due_time, priority, status, client_name, created_at') \
            .eq('user_id', user_id)

        if status_filter != 'all':
            query = query.eq('status', status_filter)

        if date_filter == 'today':
            query = query.eq('due_date', today.isoformat())
        elif date_filter == 'tomorrow':
            tomorrow = (today + timedelta(days=1)).isoformat()
            query = query.eq('due_date', tomorrow)
        elif date_filter == 'this_week':
            week_end = (today + timedelta(days=(6 - today.weekday()))).isoformat()
            query = query.gte('due_date', today.isoformat()).lte('due_date', week_end)
        elif date_filter == 'overdue':
            query = query.lt('due_date', today.isoformat()).eq('status', 'pending')

        query = query.order('due_date').order('due_time').limit(25)
        result = query.execute()

        tasks = []
        for t in result.data:
            tasks.append({
                'id': t['id'],
                'title': t['title'],
                'due_date': t['due_date'],
                'due_time': (t.get('due_time') or '')[:5],
                'priority': t['priority'],
                'status': t['status'],
                'client_name': t.get('client_name'),
            })

        return {'success': True, 'tasks': tasks, 'count': len(tasks)}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def complete_task(tool_input, user_id, supabase):
    """Complete a task by ID or title search."""
    task_id = tool_input.get('task_id')
    title_search = tool_input.get('task_title_search')

    try:
        if not task_id and title_search:
            # Fuzzy search for the task
            result = supabase.table('tasks') \
                .select('id, title, due_date, client_name') \
                .eq('user_id', user_id) \
                .eq('status', 'pending') \
                .ilike('title', f'%{title_search}%') \
                .limit(5) \
                .execute()

            if not result.data:
                return {'success': False, 'error': f'No pending task found matching "{title_search}"'}
            if len(result.data) > 1:
                candidates = [{'id': t['id'], 'title': t['title'], 'due_date': t['due_date']} for t in result.data]
                return {'success': False, 'ambiguous': True, 'candidates': candidates,
                        'error': f'Multiple tasks match "{title_search}". Please specify which one.'}
            task_id = result.data[0]['id']

        if not task_id:
            return {'success': False, 'error': 'Provide task_id or task_title_search'}

        now = datetime.now(AEST).isoformat()
        result = supabase.table('tasks') \
            .update({'status': 'completed', 'completed_at': now}) \
            .eq('id', task_id) \
            .eq('user_id', user_id) \
            .execute()

        if result.data:
            task = result.data[0]
            return {'success': True, 'task': {'id': task['id'], 'title': task['title'], 'status': 'completed'}}
        return {'success': False, 'error': 'Task not found or not owned by you'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def delay_task(tool_input, user_id, supabase):
    """Reschedule a task by hours/days or to a specific date/time."""
    task_id = tool_input.get('task_id')
    title_search = tool_input.get('task_title_search')
    hours = tool_input.get('hours', 0)
    days = tool_input.get('days', 0)
    new_date = tool_input.get('new_date')
    new_time = tool_input.get('new_time')

    try:
        if not task_id and title_search:
            result = supabase.table('tasks') \
                .select('id, title, due_date, due_time, client_name') \
                .eq('user_id', user_id) \
                .eq('status', 'pending') \
                .ilike('title', f'%{title_search}%') \
                .limit(5) \
                .execute()

            if not result.data:
                return {'success': False, 'error': f'No pending task found matching "{title_search}"'}
            if len(result.data) > 1:
                candidates = [{'id': t['id'], 'title': t['title'], 'due_date': t['due_date']} for t in result.data]
                return {'success': False, 'ambiguous': True, 'candidates': candidates,
                        'error': f'Multiple tasks match "{title_search}". Please specify which one.'}
            task_id = result.data[0]['id']

        if not task_id:
            return {'success': False, 'error': 'Provide task_id or task_title_search'}

        # Get current task
        task_result = supabase.table('tasks') \
            .select('id, title, due_date, due_time') \
            .eq('id', task_id) \
            .eq('user_id', user_id) \
            .single() \
            .execute()

        if not task_result.data:
            return {'success': False, 'error': 'Task not found'}

        task = task_result.data
        update_data = {'reminder_sent_at': None}

        if new_date:
            update_data['due_date'] = new_date
            if new_time:
                update_data['due_time'] = new_time + ':00' if len(new_time) == 5 else new_time
        elif hours or days:
            current_date = datetime.strptime(task['due_date'], '%Y-%m-%d')
            current_time_str = task.get('due_time') or '09:00:00'
            parts = current_time_str.split(':')
            h, m = int(parts[0]), int(parts[1])
            current_dt = current_date.replace(hour=h, minute=m)
            new_dt = current_dt + timedelta(hours=hours, days=days)
            update_data['due_date'] = new_dt.date().isoformat()
            update_data['due_time'] = new_dt.strftime('%H:%M:%S')
        else:
            return {'success': False, 'error': 'Provide hours, days, or new_date to reschedule'}

        result = supabase.table('tasks') \
            .update(update_data) \
            .eq('id', task_id) \
            .eq('user_id', user_id) \
            .execute()

        if result.data:
            updated = result.data[0]
            return {
                'success': True,
                'task': {
                    'id': updated['id'],
                    'title': updated['title'],
                    'new_due_date': updated['due_date'],
                    'new_due_time': (updated.get('due_time') or '')[:5],
                }
            }
        return {'success': False, 'error': 'Failed to update task'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def get_overdue_tasks(tool_input, user_id, supabase):
    """Get all pending tasks past their due date."""
    today = date.today().isoformat()

    try:
        result = supabase.table('tasks') \
            .select('id, title, due_date, due_time, priority, client_name') \
            .eq('user_id', user_id) \
            .eq('status', 'pending') \
            .lt('due_date', today) \
            .order('due_date') \
            .limit(25) \
            .execute()

        tasks = []
        for t in result.data:
            tasks.append({
                'id': t['id'],
                'title': t['title'],
                'due_date': t['due_date'],
                'due_time': (t.get('due_time') or '')[:5],
                'priority': t['priority'],
                'client_name': t.get('client_name'),
            })

        return {'success': True, 'tasks': tasks, 'count': len(tasks)}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def get_todays_tasks(tool_input, user_id, supabase):
    """Get all pending tasks due today."""
    today = date.today().isoformat()

    try:
        result = supabase.table('tasks') \
            .select('id, title, due_date, due_time, priority, client_name, status') \
            .eq('user_id', user_id) \
            .eq('due_date', today) \
            .eq('status', 'pending') \
            .order('due_time') \
            .execute()

        tasks = []
        for t in result.data:
            tasks.append({
                'id': t['id'],
                'title': t['title'],
                'due_time': (t.get('due_time') or '')[:5],
                'priority': t['priority'],
                'client_name': t.get('client_name'),
            })

        return {'success': True, 'tasks': tasks, 'count': len(tasks)}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def search_tasks(tool_input, user_id, supabase):
    """Search tasks by keyword in title, description, or client_name."""
    query_text = tool_input.get('query')
    if not query_text:
        return {'success': False, 'error': 'Search query is required'}

    include_completed = tool_input.get('include_completed', False)

    try:
        q = supabase.table('tasks') \
            .select('id, title, due_date, due_time, priority, status, client_name, description') \
            .eq('user_id', user_id) \
            .ilike('title', f'%{query_text}%')

        if not include_completed:
            q = q.eq('status', 'pending')

        q = q.order('due_date', desc=True).limit(15)
        title_results = q.execute()

        # Also search by client_name
        q2 = supabase.table('tasks') \
            .select('id, title, due_date, due_time, priority, status, client_name, description') \
            .eq('user_id', user_id) \
            .ilike('client_name', f'%{query_text}%')

        if not include_completed:
            q2 = q2.eq('status', 'pending')

        q2 = q2.order('due_date', desc=True).limit(15)
        client_results = q2.execute()

        # Merge and dedupe
        seen_ids = set()
        tasks = []
        for t in title_results.data + client_results.data:
            if t['id'] not in seen_ids:
                seen_ids.add(t['id'])
                tasks.append({
                    'id': t['id'],
                    'title': t['title'],
                    'due_date': t['due_date'],
                    'due_time': (t.get('due_time') or '')[:5],
                    'priority': t['priority'],
                    'status': t['status'],
                    'client_name': t.get('client_name'),
                })

        return {'success': True, 'tasks': tasks, 'count': len(tasks)}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def get_task_details(tool_input, user_id, supabase):
    """Get full task details with notes and checklist items."""
    task_id = tool_input.get('task_id')
    if not task_id:
        return {'success': False, 'error': 'task_id is required'}

    try:
        result = supabase.table('tasks') \
            .select('*') \
            .eq('id', task_id) \
            .eq('user_id', user_id) \
            .single() \
            .execute()

        if not result.data:
            return {'success': False, 'error': 'Task not found'}

        task = result.data

        # Get notes
        notes = []
        try:
            notes_result = supabase.table('task_notes') \
                .select('content, source, created_at') \
                .eq('task_id', task_id) \
                .order('created_at', desc=True) \
                .limit(10) \
                .execute()
            notes = notes_result.data
        except Exception:
            pass

        # Get checklist items
        checklist = []
        try:
            checklist_result = supabase.table('task_checklist_items') \
                .select('id, item_text, is_completed') \
                .eq('task_id', task_id) \
                .order('created_at') \
                .execute()
            checklist = checklist_result.data
        except Exception:
            pass

        return {
            'success': True,
            'task': {
                'id': task['id'],
                'title': task['title'],
                'description': task.get('description'),
                'due_date': task['due_date'],
                'due_time': (task.get('due_time') or '')[:5],
                'priority': task['priority'],
                'status': task['status'],
                'client_name': task.get('client_name'),
                'created_at': task.get('created_at'),
                'completed_at': task.get('completed_at'),
            },
            'notes': notes,
            'checklist': checklist,
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def add_note_to_task(tool_input, user_id, supabase):
    """Add a note to a task by ID or title search."""
    task_id = tool_input.get('task_id')
    title_search = tool_input.get('task_title_search')
    content = tool_input.get('content')

    if not content:
        return {'success': False, 'error': 'Note content is required'}

    try:
        if not task_id and title_search:
            result = supabase.table('tasks') \
                .select('id, title') \
                .eq('user_id', user_id) \
                .eq('status', 'pending') \
                .ilike('title', f'%{title_search}%') \
                .limit(5) \
                .execute()

            if not result.data:
                return {'success': False, 'error': f'No pending task found matching "{title_search}"'}
            if len(result.data) > 1:
                candidates = [{'id': t['id'], 'title': t['title']} for t in result.data]
                return {'success': False, 'ambiguous': True, 'candidates': candidates,
                        'error': f'Multiple tasks match "{title_search}". Please specify which one.'}
            task_id = result.data[0]['id']

        if not task_id:
            return {'success': False, 'error': 'Provide task_id or task_title_search'}

        # Verify task belongs to user
        task_check = supabase.table('tasks') \
            .select('id, title') \
            .eq('id', task_id) \
            .eq('user_id', user_id) \
            .execute()

        if not task_check.data:
            return {'success': False, 'error': 'Task not found or not owned by you'}

        note_data = {
            'task_id': task_id,
            'content': content,
            'source': 'chat',
            'created_by': 'user',
        }

        result = supabase.table('task_notes').insert(note_data).execute()
        if result.data:
            return {
                'success': True,
                'task_title': task_check.data[0]['title'],
                'note_id': result.data[0]['id'],
            }
        return {'success': False, 'error': 'Failed to add note'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# Registry of all tool handlers
TOOL_HANDLERS = {
    'create_task': create_task,
    'list_tasks': list_tasks,
    'complete_task': complete_task,
    'delay_task': delay_task,
    'get_overdue_tasks': get_overdue_tasks,
    'get_todays_tasks': get_todays_tasks,
    'search_tasks': search_tasks,
    'get_task_details': get_task_details,
    'add_note_to_task': add_note_to_task,
}
