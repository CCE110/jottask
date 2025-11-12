#!/usr/bin/env python3
"""
Enhanced 5-Business Task Management System with Sub-Tasks
"""

import os
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from supabase import create_client, Client
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

class EnhancedTaskManager:
    def __init__(self):
        # Initialize Supabase
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_KEY')
        
        self.supabase: Client = create_client(supabase_url, supabase_key)
        
        # Enhanced business mapping - ALL 5 COMPANIES
        self.business_names = {
            'Cloud Clean Energy': 'Solar energy, main business operations',
            'DSW (Direct Solar Warehouse)': 'Solar equipment wholesale and distribution', 
            'KVELL': 'KVELL business operations',
            'AI Project Pro': 'AI consulting business',
            'Veterans Health Centre (VHC)': 'Veterans healthcare and support services'
        }
        
        # Zoho Mail settings
        self.smtp_server = "smtp.zoho.com.au"
        self.smtp_port = 465
        self.from_email = "rob@cloudcleanenergy.com.au"
        self.smtp_password = os.getenv('ZOHO_PASSWORD')
    
    def get_businesses(self, active_only: bool = True) -> List[Dict]:
        """Get all businesses"""
        query = self.supabase.table('businesses').select('*')
        if active_only:
            query = query.eq('active', True)
        
        result = query.order('name').execute()
        return result.data
    
    def get_business_by_name(self, name: str) -> Optional[Dict]:
        """Get a business by name"""
        result = self.supabase.table('businesses').select('*').eq('name', name).execute()
        return result.data[0] if result.data else None
    
    def create_task_with_subtasks(self, business_name: str, title: str, description: str = "",
                                 due_date: str = None, priority: str = "medium", 
                                 subtasks: List[str] = None) -> Optional[Dict]:
        """Create a task with optional sub-tasks"""
        business = self.get_business_by_name(business_name)
        if not business:
            print(f"❌ Business '{business_name}' not found")
            available = [b['name'] for b in self.get_businesses()]
            print(f"Available businesses: {', '.join(available)}")
            return None
        
        # Create main task
        task_result = self.supabase.table('tasks').insert({
            'business_id': business['id'],
            'title': title,
            'description': description,
            'due_date': due_date,
            'priority': priority,
            'is_meeting': False
        }).execute()
        
        if not task_result.data:
            print("❌ Failed to create task")
            return None
        
        task = task_result.data[0]
        
        # Create sub-tasks if provided
        if subtasks:
            for i, subtask_title in enumerate(subtasks):
                self.supabase.table('subtasks').insert({
                    'task_id': task['id'],
                    'title': subtask_title,
                    'completed': False,
                    'order_index': i + 1
                }).execute()
        
        print(f"✅ Task created: {title}")
        print(f"🏢 Business: {business_name}")
        print(f"📋 Sub-tasks: {len(subtasks) if subtasks else 0}")
        return task
    
    def get_tasks_by_business(self) -> Dict[str, List[Dict]]:
        """Get all pending tasks grouped by business with their sub-tasks"""
        businesses = self.get_businesses()
        business_tasks = {}
        
        for business in businesses:
            # Get pending tasks for this business
            tasks_result = self.supabase.table('tasks').select('*').eq('business_id', business['id']).eq('status', 'pending').order('due_date').execute()
            tasks = tasks_result.data or []
            
            # Get sub-tasks for each task
            for task in tasks:
                subtasks_result = self.supabase.table('subtasks').select('*').eq('task_id', task['id']).order('order_index').execute()
                task['subtasks'] = subtasks_result.data or []
            
            if tasks:  # Only include businesses with pending tasks
                business_tasks[business['name']] = tasks
        
        return business_tasks
    
    def send_enhanced_daily_summary(self):
        """Send daily summary with all 5 businesses and sub-tasks"""
        today = datetime.now().date()
        business_tasks = self.get_tasks_by_business()
        
        if not business_tasks:
            print("📭 No pending tasks across all businesses")
            return
        
        total_task_count = sum(len(tasks) for tasks in business_tasks.values())
        subject = f"📊 5-Business Daily Summary - {today.strftime('%B %d, %Y')} ({total_task_count} tasks)"
        
        # Build enhanced HTML with 5 businesses and unique colors
        business_html = ""
        for business_name, tasks in business_tasks.items():
            # Business-specific colors for all 5 companies
            business_colors = {
                'Cloud Clean Energy': '#10b981',                  # Green
                'DSW (Direct Solar Warehouse)': '#f59e0b',        # Orange
                'KVELL': '#8b5cf6',                              # Purple
                'AI Project Pro': '#3b82f6',                     # Blue
                'Veterans Health Centre (VHC)': '#ef4444'        # Red
            }
            color = business_colors.get(business_name, '#6b7280')
            
            business_html += f"""
            <div style="margin-bottom: 30px; border: 2px solid {color}; border-radius: 12px; overflow: hidden;">
                <div style="background: {color}; color: white; padding: 20px;">
                    <h2 style="margin: 0; font-size: 20px;">🏢 {business_name}</h2>
                    <p style="margin: 5px 0 0 0; opacity: 0.9;">{len(tasks)} pending task{'s' if len(tasks) != 1 else ''}</p>
                </div>
                <div style="padding: 20px; background: white;">
            """
            
            for task in tasks:
                overdue = datetime.fromisoformat(task['due_date']).date() < today if task['due_date'] else False
                overdue_badge = "🚨 OVERDUE " if overdue else ""
                
                base_url = os.getenv('TASK_ACTION_URL', 'https://rob-crm-tasks-production.up.railway.app/action')
                
                # Sub-tasks HTML
                subtasks_html = ""
                if task['subtasks']:
                    completed_count = sum(1 for st in task['subtasks'] if st['completed'])
                    total_count = len(task['subtasks'])
                    progress = f"({completed_count}/{total_count})"
                    
                    subtasks_html = f"<div style='margin-top: 15px; padding: 15px; background: #f3f4f6; border-radius: 8px;'><strong>📋 Sub-tasks {progress}:</strong><ul style='margin: 10px 0; padding-left: 20px;'>"
                    for subtask in task['subtasks']:
                        check = "✅" if subtask['completed'] else "☐"
                        style = "text-decoration: line-through; opacity: 0.6;" if subtask['completed'] else ""
                        subtasks_html += f"<li style='margin: 8px 0; {style}'>{check} {subtask['title']}</li>"
                    subtasks_html += "</ul></div>"
                
                business_html += f"""
                <div style="background: #f9fafb; padding: 20px; border-radius: 8px; margin-bottom: 20px; border-left: 4px solid {color};">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 15px;">
                        <h3 style="margin: 0; color: #1f2937; font-size: 18px;">{overdue_badge}{task['title']}</h3>
                        <span style="background: #e5e7eb; color: #374151; padding: 6px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;">
                            {task['priority'].upper()}
                        </span>
                    </div>
                    
                    <p style="margin: 0 0 10px 0; color: #6b7280;">
                        📅 Due: {task['due_date'] or 'No due date'}<br>
                        {'📅 Meeting' if task.get('is_meeting') else '✓ Task'}
                    </p>
                    
                    {subtasks_html}
                    
                    <div style="display: flex; gap: 10px; margin-top: 15px; flex-wrap: wrap;">
                        <a href="{base_url}?action=complete&task_id={task['id']}" 
                           style="background: {color}; color: white; padding: 10px 15px; text-decoration: none; border-radius: 6px; font-size: 13px; font-weight: 600;">
                            ✅ Complete Task
                        </a>
                        <a href="{base_url}?action=postpone&task_id={task['id']}&days=1" 
                           style="background: #6b7280; color: white; padding: 10px 15px; text-decoration: none; border-radius: 6px; font-size: 13px; font-weight: 600;">
                            📅 +1 Day
                        </a>
                    </div>
                </div>
                """
            
            business_html += "</div></div>"
        
        html_body = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 12px; text-align: center; margin-bottom: 30px;">
                <h1 style="color: white; margin: 0; font-size: 28px;">📊 5-Business Dashboard</h1>
                <p style="color: white; margin: 15px 0 0 0; opacity: 0.9; font-size: 16px;">{today.strftime('%A, %B %d, %Y')} • {total_task_count} pending tasks</p>
            </div>
            
            {business_html}
            
            <div style="text-align: center; padding: 25px; margin-top: 30px; background: #f9fafb; border-radius: 12px; border: 2px solid #e5e7eb;">
                <p style="margin: 0; color: #6b7280; font-size: 14px;">
                    💡 Daily summaries sent at 8:00 AM AEST<br>
                    ✅ Click buttons to complete tasks instantly<br>
                    📋 Sub-tasks help break down complex work
                </p>
            </div>
        </body>
        </html>
        """
        
        return self.send_html_email(self.from_email, subject, html_body, "5-Business daily summary")
    
    def send_html_email(self, to_email: str, subject: str, html_body: str, plain_body: str) -> bool:
        """Send HTML email using Resend API"""
        import requests
        import time
        import os
        
        resend_api_key = os.getenv('RESEND_API_KEY')
        from_email = 'rob@cloudcleanenergy.com.au'
        
        print(f"📧 Sending via Resend API: '{subject[:50]}...' to {to_email}")
        
        try:
            response = requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {resend_api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'from': from_email,
                    'to': [to_email],
                    'subject': subject,
                    'html': html_body
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Email sent! ID: {result.get('id')}")
                time.sleep(0.6)  # Rate limit: 2 req/sec
                return True
            else:
                print(f"❌ Error {response.status_code}: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return False

