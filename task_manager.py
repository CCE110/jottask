#!/usr/bin/env python3
"""
Multi-Business Task Management System
Uses Supabase for database and Zoho Mail for notifications
"""
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

import os
import sys
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from supabase import create_client, Client
from anthropic import Anthropic
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class TaskManager:
    def __init__(self):
        # Initialize Supabase
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_KEY')
        
        if not supabase_url or not supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env file")
        
        self.supabase: Client = create_client(supabase_url, supabase_key)
        
        # Initialize Anthropic
        anthropic_key = os.getenv('ANTHROPIC_API_KEY')
        if anthropic_key:
            self.claude = Anthropic(api_key=anthropic_key)
        else:
            self.claude = None
        
        # Zoho Mail settings (Australian servers)
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
    
    def get_business_by_id(self, business_id: str) -> Optional[Dict]:
        """Get a business by ID"""
        result = self.supabase.table('businesses').select('*').eq('id', business_id).execute()
        return result.data[0] if result.data else None
    
    def send_html_email(self, to_email: str, subject: str, html_body: str, plain_body: str) -> bool:
        """Send HTML email with plain text fallback via Zoho Mail SMTP"""
        if not self.smtp_password:
            print("❌ Zoho password not configured. Set ZOHO_PASSWORD in .env file")
            return False
        
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.from_email
            msg['To'] = to_email
            msg['Subject'] = subject
            
            # Attach both plain and HTML versions
            msg.attach(MIMEText(plain_body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
            
            server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port)
            server.login(self.from_email, self.smtp_password)
            server.send_message(msg)
            server.quit()
            
            print(f"✅ HTML email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            print(f"❌ Failed to send email: {e}")
            return False
    
    def send_task_confirmation_email(self, task_id: str) -> bool:
        """Send confirmation email with clickable buttons for a task"""
        # Get task details
        task_result = self.supabase.table('tasks').select('*').eq('id', task_id).execute()
        if not task_result.data:
            print(f"❌ Task {task_id} not found")
            return False
        
        task = task_result.data[0]
        business = self.get_business_by_id(task['business_id'])
        
        # Base URL for actions
        base_url = os.getenv('TASK_ACTION_URL', 'https://placeholder.com/action')
        
        task_id_short = task['id'][:8]
        
        subject = f"✅ Task Created: {task['title']}"
        
        # Create beautiful HTML email
        html_body = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
                <h2 style="color: white; margin: 0;">✅ Task Created Successfully!</h2>
            </div>
            
            <div style="background: white; padding: 30px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
                <div style="background: #f9fafb; padding: 20px; border-radius: 8px; margin-bottom: 25px; border-left: 4px solid #667eea;">
                    <h3 style="margin: 0 0 15px 0; color: #1f2937; font-size: 20px;">📋 {task['title']}</h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px 0; color: #6b7280; font-weight: 600;">Business:</td>
                            <td style="padding: 8px 0; color: #1f2937;">{business['name']}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #6b7280; font-weight: 600;">Due Date:</td>
                            <td style="padding: 8px 0; color: #1f2937;">{task['due_date']}{' at ' + task['due_time'] if task.get('due_time') else ''}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #6b7280; font-weight: 600;">Priority:</td>
                            <td style="padding: 8px 0;">
                                <span style="background: {'#fee2e2' if task['priority'] == 'urgent' else '#fed7aa' if task['priority'] == 'high' else '#fef08a' if task['priority'] == 'medium' else '#dcfce7'}; 
                                             color: {'#991b1b' if task['priority'] == 'urgent' else '#9a3412' if task['priority'] == 'high' else '#854d0e' if task['priority'] == 'medium' else '#166534'}; 
                                             padding: 4px 12px; border-radius: 12px; font-size: 13px; font-weight: 600;">
                                    {task['priority'].upper()}
                                </span>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #6b7280; font-weight: 600;">Type:</td>
                            <td style="padding: 8px 0; color: #1f2937;">{'📅 Meeting' if task['is_meeting'] else '✓ Task'}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #6b7280; font-weight: 600; font-size: 11px;">Task ID:</td>
                            <td style="padding: 8px 0; color: #9ca3af; font-size: 11px; font-family: monospace;">{task_id_short}</td>
                        </tr>
                    </table>
                    {f'<div style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #e5e7eb; color: #4b5563; line-height: 1.6;">{task["description"]}</div>' if task.get('description') else ''}
                </div>
                
                <div style="margin: 30px 0;">
                    <h3 style="color: #1f2937; margin-bottom: 15px; font-size: 18px;">🔗 Quick Actions</h3>
                    <p style="color: #6b7280; margin-bottom: 20px;">Click any button to take action on this task:</p>
                    
                    <table cellspacing="0" cellpadding="0" style="width: 100%;">
                        <tr>
                            <td style="padding: 5px;">
                                <a href="{base_url}?action=complete&task_id={task['id']}" 
                                   style="display: block; background: #10b981; color: white; padding: 14px 20px; text-decoration: none; border-radius: 8px; font-weight: 600; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                                    ✅ Mark Complete
                                </a>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 5px;">
                                <a href="{base_url}?action=postpone&task_id={task['id']}&days=1" 
                                   style="display: block; background: #f59e0b; color: white; padding: 14px 20px; text-decoration: none; border-radius: 8px; font-weight: 600; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                                    📅 Postpone 1 Day
                                </a>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 5px;">
                                <a href="{base_url}?action=postpone&task_id={task['id']}&days=7" 
                                   style="display: block; background: #f59e0b; color: white; padding: 14px 20px; text-decoration: none; border-radius: 8px; font-weight: 600; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                                    📅 Postpone 1 Week
                                </a>
                            </td>
                        </tr>
                        <tr>
                            <td style="padding: 5px;">
                                <a href="{base_url}?action=add_followup_form&task_id={task['id']}" 
                                   style="display: block; background: #6366f1; color: white; padding: 14px 20px; text-decoration: none; border-radius: 8px; font-weight: 600; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                                    📌 Add Follow-up
                                </a>
                            </td>
                        </tr>
                    </table>
                </div>
            </div>
            
            <div style="text-align: center; padding: 20px; color: #9ca3af; font-size: 12px;">
                <p style="margin: 0;">Task Management Bot • Powered by Claude AI</p>
            </div>
        </body>
        </html>
        """
        
        # Plain text fallback
        plain_body = f"""✅ Task Created Successfully!

TASK DETAILS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Business: {business['name']}
Title: {task['title']}
Due Date: {task['due_date']}{' at ' + task['due_time'] if task.get('due_time') else ''}
Priority: {task['priority'].upper()}
Type: {'📅 Meeting' if task['is_meeting'] else '✓ Task'}
Task ID: {task_id_short}

{('Description: ' + task['description']) if task.get('description') else ''}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

QUICK ACTIONS (Click these links in your email):
✅ Mark Complete: {base_url}?action=complete&task_id={task['id']}
📅 Postpone 1 Day: {base_url}?action=postpone&task_id={task['id']}&days=1
📅 Postpone 1 Week: {base_url}?action=postpone&task_id={task['id']}&days=7
📌 Add Follow-up: {base_url}?action=add_followup_form&task_id={task['id']}

💡 Tip: You'll receive a reminder about this task on {task['due_date']}.

---
Task Management Bot • Powered by Claude AI
"""
        
        return self.send_html_email(self.from_email, subject, html_body, plain_body)

if __name__ == "__main__":
    print("TaskManager library loaded successfully!")
