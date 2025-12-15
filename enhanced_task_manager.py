"""
Enhanced Task Manager - AI Summarization & Rich Email Templates
Updated: November 28, 2025
"""

import os
import requests
import json
from datetime import datetime, date, timedelta
from anthropic import Anthropic
import pytz

class EnhancedTaskManager:
    def __init__(self, task_manager=None):
        self.anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        self.resend_api_key = os.getenv('RESEND_API_KEY')
        self.from_email = 'rob@cloudcleanenergy.com.au'
        self.aest = pytz.timezone('Australia/Brisbane')
        
        # Import TaskManager if not provided
        if task_manager:
            self.tm = task_manager
        else:
            from task_manager import TaskManager
            self.tm = TaskManager()
    
    # ========================================
    # AI SUMMARIZATION
    # ========================================
    
    def summarize_notes(self, notes, max_tokens=300):
        """
        Use Claude AI to create a concise summary of task notes.
        Returns a brief, actionable summary for the reminder email.
        """
        if not notes:
            return "No notes recorded yet."
        
        # Build notes text for AI
        notes_text = ""
        for note in notes:
            source = note.get('source', 'manual')
            created = note.get('created_at', '')[:10]
            content = note.get('content', '')
            
            if source == 'email':
                subject = note.get('source_email_subject', 'Email')
                notes_text += f"[{created}] Email - {subject}: {content}\n"
            else:
                notes_text += f"[{created}] {source}: {content}\n"
        
        try:
            response = self.anthropic.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                messages=[{
                    "role": "user",
                    "content": f"""Summarize these task notes in 2-3 sentences. Focus on:
1. Key client requests or requirements
2. Current status/progress
3. Next actions needed

Be concise and actionable. Use bullet points only if there are distinct items.

NOTES:
{notes_text}

SUMMARY:"""
                }]
            )
            
            summary = response.content[0].text.strip()
            print(f"🤖 AI Summary generated ({len(summary)} chars)")
            return summary
            
        except Exception as e:
            print(f"⚠️ AI summarization failed: {e}")
            # Fallback: return last note content
            if notes:
                return f"Latest: {notes[0].get('content', '')[:200]}"
            return "Unable to generate summary."
    
    def extract_action_items(self, notes, task_title):
        """
        Use Claude AI to extract actionable to-do items from task notes.
        Returns a list of action item strings.
        """
        if not notes:
            return []
        
        # Build notes text for AI
        notes_text = ""
        for note in notes:
            source = note.get('source', 'manual')
            created = note.get('created_at', '')[:10]
            content = note.get('content', '')
            notes_text += f"[{created}] {content}\n"
        
        try:
            response = self.anthropic.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": f"""Extract specific action items from these task notes.

TASK: {task_title}

NOTES:
{notes_text}

Return ONLY a JSON array of action item strings. Each item should be:
- A specific, actionable task (start with a verb)
- Concise (under 60 characters)
- Something that can be checked off when done

Example format:
["Call client to confirm appointment", "Send quote via email", "Order equipment from supplier"]

If no clear action items, return: []

ACTION ITEMS (JSON array only):"""
                }]
            )
            
            result = response.content[0].text.strip()
            
            # Parse JSON response
            import json
            # Clean up response if needed
            if result.startswith('```'):
                result = result.split('```')[1]
                if result.startswith('json'):
                    result = result[4:]
            result = result.strip()
            
            items = json.loads(result)
            if isinstance(items, list):
                print(f"📋 Extracted {len(items)} action items")
                return items[:10]  # Max 10 items
            return []
            
        except Exception as e:
            print(f"⚠️ Action item extraction failed: {e}")
            return []
    
    def sync_checklist_items(self, task_id, action_items):
        """
        Sync extracted action items with database checklist.
        Adds new items, preserves existing ones (including completed status).
        """
        for item_text in action_items:
            self.tm.add_checklist_item(task_id, item_text)
    
    # ========================================
    # EMAIL SENDING (Resend API)
    # ========================================
    
    def send_html_email(self, to_email, subject, html_content, plain_text):
        """Send email via Resend API"""
        print(f"📧 Sending: '{subject[:50]}...' to {to_email}")
        
        try:
            response = requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {self.resend_api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'from': self.from_email,
                    'to': [to_email],
                    'subject': subject,
                    'html': html_content,
                    'text': plain_text
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Email sent! ID: {result.get('id', 'unknown')}")
                return True
            else:
                print(f"❌ Email failed ({response.status_code}): {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Email exception: {e}")
            return False
    
    # ========================================
    # ENHANCED REMINDER EMAIL
    # ========================================
    
    def send_task_reminder(self, task, due_time, action_url):
        """
        Send enhanced reminder email with:
        - AI summary of notes
        - Last 10 notes history
        - Project status with color
        - Status change buttons
        """
        task_id = task['id']
        
        # Get notes and generate AI summary
        notes = self.tm.get_task_notes(task_id, limit=10)
        all_notes = self.tm.get_all_task_notes(task_id)
        ai_summary = self.summarize_notes(all_notes)
        
        # Get status info
        status_info = task.get('project_statuses') or {}
        status_name = status_info.get('name', 'Unknown')
        status_color = status_info.get('color', '#6b7280')
        status_emoji = status_info.get('emoji', '📋')
        
        # Get client info
        client_name = task.get('client_name', 'Unknown Client')
        client_email = task.get('client_email', '')
        project_name = task.get('project_name', task.get('title', 'Project'))
        
        # Build notes HTML
        notes_html = self._build_notes_html(notes)
        
        # Extract and sync action items from notes
        action_items = self.extract_action_items(all_notes, task['title'])
        if action_items:
            self.sync_checklist_items(task_id, action_items)
        
        # Get incomplete checklist items
        checklist_items = self.tm.get_checklist_items(task_id, include_completed=False)
        checklist_html = self._build_checklist_html(task_id, checklist_items, action_url)
        
        # Build status buttons HTML
        status_buttons_html = self._build_status_buttons_html(task_id, action_url)
        
        # Build complete HTML email
        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
             padding: 20px; 
             max-width: 650px; 
             margin: 0 auto;
             background-color: #f9fafb;">

<!-- Header with Status -->
<div style="background: linear-gradient(135deg, {status_color}22, {status_color}11); 
            border-left: 4px solid {status_color}; 
            padding: 20px; 
            margin-bottom: 20px; 
            border-radius: 8px;">
    
    <div style="display: flex; justify-content: space-between; align-items: center;">
        <h2 style="color: #111827; margin: 0; font-size: 20px;">
            ⏰ Task Reminder
        </h2>
        <span style="background: {status_color}; 
                     color: white; 
                     padding: 6px 12px; 
                     border-radius: 20px; 
                     font-size: 12px;
                     font-weight: 600;">
            {status_emoji} {status_name}
        </span>
    </div>
</div>

<!-- Task Title & Client -->
<div style="background: white; 
            padding: 20px; 
            border-radius: 8px; 
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
    
    <div style="font-size: 22px; 
                font-weight: bold; 
                color: #111827;
                margin-bottom: 10px;">
        {task['title']}
    </div>
    
    <div style="display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 15px;">
        <div style="color: #6b7280; font-size: 14px;">
            <strong>👤 Client:</strong> {client_name}
            {f'<br><span style="color: #9ca3af;">{client_email}</span>' if client_email else ''}
        </div>
        <div style="color: #ef4444; font-size: 14px;">
            <strong>⏰ Due:</strong> {due_time.strftime('%I:%M %p AEST')}
        </div>
    </div>
    
    {f'''<div style="color: #374151; font-size: 14px;">
        <strong>📁 Project:</strong> {project_name}
    </div>''' if project_name else ''}
</div>

<!-- AI Summary -->
<div style="background: #eff6ff; 
            border: 1px solid #bfdbfe;
            padding: 20px; 
            border-radius: 8px; 
            margin-bottom: 20px;">
    
    <div style="font-weight: 600; 
                color: #1e40af; 
                margin-bottom: 10px;
                font-size: 14px;">
        🤖 AI Summary
    </div>
    <div style="color: #1e3a8a; 
                font-size: 15px; 
                line-height: 1.6;">
        {ai_summary}
    </div>
</div>

<!-- Notes History -->
{notes_html}

<!-- Checklist -->
{checklist_html}

<!-- Action Buttons -->
<div style="background: white; 
            padding: 20px; 
            border-radius: 8px; 
            margin-bottom: 15px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
    
    <div style="font-weight: 600; 
                color: #374151; 
                margin-bottom: 15px;
                font-size: 14px;">
        ⚡ Quick Actions
    </div>
    
    <div style="margin-bottom: 15px;">
        <a href="{action_url}?action=complete&task_id={task_id}" 
           style="display: inline-block; 
                  padding: 12px 24px; 
                  margin: 5px; 
                  background: #10b981; 
                  color: white; 
                  text-decoration: none; 
                  border-radius: 6px; 
                  font-weight: 600;
                  font-size: 14px;">
            ✅ Complete Task
        </a>
        
        <a href="{action_url}?action=delay_1hour&task_id={task_id}" 
           style="display: inline-block; 
                  padding: 12px 24px; 
                  margin: 5px; 
                  background: #6b7280; 
                  color: white; 
                  text-decoration: none; 
                  border-radius: 6px; 
                  font-weight: 600;
                  font-size: 14px;">
            ⏰ +1 Hour
        </a>
        
        <a href="{action_url}?action=delay_1day&task_id={task_id}" 
           style="display: inline-block; 
                  padding: 12px 24px; 
                  margin: 5px; 
                  background: #6b7280; 
                  color: white; 
                  text-decoration: none; 
                  border-radius: 6px; 
                  font-weight: 600;
                  font-size: 14px;">
            📅 +1 Day
        </a>
        
        <a href="{action_url}?action=delay_custom&task_id={task_id}" 
           style="display: inline-block; 
                  padding: 12px 24px; 
                  margin: 5px; 
                  background: #3b82f6; 
                  color: white; 
                  text-decoration: none; 
                  border-radius: 6px; 
                  font-weight: 600;
                  font-size: 14px;">
            📅 Custom
        </a>
    </div>
</div>

<!-- Status Change Buttons -->
{status_buttons_html}

<!-- Footer -->
<div style="text-align: center; 
            color: #9ca3af; 
            font-size: 12px; 
            padding-top: 20px;">
    Rob's AI Task Manager • Cloud Clean Energy
</div>

</body>
</html>"""

        # Build plain text version
        plain = f"""⏰ TASK REMINDER

📋 {task['title']}
👤 Client: {client_name}
⏰ Due: {due_time.strftime('%I:%M %p AEST')}
📊 Status: {status_emoji} {status_name}

🤖 AI SUMMARY:
{ai_summary}

📝 RECENT NOTES:
{self._build_notes_plain(notes)}

ACTIONS:
- Complete: {action_url}?action=complete&task_id={task_id}
- +1 Hour: {action_url}?action=delay_1hour&task_id={task_id}
- +1 Day: {action_url}?action=delay_1day&task_id={task_id}
- Next Stage: {action_url}?action=next_status&task_id={task_id}
- Previous Stage: {action_url}?action=prev_status&task_id={task_id}
"""

        subject = f"⏰ {status_emoji} {task['title'][:40]} | {client_name}"
        
        return self.send_html_email(
            'rob@cloudcleanenergy.com.au',
            subject,
            html,
            plain
        )
    
    def _build_checklist_html(self, task_id, checklist_items, action_url):
        """Build HTML for checklist section with Update button"""
        if not checklist_items:
            return ""
        
        # Build checklist items display
        items_html = ""
        for item in checklist_items:
            items_html += f"""
            <div style="padding: 8px 0; 
                        border-bottom: 1px solid #e5e7eb;
                        color: #374151;
                        font-size: 14px;">
                ○ {item['item_text']}
            </div>"""
        
        return f"""
<!-- Checklist Section -->
<div style="background: white; 
            padding: 20px; 
            border-radius: 8px; 
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
    
    <div style="font-weight: 600; 
                color: #374151; 
                margin-bottom: 15px;
                font-size: 14px;">
        📋 To-Do Items ({len(checklist_items)} remaining)
    </div>
    
    <div style="margin-bottom: 15px;">
        {items_html}
    </div>
    
    <a href="{action_url}?action=checklist&task_id={task_id}" 
       style="display: inline-block; 
              padding: 10px 20px; 
              background: #8b5cf6; 
              color: white; 
              text-decoration: none; 
              border-radius: 6px; 
              font-weight: 600;
              font-size: 14px;">
        ✏️ Update Checklist
    </a>
</div>
"""

    def _build_notes_html(self, notes):
        """Build HTML for notes history section"""
        if not notes:
            return """
<div style="background: #f3f4f6; 
            padding: 15px; 
            border-radius: 8px; 
            margin-bottom: 20px;
            text-align: center;
            color: #6b7280;">
    No notes recorded yet
</div>"""
        
        notes_items = ""
        for note in notes:
            created = note.get('created_at', '')
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    created_str = dt.strftime('%d/%m %I:%M %p')
                except:
                    created_str = created[:16]
            else:
                created_str = "Unknown"
            
            source = note.get('source', 'manual')
            content = note.get('content', '')[:300]
            
            # Source icon and color
            if source == 'email':
                icon = "📧"
                bg = "#fef3c7"
                border = "#fbbf24"
            elif source == 'system':
                icon = "⚙️"
                bg = "#f3f4f6"
                border = "#9ca3af"
            else:
                icon = "📝"
                bg = "#ecfdf5"
                border = "#10b981"
            
            notes_items += f"""
<div style="background: {bg}; 
            border-left: 3px solid {border};
            padding: 12px 15px; 
            margin-bottom: 10px;
            border-radius: 0 6px 6px 0;">
    <div style="display: flex; justify-content: space-between; margin-bottom: 5px;">
        <span style="font-weight: 600; font-size: 12px; color: #374151;">
            {icon} {source.title()}
        </span>
        <span style="font-size: 11px; color: #6b7280;">
            {created_str}
        </span>
    </div>
    <div style="font-size: 14px; color: #1f2937; line-height: 1.5;">
        {content}
    </div>
</div>"""
        
        return f"""
<div style="background: white; 
            padding: 20px; 
            border-radius: 8px; 
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
    
    <div style="font-weight: 600; 
                color: #374151; 
                margin-bottom: 15px;
                font-size: 14px;">
        📝 Recent Notes ({len(notes)})
    </div>
    
    {notes_items}
</div>"""
    
    def _build_notes_plain(self, notes):
        """Build plain text version of notes"""
        if not notes:
            return "No notes recorded yet"
        
        lines = []
        for note in notes:
            created = note.get('created_at', '')[:10]
            source = note.get('source', 'manual')
            content = note.get('content', '')[:100]
            lines.append(f"[{created}] {source}: {content}")
        
        return "\n".join(lines)
    
    def _build_status_buttons_html(self, task_id, action_url):
        """Build HTML for status change buttons"""
        return f"""
<div style="background: white; 
            padding: 20px; 
            border-radius: 8px; 
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
    
    <div style="font-weight: 600; 
                color: #374151; 
                margin-bottom: 15px;
                font-size: 14px;">
        📊 Change Project Status
    </div>
    
    <div>
        <a href="{action_url}?action=prev_status&task_id={task_id}" 
           style="display: inline-block; 
                  padding: 10px 20px; 
                  margin: 5px; 
                  background: #f3f4f6; 
                  color: #374151; 
                  text-decoration: none; 
                  border-radius: 6px; 
                  font-weight: 600;
                  font-size: 13px;
                  border: 1px solid #e5e7eb;">
            ⬅️ Previous Stage
        </a>
        
        <a href="{action_url}?action=next_status&task_id={task_id}" 
           style="display: inline-block; 
                  padding: 10px 20px; 
                  margin: 5px; 
                  background: #3b82f6; 
                  color: white; 
                  text-decoration: none; 
                  border-radius: 6px; 
                  font-weight: 600;
                  font-size: 13px;">
            ➡️ Next Stage
        </a>
    </div>
</div>"""
    
    # ========================================
    # DAILY SUMMARY (Enhanced)
    # ========================================
    
    def send_enhanced_daily_summary(self):
        """Send daily summary email with project status breakdown"""
        print("📊 Generating enhanced daily summary...")
        
        try:
            # Get all pending tasks
            result = self.tm.supabase.table('tasks')\
                .select('*, project_statuses(*)')\
                .eq('status', 'pending')\
                .order('due_date')\
                .execute()
            
            tasks = result.data
            
            if not tasks:
                print("   ℹ️ No pending tasks for summary")
                return
            
            # Group by status
            by_status = {}
            for task in tasks:
                status = task.get('project_statuses') or {}
                status_name = status.get('name', 'Unknown')
                if status_name not in by_status:
                    by_status[status_name] = {
                        'tasks': [],
                        'color': status.get('color', '#6b7280'),
                        'emoji': status.get('emoji', '📋'),
                        'order': status.get('display_order', 99)
                    }
                by_status[status_name]['tasks'].append(task)
            
            # Build HTML
            html = self._build_daily_summary_html(by_status, len(tasks))
            plain = self._build_daily_summary_plain(by_status, len(tasks))
            
            now = datetime.now(self.aest)
            subject = f"📊 Daily Summary - {len(tasks)} Tasks | {now.strftime('%d %b %Y')}"
            
            self.send_html_email(
                'rob@cloudcleanenergy.com.au',
                subject,
                html,
                plain
            )
            
            print(f"✅ Daily summary sent ({len(tasks)} tasks)")
            
        except Exception as e:
            print(f"❌ Daily summary error: {e}")
    
    def _build_daily_summary_html(self, by_status, total_count):
        """Build HTML for daily summary"""
        now = datetime.now(self.aest)
        
        # Status sections
        status_sections = ""
        for status_name in sorted(by_status.keys(), key=lambda x: by_status[x]['order']):
            info = by_status[status_name]
            tasks = info['tasks']
            color = info['color']
            emoji = info['emoji']
            
            tasks_html = ""
            for task in tasks[:5]:  # Show max 5 per status
                client = task.get('client_name', 'Unknown')
                due = task.get('due_date', 'No date')
                task_id = task.get('id', '')
                tasks_html += f"""
<div style="padding: 12px; 
            background: #f9fafb; 
            border-radius: 6px; 
            margin-bottom: 10px;
            font-size: 14px;">
    <div style="font-weight: 600; color: #111827; margin-bottom: 6px;">{task['title'][:50]}</div>
    <div style="color: #6b7280; font-size: 12px; margin-bottom: 10px;">👤 {client} • 📅 {due}</div>
    <div style="display: flex; gap: 6px; flex-wrap: wrap;">
        <a href="{self.action_url}?action=complete&task_id={task_id}" 
           style="display: inline-block; 
                  padding: 6px 12px; 
                  background: #10b981; 
                  color: white; 
                  text-decoration: none; 
                  border-radius: 4px; 
                  font-size: 11px;
                  font-weight: 600;">✅ Done</a>
        <a href="{self.action_url}?action=delay_1day&task_id={task_id}" 
           style="display: inline-block; 
                  padding: 6px 12px; 
                  background: #6b7280; 
                  color: white; 
                  text-decoration: none; 
                  border-radius: 4px; 
                  font-size: 11px;
                  font-weight: 600;">📅 +1 Day</a>
        <a href="{self.action_url}?action=next_status&task_id={task_id}" 
           style="display: inline-block; 
                  padding: 6px 12px; 
                  background: #3b82f6; 
                  color: white; 
                  text-decoration: none; 
                  border-radius: 4px; 
                  font-size: 11px;
                  font-weight: 600;">⏭️ Next Status</a>
    </div>
</div>"""
            
            if len(tasks) > 5:
                tasks_html += f"""
<div style="color: #6b7280; font-size: 12px; text-align: center;">
    +{len(tasks) - 5} more...
</div>"""
            
            status_sections += f"""
<div style="background: white; 
            border-radius: 8px; 
            margin-bottom: 15px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
    <div style="background: {color}; 
                color: white; 
                padding: 12px 15px;
                font-weight: 600;">
        {emoji} {status_name} ({len(tasks)})
    </div>
    <div style="padding: 15px;">
        {tasks_html}
    </div>
</div>"""
        
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
             padding: 20px; 
             max-width: 650px; 
             margin: 0 auto;
             background-color: #f9fafb;">

<div style="background: linear-gradient(135deg, #3b82f6, #1d4ed8); 
            color: white; 
            padding: 25px; 
            border-radius: 12px; 
            margin-bottom: 20px;
            text-align: center;">
    <h1 style="margin: 0 0 10px 0; font-size: 24px;">📊 Daily Task Summary</h1>
    <div style="font-size: 14px; opacity: 0.9;">{now.strftime('%A, %d %B %Y')}</div>
    <div style="font-size: 32px; font-weight: bold; margin-top: 15px;">{total_count}</div>
    <div style="font-size: 14px; opacity: 0.8;">Pending Tasks</div>
</div>

{status_sections}

<div style="text-align: center; 
            color: #9ca3af; 
            font-size: 12px; 
            padding-top: 20px;">
    Rob's AI Task Manager • Cloud Clean Energy
</div>

</body>
</html>"""
    
    def _build_daily_summary_plain(self, by_status, total_count):
        """Build plain text daily summary"""
        now = datetime.now(self.aest)
        lines = [
            f"📊 DAILY TASK SUMMARY",
            f"{now.strftime('%A, %d %B %Y')}",
            f"Total Pending: {total_count}",
            "",
        ]
        
        for status_name in sorted(by_status.keys(), key=lambda x: by_status[x]['order']):
            info = by_status[status_name]
            lines.append(f"\n{info['emoji']} {status_name} ({len(info['tasks'])})")
            for task in info['tasks'][:5]:
                lines.append(f"  - {task['title'][:40]}")
        
        return "\n".join(lines)


# ========================================
# STANDALONE TESTING
# ========================================

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    etm = EnhancedTaskManager()
    
    # Test AI summarization
    test_notes = [
        {'content': 'Client called about 10kW solar system quote', 'source': 'email', 'created_at': '2025-11-27T10:00:00'},
        {'content': 'Sent initial quote $12,500', 'source': 'manual', 'created_at': '2025-11-27T14:00:00'},
        {'content': 'Client interested in battery storage too', 'source': 'email', 'created_at': '2025-11-28T09:00:00'},
    ]
    
    print("\n🤖 Testing AI Summarization:")
    summary = etm.summarize_notes(test_notes)
    print(f"Summary: {summary}")
