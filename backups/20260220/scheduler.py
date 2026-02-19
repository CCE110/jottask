#!/usr/bin/env python3
"""
Task Reminder Scheduler
Sends daily reminders at 8AM AEST (or custom time)
"""

import os
from datetime import datetime, timedelta
import pytz
from task_manager import TaskManager
from dotenv import load_dotenv

load_dotenv()

class TaskScheduler:
    def __init__(self):
        self.tm = TaskManager()
        self.aest = pytz.timezone('Australia/Sydney')
        self.default_reminder_time = "08:00"  # 8AM AEST default
    
    def send_daily_reminders(self, reminder_time="08:00"):
        """Send reminders for tasks due today or overdue"""
        today = datetime.now(self.aest).date()
        
        # Get tasks due today or overdue
        tasks_result = self.tm.supabase.table('tasks').select('*').or_(
            f'due_date.eq.{today},due_date.lt.{today}'
        ).eq('status', 'pending').execute()
        
        if not tasks_result.data:
            print(f"📅 No tasks due today ({today})")
            return
        
        # Group tasks by business
        businesses = {}
        for task in tasks_result.data:
            business = self.tm.get_business_by_id(task['business_id'])
            if business['name'] not in businesses:
                businesses[business['name']] = []
            businesses[business['name']].append(task)
        
        # Send daily summary email
        self.send_daily_summary_email(businesses, today)
    
    def send_daily_summary_email(self, businesses, date):
        """Send beautiful daily task summary email"""
        total_tasks = sum(len(tasks) for tasks in businesses.values())
        
        subject = f"📋 Daily Task Summary - {date.strftime('%B %d, %Y')} ({total_tasks} tasks)"
        
        # Build HTML email
        task_html = ""
        for business_name, tasks in businesses.items():
            task_html += f"""
            <div style="margin-bottom: 30px;">
                <h3 style="color: #1f2937; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 2px solid #667eea;">
                    🏢 {business_name} ({len(tasks)} tasks)
                </h3>
            """
            
            for task in tasks:
                priority_colors = {
                    'urgent': '#fee2e2, #991b1b',
                    'high': '#fed7aa, #9a3412', 
                    'medium': '#fef08a, #854d0e',
                    'low': '#dcfce7, #166534'
                }
                bg_color, text_color = priority_colors.get(task['priority'], '#f9fafb, #1f2937').split(', ')
                
                overdue = datetime.fromisoformat(task['due_date']).date() < date
                overdue_badge = "🚨 OVERDUE" if overdue else ""
                
                base_url = os.getenv('TASK_ACTION_URL', 'https://placeholder.com/action')
                
                task_html += f"""
                <div style="background: #f9fafb; padding: 15px; border-radius: 8px; margin-bottom: 15px; border-left: 4px solid #667eea;">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 10px;">
                        <h4 style="margin: 0; color: #1f2937;">{task['title']} {overdue_badge}</h4>
                        <span style="background: {bg_color}; color: {text_color}; padding: 4px 8px; border-radius: 12px; font-size: 12px; font-weight: 600;">
                            {task['priority'].upper()}
                        </span>
                    </div>
                    
                    <p style="margin: 0 0 15px 0; color: #6b7280;">
                        📅 Due: {task['due_date']}{' at ' + task['due_time'] if task.get('due_time') else ''}<br>
                        {'📅 Meeting' if task['is_meeting'] else '✓ Task'}
                        {f'<br>📝 {task["description"]}' if task.get('description') else ''}
                    </p>
                    
                    <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                        <a href="{base_url}?action=complete&task_id={task['id']}" 
                           style="background: #10b981; color: white; padding: 8px 12px; text-decoration: none; border-radius: 6px; font-size: 12px; font-weight: 600;">
                            ✅ Complete
                        </a>
                        <a href="{base_url}?action=postpone&task_id={task['id']}&days=1" 
                           style="background: #f59e0b; color: white; padding: 8px 12px; text-decoration: none; border-radius: 6px; font-size: 12px; font-weight: 600;">
                            📅 +1 Day
                        </a>
                        <a href="{base_url}?action=postpone&task_id={task['id']}&days=7" 
                           style="background: #f59e0b; color: white; padding: 8px 12px; text-decoration: none; border-radius: 6px; font-size: 12px; font-weight: 600;">
                            📅 +1 Week
                        </a>
                    </div>
                </div>
                """
            
            task_html += "</div>"
        
        html_body = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
                <h2 style="color: white; margin: 0;">📋 Daily Task Summary</h2>
                <p style="color: white; margin: 10px 0 0 0; opacity: 0.9;">{date.strftime('%A, %B %d, %Y')} • {total_tasks} tasks</p>
            </div>
            
            <div style="background: white; padding: 30px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
                {task_html}
                
                <div style="text-align: center; padding: 20px; margin-top: 20px; background: #f9fafb; border-radius: 8px;">
                    <p style="margin: 0; color: #6b7280; font-size: 14px;">
                        💡 You'll receive this summary daily at 8:00 AM AEST<br>
                        Click any button to take instant action on your tasks
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        plain_body = f"""📋 DAILY TASK SUMMARY - {date.strftime('%B %d, %Y')}

{total_tasks} tasks need your attention:

"""
        for business_name, tasks in businesses.items():
            plain_body += f"\n🏢 {business_name} ({len(tasks)} tasks):\n" + "="*50 + "\n"
            for task in tasks:
                overdue = datetime.fromisoformat(task['due_date']).date() < date
                overdue_text = " 🚨 OVERDUE" if overdue else ""
                plain_body += f"""
- {task['title']}{overdue_text}
  Due: {task['due_date']}{' at ' + task['due_time'] if task.get('due_time') else ''}
  Priority: {task['priority'].upper()}
  {'Meeting' if task['is_meeting'] else 'Task'}
  {task['description'] if task.get('description') else ''}
"""
        
        plain_body += f"\n💡 Daily reminders sent at 8:00 AM AEST\n"
        
        return self.tm.send_html_email(self.tm.from_email, subject, html_body, plain_body)

if __name__ == "__main__":
    scheduler = TaskScheduler()
    scheduler.send_daily_reminders()
