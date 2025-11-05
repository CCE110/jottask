#!/usr/bin/env python3
"""Cloud Email Processor - FINAL FIXED VERSION"""
import imaplib
import email
from email.header import decode_header
import json
import re
from task_manager import TaskManager
from anthropic import Anthropic
import os
import time
import schedule
from datetime import datetime, timedelta, date
import pytz

class CloudEmailProcessor:
    def __init__(self):
        self.tm = TaskManager()
        self.claude = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        self.gmail_user = 'robcrm.ai@gmail.com'
        self.gmail_pass = os.getenv('GMAIL_APP_PASSWORD', 'sgho tbwr optz yxie')
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
        try:
            print(f"🔍 Checking emails at {datetime.now()}")
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            mail.login(self.gmail_user, self.gmail_pass)
            mail.select('inbox')
            aest = pytz.timezone('Australia/Brisbane')
            seven_days_ago = (datetime.now(aest) - timedelta(days=7)).strftime("%d-%b-%Y")
            status, messages = mail.search(None, f'(SINCE {seven_days_ago})')
            if not messages[0]:
                print("📭 No emails in last 7 days")
                mail.close()
                mail.logout()
                return
            email_ids = messages[0].split()
            new_count = len([eid for eid in email_ids if eid not in self.processed_emails])
            if new_count == 0:
                print("📭 No new emails (all already processed)")
                mail.close()
                mail.logout()
                return
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
            import traceback
            traceback.print_exc()
    
    def clean_json_response(self, text):
        text = text.strip()
        if text.startswith('```'):
            lines = text.split('\n')
            lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            text = '\n'.join(lines).strip()
        return text
    
    def process_single_email(self, mail, msg_id):
        try:
            status, msg_data = mail.fetch(msg_id, '(RFC822)')
            email_body = email.message_from_bytes(msg_data[0][1])
            subject = decode_header(email_body['Subject'])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode()
            sender = email_body.get('From', '')
            content = ""
            if email_body.is_multipart():
                for part in email_body.walk():
                    if part.get_content_type() == "text/plain":
                        try:
                            content += part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        except:
                            pass
            else:
                try:
                    content = email_body.get_payload(decode=True).decode('utf-8', errors='ignore')
                except:
                    content = ""
            print(f"📧 Processing: {subject[:60]}...")
            print(f"   From: {sender[:50]}")
            task_keywords = ['task', 'create', 'set', 'reminder', 'follow', 'call', 'meeting', 'urgent', 'todo']
            if not any(kw in subject.lower() or kw in content.lower() for kw in task_keywords):
                print(f"   ⏭️  Not a task-related email")
                return
            prompt = f"""Analyze this email and extract task information.

Email Subject: {subject}
Email Content: {content[:1000]}

Respond with ONLY pure JSON, no markdown formatting:

{{"create_tasks": true, "tasks": [{{"title": "string", "description": "string", "business": "Cloud Clean Energy", "priority": "high/medium/low", "due_date": "YYYY-MM-DD", "due_time": "HH:MM", "is_meeting": false}}]}}

IMPORTANT: If no specific date mentioned, leave due_date as empty string "". Only include due_date if you can determine a specific date. If not a task email: {{"create_tasks": false, "tasks": []}}"""
            response = self.claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=1000, messages=[{"role": "user", "content": prompt}])
            raw_response = response.content[0].text
            print(f"   🤖 Raw: {raw_response[:80]}...")
            clean_response = self.clean_json_response(raw_response)
            try:
                analysis = json.loads(clean_response)
            except json.JSONDecodeError:
                json_match = re.search(r'\{.*\}', clean_response, re.DOTALL)
                if json_match:
                    analysis = json.loads(json_match.group())
                else:
                    raise
            if not analysis.get('create_tasks') or not analysis.get('tasks'):
                print(f"   ⏭️  No tasks to create")
                return
            print(f"   📝 Creating {len(analysis['tasks'])} task(s)...")
            for task in analysis['tasks']:
                self.create_task(task)
        except Exception as e:
            print(f"   ❌ Error: {e}")
    
    def create_task(self, task_data):
        try:
            business_id = self.businesses.get(task_data.get('business', 'Cloud Clean Energy'))
            if not business_id:
                business_id = self.businesses['Cloud Clean Energy']
            due_date_value = task_data.get('due_date')
            if not due_date_value or str(due_date_value).strip() == '':
                due_date_value = date.today().isoformat()
            else:
                try:
                    datetime.strptime(str(due_date_value), '%Y-%m-%d')
                    due_date_value = str(due_date_value)
                except:
                    due_date_value = date.today().isoformat()
            task_insert = {
                'business_id': business_id,
                'title': task_data['title'][:200],
                'description': task_data.get('description', '')[:1000],
                'priority': task_data.get('priority', 'medium'),
                'status': 'pending',
                'due_date': due_date_value
            }
            if task_data.get('due_time') and str(task_data.get('due_time')).strip():
                task_insert['due_time'] = task_data['due_time']
            if task_data.get('is_meeting') is not None:
                task_insert['is_meeting'] = task_data['is_meeting']
            result = self.tm.supabase.table('tasks').insert(task_insert).execute()
            if result.data:
                print(f"   ✅ Task: {task_data['title'][:50]}")
            else:
                print(f"   ❌ Failed to create task")
        except Exception as e:
            print(f"   ❌ Create error: {e}")
            import traceback
            traceback.print_exc()
    
    def start(self):
        schedule.every(15).minutes.do(self.process_emails)
        print("🚀 Processing emails on startup...")
        self.process_emails()
        print("🌐 Cloud scheduler started - Running 24/7!")
        print("📧 Email checks: Every 15 minutes")
        while True:
            schedule.run_pending()
            time.sleep(60)

if __name__ == "__main__":
    processor = CloudEmailProcessor()
    processor.start()
