#!/usr/bin/env python3
import imaplib
import email
from email.header import decode_header
import json
from task_manager import TaskManager
from anthropic import Anthropic
import os
import time
import schedule
from datetime import datetime

class CloudEmailProcessor:
    def __init__(self):
        self.tm = TaskManager()
        self.claude = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        self.gmail_user = 'robcrm.ai@gmail.com'
        self.gmail_pass = 'sgho tbwr optz yxie'
        
        self.businesses = {
            'Cloud Clean Energy': 'feb14276-5c3d-4fcf-af06-9a8f54cf7159',
            'DSW (Direct Solar Warehouse)': '390fbfb9-1166-45a5-ba17-39c9c48d5f9a',
            'KVELL': 'e15518d2-39c2-4503-95bd-cb6f0b686022',
            'AI Project Pro': 'ec5d7aab-8d74-4ef2-9d92-01b143c68c82',
            'Veterans Health Centre (VHC)': '0b083ea5-ff45-4606-8cae-6ed387926641'
        }
        
        self.processed_emails = set()
        print("🌐 Cloud Email Processor initialized!")
    
    def process_emails(self):
        """Check and process new emails"""
        try:
            print(f"🔍 Checking emails at {datetime.now()}")
            
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            mail.login(self.gmail_user, self.gmail_pass)
            mail.select('inbox')
            
            # Get unread emails
            status, messages = mail.search(None, 'UNSEEN')
            
            if not messages[0]:
                print("📭 No new emails")
                mail.close()
                mail.logout()
                return
            
            email_ids = messages[0].split()
            new_count = len([eid for eid in email_ids if eid not in self.processed_emails])
            
            print(f"📬 Found {new_count} new emails to process")
            
            for msg_id in email_ids:
                if msg_id in self.processed_emails:
                    continue
                    
                self.process_single_email(mail, msg_id)
                self.processed_emails.add(msg_id)
            
            mail.close()
            mail.logout()
            print("✅ Email processing completed")
            
        except Exception as e:
            print(f"❌ Email processing error: {e}")
    
    def process_single_email(self, mail, msg_id):
        """Process a single email and create tasks"""
        try:
            status, msg_data = mail.fetch(msg_id, '(RFC822)')
            email_body = email.message_from_bytes(msg_data[0][1])
            
            subject = decode_header(email_body['Subject'])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode()
            
            sender = email_body.get('From', '')
            
            # Get content
            content = ""
            if email_body.is_multipart():
                for part in email_body.walk():
                    if part.get_content_type() == "text/plain":
                        content += part.get_payload(decode=True).decode('utf-8', errors='ignore')
            else:
                content = email_body.get_payload(decode=True).decode('utf-8', errors='ignore')
            
            print(f"📧 Processing: {subject[:50]}...")
            print(f"   From: {sender[:40]}")
            
            # Check if task-related
            task_keywords = ['task', 'create', 'set', 'reminder', 'follow', 'call', 'meeting', 'urgent']
            if not any(kw in subject.lower() or kw in content.lower() for kw in task_keywords):
                print(f"   ⏭️  Not a task-related email")
                return
            
            # Use Claude to analyze
            prompt = f"""Analyze this email and extract task information.

Email Subject: {subject}
Email Content: {content[:1000]}

Extract tasks in JSON format:
{{
    "create_tasks": true,
    "tasks": [
        {{
            "title": "string",
            "description": "string",
            "business": "Cloud Clean Energy",
            "priority": "high/medium/low",
            "due_date": "YYYY-MM-DD or null",
            "due_time": "HH:MM or null",
            "is_meeting": false
        }}
    ]
}}

If not a task email, return: {{"create_tasks": false, "tasks": []}}"""

            response = self.claude.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            analysis = json.loads(response.content[0].text)
            
            if not analysis.get('create_tasks') or not analysis.get('tasks'):
                print(f"   ⏭️  No tasks to create")
                return
            
            # Create tasks
            for task in analysis['tasks']:
                self.create_task(task)
                
        except json.JSONDecodeError as e:
            print(f"   ❌ JSON parsing error: {e}")
        except Exception as e:
            print(f"   ❌ Error processing email: {e}")
    
    def create_task(self, task_data):
        """Create a task in database"""
        try:
            business_id = self.businesses.get(task_data.get('business', 'Cloud Clean Energy'))
            if not business_id:
                business_id = self.businesses['Cloud Clean Energy']
            
            result = self.tm.supabase.table('tasks').insert({
                'business_id': business_id,
                'title': task_data['title'],
                'description': task_data.get('description', ''),
                'due_date': task_data.get('due_date'),
                'due_time': task_data.get('due_time'),
                'priority': task_data.get('priority', 'medium'),
                'is_meeting': task_data.get('is_meeting', False),
                'status': 'pending'
            }).execute()
            
            if result.data:
                print(f"   ✅ Task created: {task_data['title']}")
            else:
                print(f"   ❌ Failed to create task")
                
        except Exception as e:
            print(f"   ❌ Error creating task: {e}")
    
    def start(self):
        """Start 24/7 scheduler"""
        schedule.every(15).minutes.do(self.process_emails)
        
        # Process immediately on startup
        self.process_emails()
        
        print("🌐 Cloud scheduler started - Running 24/7!")
        print("📧 Email checks: Every 15 minutes")
        
        while True:
            schedule.run_pending()
            time.sleep(60)

if __name__ == "__main__":
    processor = CloudEmailProcessor()
    processor.start()
