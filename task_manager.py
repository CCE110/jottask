"""
Task Manager - Enhanced with Notes & Project Status Support
Updated: November 28, 2025
"""

import os
from datetime import datetime, date, timedelta
from supabase import create_client, Client
import pytz

class TaskManager:
    def __init__(self):
        url = os.getenv('SUPABASE_URL')
        key = os.getenv('SUPABASE_KEY')
        
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
        
        self.supabase: Client = create_client(url, key)
        self.aest = pytz.timezone('Australia/Brisbane')
        
        # Cache project statuses on init
        self.statuses = self.load_project_statuses()
        print(f"📊 Loaded {len(self.statuses)} project statuses")
    
    # ========================================
    # PROJECT STATUS METHODS
    # ========================================
    
    def load_project_statuses(self):
        """Load all project statuses into memory (graceful if table doesn't exist)"""
        try:
            result = self.supabase.table('project_statuses')\
                .select('*')\
                .order('display_order')\
                .execute()
            return {s['id']: s for s in result.data}
        except Exception as e:
            # Table might not exist yet - that's OK, system still works
            print(f"⚠️ Could not load statuses (run database migration): {e}")
            return {}
    
    def statuses_available(self):
        """Check if project statuses are configured"""
        return len(self.statuses) > 0
    
    def get_status_by_name(self, name):
        """Get status by name (case-insensitive)"""
        for status in self.statuses.values():
            if status['name'].lower() == name.lower():
                return status
        return None
    
    def get_default_status_id(self):
        """Get the first status (Remember to Callback)"""
        for status in self.statuses.values():
            if status['display_order'] == 1:
                return status['id']
        # Fallback: return first status
        if self.statuses:
            return list(self.statuses.keys())[0]
        return None
    
    def get_next_status(self, current_status_id):
        """Get the next status in workflow"""
        if current_status_id not in self.statuses:
            return None
        
        current_order = self.statuses[current_status_id]['display_order']
        
        for status in self.statuses.values():
            if status['display_order'] == current_order + 1:
                return status
        return None  # Already at last status
    
    def get_previous_status(self, current_status_id):
        """Get the previous status in workflow"""
        if current_status_id not in self.statuses:
            return None
        
        current_order = self.statuses[current_status_id]['display_order']
        
        for status in self.statuses.values():
            if status['display_order'] == current_order - 1:
                return status
        return None  # Already at first status
    
    def update_task_status(self, task_id, new_status_id):
        """Update task's project status"""
        try:
            result = self.supabase.table('tasks')\
                .update({'project_status_id': new_status_id})\
                .eq('id', task_id)\
                .execute()
            
            if result.data:
                status = self.statuses.get(new_status_id, {})
                print(f"✅ Task status updated to: {status.get('name', 'Unknown')}")
                return True
            return False
        except Exception as e:
            print(f"❌ Error updating status: {e}")
            return False
    
    def move_task_to_next_status(self, task_id):
        """Move task to next stage in workflow"""
        task = self.get_task(task_id)
        if not task:
            return False, "Task not found"
        
        next_status = self.get_next_status(task.get('project_status_id'))
        if not next_status:
            return False, "Already at final status"
        
        success = self.update_task_status(task_id, next_status['id'])
        return success, next_status['name'] if success else "Update failed"
    
    def move_task_to_previous_status(self, task_id):
        """Move task to previous stage in workflow"""
        task = self.get_task(task_id)
        if not task:
            return False, "Task not found"
        
        prev_status = self.get_previous_status(task.get('project_status_id'))
        if not prev_status:
            return False, "Already at first status"
        
        success = self.update_task_status(task_id, prev_status['id'])
        return success, prev_status['name'] if success else "Update failed"
    
    # ========================================
    # TASK NOTES METHODS
    # ========================================
    
    def add_note(self, task_id, content, source='manual', email_subject=None, 
                 email_from=None, email_date=None):
        """Add a note to a task (graceful if table doesn't exist)"""
        try:
            note_data = {
                'task_id': task_id,
                'content': content,
                'source': source,
                'created_by': 'system'
            }
            
            if email_subject:
                note_data['source_email_subject'] = email_subject
            if email_from:
                note_data['source_email_from'] = email_from
            if email_date:
                note_data['source_email_date'] = email_date
            
            result = self.supabase.table('task_notes')\
                .insert(note_data)\
                .execute()
            
            if result.data:
                print(f"📝 Note added to task (source: {source})")
                return result.data[0]
            return None
        except Exception as e:
            # Table might not exist yet - log but don't crash
            if 'task_notes' in str(e):
                print(f"⚠️ Notes table not ready (run database migration)")
            else:
                print(f"❌ Error adding note: {e}")
            return None
    
    def get_task_notes(self, task_id, limit=10):
        """Get notes for a task (graceful if table doesn't exist)"""
        try:
            result = self.supabase.table('task_notes')\
                .select('*')\
                .eq('task_id', task_id)\
                .order('created_at', desc=True)\
                .limit(limit)\
                .execute()
            return result.data
        except Exception as e:
            # Table might not exist yet
            if 'task_notes' in str(e):
                return []  # Graceful fallback
            print(f"❌ Error getting notes: {e}")
            return []
    
    def get_all_task_notes(self, task_id):
        """Get ALL notes for a task (for AI summarization)"""
        try:
            result = self.supabase.table('task_notes')\
                .select('*')\
                .eq('task_id', task_id)\
                .order('created_at', desc=False)\
                .execute()
            return result.data
        except Exception as e:
            # Table might not exist yet
            return []
    
    # ========================================
    # CLIENT MATCHING METHODS
    # ========================================
    
    def find_existing_task_by_client(self, client_email=None, client_name=None, 
                                      project_name=None):
        """
        Find existing task by client identifiers.
        Priority: email > project_name > client_name
        Returns most recent non-closed task for this client.
        """
        try:
            # Try email match first (most reliable)
            if client_email:
                result = self.supabase.table('tasks')\
                    .select('*, project_statuses!inner(name)')\
                    .eq('client_email', client_email.lower())\
                    .neq('status', 'completed')\
                    .order('created_at', desc=True)\
                    .limit(1)\
                    .execute()
                
                if result.data:
                    print(f"🔗 Found existing task by email: {client_email}")
                    return result.data[0]
            
            # Try project name match
            if project_name:
                result = self.supabase.table('tasks')\
                    .select('*')\
                    .ilike('project_name', f'%{project_name}%')\
                    .neq('status', 'completed')\
                    .order('created_at', desc=True)\
                    .limit(1)\
                    .execute()
                
                if result.data:
                    print(f"🔗 Found existing task by project: {project_name}")
                    return result.data[0]
            
            # Try client name match (fuzzy)
            if client_name and len(client_name) > 2:
                result = self.supabase.table('tasks')\
                    .select('*')\
                    .ilike('client_name', f'%{client_name}%')\
                    .neq('status', 'completed')\
                    .order('created_at', desc=True)\
                    .limit(1)\
                    .execute()
                
                if result.data:
                    print(f"🔗 Found existing task by name: {client_name}")
                    return result.data[0]
            
            return None
            
        except Exception as e:
            print(f"⚠️ Error finding existing task: {e}")
            return None
    
    # ========================================
    # TASK CRUD METHODS (Enhanced)
    # ========================================
    
    def create_task(self, business_id, title, description=None, due_date=None,
                    due_time=None, priority='medium', is_meeting=False,
                    client_name=None, client_email=None, client_phone=None,
                    project_name=None, initial_note=None, note_source='manual'):
        """
        Create a new task with optional client info and initial note.
        Backwards compatible - works even without new database columns.
        """
        try:
            # Core task data (always works)
            task_data = {
                'business_id': business_id,
                'title': title,
                'description': description,
                'due_date': due_date or date.today().isoformat(),
                'due_time': due_time or '08:00:00',
                'priority': priority,
                'status': 'pending',
                'is_meeting': is_meeting
            }
            
            # Add project status if available
            if self.statuses_available():
                default_status = self.get_default_status_id()
                if default_status:
                    task_data['project_status_id'] = default_status
            
            # Add client info if provided (may fail if columns don't exist yet)
            try:
                if client_name:
                    task_data['client_name'] = client_name
                if client_email:
                    task_data['client_email'] = client_email.lower()
                if client_phone:
                    task_data['client_phone'] = client_phone
                if project_name:
                    task_data['project_name'] = project_name
            except Exception:
                pass  # Columns might not exist yet
            
            # Insert task
            result = self.supabase.table('tasks')\
                .insert(task_data)\
                .execute()
            
            if result.data:
                task = result.data[0]
                print(f"✅ Task created: {title}")
                
                # Add initial note if provided (may fail if table doesn't exist)
                if initial_note:
                    self.add_note(
                        task_id=task['id'],
                        content=initial_note,
                        source=note_source
                    )
                
                return task
            return None
            
        except Exception as e:
            print(f"❌ Error creating task: {e}")
            
            # Fallback: try with minimal data (backwards compatible)
            try:
                minimal_data = {
                    'business_id': business_id,
                    'title': title,
                    'description': description,
                    'due_date': due_date or date.today().isoformat(),
                    'due_time': due_time or '08:00:00',
                    'priority': priority,
                    'status': 'pending',
                    'is_meeting': is_meeting
                }
                result = self.supabase.table('tasks').insert(minimal_data).execute()
                if result.data:
                    print(f"✅ Task created (minimal mode): {title}")
                    return result.data[0]
            except Exception as e2:
                print(f"❌ Fallback also failed: {e2}")
            
            return None
    
    def get_task(self, task_id):
        """Get a single task by ID with status info"""
        try:
            result = self.supabase.table('tasks')\
                .select('*, project_statuses(*)')\
                .eq('id', task_id)\
                .single()\
                .execute()
            return result.data
        except Exception as e:
            print(f"❌ Error getting task: {e}")
            return None
    
    def get_task_with_notes(self, task_id, note_limit=10):
        """Get task with its recent notes"""
        task = self.get_task(task_id)
        if task:
            task['notes'] = self.get_task_notes(task_id, limit=note_limit)
        return task
    
    def get_pending_tasks_due_today(self):
        """Get all pending tasks due today with status info"""
        try:
            today = date.today().isoformat()
            result = self.supabase.table('tasks')\
                .select('*, project_statuses(*)')\
                .eq('status', 'pending')\
                .eq('due_date', today)\
                .order('due_time')\
                .execute()
            return result.data
        except Exception as e:
            print(f"❌ Error getting tasks: {e}")
            return []
    
    def complete_task(self, task_id):
        """Mark task as completed"""
        try:
            now = datetime.now(self.aest).isoformat()
            result = self.supabase.table('tasks')\
                .update({
                    'status': 'completed',
                    'completed_at': now
                })\
                .eq('id', task_id)\
                .execute()
            
            if result.data:
                # Add completion note
                self.add_note(
                    task_id=task_id,
                    content='Task marked as completed',
                    source='system'
                )
                print(f"✅ Task completed")
                return True
            return False
        except Exception as e:
            print(f"❌ Error completing task: {e}")
            return False
    
    def delay_task(self, task_id, hours=0, days=0):
        """Delay task by specified time"""
        try:
            task = self.get_task(task_id)
            if not task:
                return False
            
            # Parse current due date/time
            current_date = datetime.strptime(task['due_date'], '%Y-%m-%d')
            
            if task.get('due_time'):
                parts = task['due_time'].split(':')
                h, m = int(parts[0]), int(parts[1])
                s = int(float(parts[2])) if len(parts) > 2 else 0
                current_datetime = current_date.replace(hour=h, minute=m, second=s)
            else:
                current_datetime = current_date.replace(hour=8, minute=0, second=0)
            
            # Make timezone aware
            current_datetime = self.aest.localize(current_datetime)
            
            # Add delay
            new_datetime = current_datetime + timedelta(hours=hours, days=days)
            
            # Update task
            result = self.supabase.table('tasks')\
                .update({
                    'due_date': new_datetime.date().isoformat(),
                    'due_time': new_datetime.strftime('%H:%M:%S')
                })\
                .eq('id', task_id)\
                .execute()
            
            if result.data:
                # Add note about delay
                delay_desc = f"{hours} hour(s)" if hours else f"{days} day(s)"
                self.add_note(
                    task_id=task_id,
                    content=f'Task delayed by {delay_desc}. New due: {new_datetime.strftime("%I:%M %p %d/%m/%Y")}',
                    source='system'
                )
                print(f"⏰ Task delayed to {new_datetime.strftime('%I:%M %p')}")
                return True
            return False
        except Exception as e:
            print(f"❌ Error delaying task: {e}")
            return False
    
    def update_task_client_info(self, task_id, client_name=None, client_email=None,
                                 client_phone=None, project_name=None):
        """Update client information on existing task"""
        try:
            update_data = {}
            if client_name:
                update_data['client_name'] = client_name
            if client_email:
                update_data['client_email'] = client_email.lower()
            if client_phone:
                update_data['client_phone'] = client_phone
            if project_name:
                update_data['project_name'] = project_name
            
            if not update_data:
                return True
            
            result = self.supabase.table('tasks')\
                .update(update_data)\
                .eq('id', task_id)\
                .execute()
            
            return bool(result.data)
        except Exception as e:
            print(f"❌ Error updating client info: {e}")
            return False


# ========================================
# STANDALONE TESTING
# ========================================

    # ========================================
    # CHECKLIST METHODS
    # ========================================
    
    def get_checklist_items(self, task_id, include_completed=False):
        """Get checklist items for a task"""
        try:
            query = self.supabase.table('task_checklist_items')\
                .select('*')\
                .eq('task_id', task_id)\
                .order('created_at')
            
            if not include_completed:
                query = query.eq('is_completed', False)
            
            result = query.execute()
            return result.data
        except Exception as e:
            print(f"Error getting checklist: {e}")
            return []
    
    def add_checklist_item(self, task_id, item_text):
        """Add a new checklist item"""
        try:
            # Check if item already exists (avoid duplicates)
            existing = self.supabase.table('task_checklist_items')\
                .select('id')\
                .eq('task_id', task_id)\
                .ilike('item_text', item_text)\
                .execute()
            
            if existing.data:
                return existing.data[0]  # Return existing item
            
            result = self.supabase.table('task_checklist_items').insert({
                'task_id': task_id,
                'item_text': item_text
            }).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error adding checklist item: {e}")
            return None
    
    def complete_checklist_item(self, item_id):
        """Mark a checklist item as completed"""
        try:
            from datetime import datetime
            result = self.supabase.table('task_checklist_items').update({
                'is_completed': True,
                'completed_at': datetime.now().isoformat()
            }).eq('id', item_id).execute()
            return bool(result.data)
        except Exception as e:
            print(f"Error completing checklist item: {e}")
            return False
    
    def bulk_update_checklist(self, task_id, completed_item_ids):
        """Mark multiple items as completed, others as incomplete"""
        try:
            from datetime import datetime
            
            # Get all items for this task
            all_items = self.supabase.table('task_checklist_items')\
                .select('id')\
                .eq('task_id', task_id)\
                .execute()
            
            for item in all_items.data:
                if item['id'] in completed_item_ids:
                    # Mark as completed
                    self.supabase.table('task_checklist_items').update({
                        'is_completed': True,
                        'completed_at': datetime.now().isoformat()
                    }).eq('id', item['id']).execute()
                else:
                    # Mark as incomplete (in case it was unchecked)
                    self.supabase.table('task_checklist_items').update({
                        'is_completed': False,
                        'completed_at': None
                    }).eq('id', item['id']).execute()
            
            return True
        except Exception as e:
            print(f"Error bulk updating checklist: {e}")
            return False

    # ========================================
    # PROJECT METHODS
    # ========================================

    def find_project_by_name(self, name, business_id=None):
        """Find a project by name (case-insensitive partial match)"""
        try:
            query = self.supabase.table('projects')\
                .select('*')\
                .ilike('name', f'%{name}%')\
                .neq('status', 'archived')

            if business_id:
                query = query.eq('business_id', business_id)

            result = query.order('created_at', desc=True).limit(1).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Error finding project: {e}")
            return None

    def create_project(self, name, description=None, business_id=None):
        """Create a new project"""
        try:
            project_data = {
                'name': name,
                'description': description,
                'status': 'active'
            }

            if business_id:
                project_data['business_id'] = business_id

            result = self.supabase.table('projects').insert(project_data).execute()

            if result.data:
                print(f"📁 Project created: {name}")
                return result.data[0]
            return None
        except Exception as e:
            print(f"Error creating project: {e}")
            return None

    def get_or_create_project(self, name, business_id=None):
        """Find existing project or create new one"""
        # Try to find existing
        project = self.find_project_by_name(name, business_id)
        if project:
            print(f"📁 Found existing project: {project['name']}")
            return project

        # Create new
        return self.create_project(name, business_id=business_id)

    def add_project_item(self, project_id, item_text, source='email', source_email_subject=None):
        """Add a to-do item to a project"""
        try:
            # Check if item already exists (avoid duplicates)
            existing = self.supabase.table('project_items')\
                .select('id')\
                .eq('project_id', project_id)\
                .ilike('item_text', item_text.strip())\
                .execute()

            if existing.data:
                print(f"   ⏭️ Item already exists: {item_text[:40]}")
                return existing.data[0]

            # Get next display order
            order_result = self.supabase.table('project_items')\
                .select('display_order')\
                .eq('project_id', project_id)\
                .order('display_order', desc=True)\
                .limit(1)\
                .execute()

            next_order = (order_result.data[0]['display_order'] + 1) if order_result.data else 0

            item_data = {
                'project_id': project_id,
                'item_text': item_text.strip(),
                'display_order': next_order,
                'source': source
            }

            if source_email_subject:
                item_data['source_email_subject'] = source_email_subject

            result = self.supabase.table('project_items').insert(item_data).execute()

            if result.data:
                print(f"   ✅ Added item: {item_text[:50]}")
                return result.data[0]
            return None
        except Exception as e:
            print(f"Error adding project item: {e}")
            return None

    def get_project_items(self, project_id, include_completed=False):
        """Get all items for a project"""
        try:
            query = self.supabase.table('project_items')\
                .select('*')\
                .eq('project_id', project_id)\
                .order('display_order')

            if not include_completed:
                query = query.eq('is_completed', False)

            result = query.execute()
            return result.data
        except Exception as e:
            print(f"Error getting project items: {e}")
            return []

    def get_project_with_items(self, project_id, include_completed=True):
        """Get project with all its items"""
        try:
            project = self.supabase.table('projects')\
                .select('*')\
                .eq('id', project_id)\
                .single()\
                .execute()

            if project.data:
                project.data['items'] = self.get_project_items(project_id, include_completed)
                return project.data
            return None
        except Exception as e:
            print(f"Error getting project: {e}")
            return None

    def get_active_projects(self, business_id=None):
        """Get all active projects with their items"""
        try:
            query = self.supabase.table('projects')\
                .select('*')\
                .eq('status', 'active')\
                .order('created_at', desc=True)

            if business_id:
                query = query.eq('business_id', business_id)

            result = query.execute()

            projects = result.data
            for project in projects:
                project['items'] = self.get_project_items(project['id'], include_completed=True)
                # Calculate progress
                total = len(project['items'])
                completed = len([i for i in project['items'] if i['is_completed']])
                project['progress'] = {
                    'total': total,
                    'completed': completed,
                    'percent': int((completed / total * 100) if total > 0 else 0)
                }

            return projects
        except Exception as e:
            print(f"Error getting active projects: {e}")
            return []

    def complete_project_item(self, item_id):
        """Mark a project item as completed"""
        try:
            result = self.supabase.table('project_items').update({
                'is_completed': True,
                'completed_at': datetime.now(self.aest).isoformat()
            }).eq('id', item_id).execute()
            return bool(result.data)
        except Exception as e:
            print(f"Error completing project item: {e}")
            return False

    def uncomplete_project_item(self, item_id):
        """Mark a project item as not completed"""
        try:
            result = self.supabase.table('project_items').update({
                'is_completed': False,
                'completed_at': None
            }).eq('id', item_id).execute()
            return bool(result.data)
        except Exception as e:
            print(f"Error uncompleting project item: {e}")
            return False

    def bulk_update_project_items(self, project_id, completed_item_ids):
        """Update completion status for multiple items"""
        try:
            all_items = self.get_project_items(project_id, include_completed=True)

            for item in all_items:
                if item['id'] in completed_item_ids:
                    if not item['is_completed']:
                        self.complete_project_item(item['id'])
                else:
                    if item['is_completed']:
                        self.uncomplete_project_item(item['id'])

            return True
        except Exception as e:
            print(f"Error bulk updating project items: {e}")
            return False


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    tm = TaskManager()
    
    print("\n📊 Project Statuses:")
    for status in sorted(tm.statuses.values(), key=lambda x: x['display_order']):
        print(f"  {status['emoji']} {status['name']} (order: {status['display_order']})")
    
    print("\n📋 Pending Tasks Today:")
    tasks = tm.get_pending_tasks_due_today()
    for task in tasks:
        status = task.get('project_statuses', {})
        print(f"  - {task['title']}")
        print(f"    Status: {status.get('emoji', '📋')} {status.get('name', 'Unknown')}")
        print(f"    Client: {task.get('client_name', 'N/A')}")

