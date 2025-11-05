import imaplib
import email
from email.header import decode_header
import os
import time
import schedule
from datetime import datetime, timedelta
import pytz
from anthropic import Anthropic
from enhanced_task_manager import EnhancedTaskManager
import json

class CloudEmailProcessor:
    def __init__(self):
        self.etm = EnhancedTaskManager()
        self.claude = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        
        # Gmail settings
        self.gmail_user = "robcrm.ai@gmail.com"
        self.gmail_password = os.getenv('GMAIL_APP_PASSWORD', 'sgho tbwr optz yxie')
        
        # Business mapping
        self.businesses = {
            'Cloud Clean Energy': 'feb14276-5c3d-4fcf-af06-9a8f54cf7159',
            'DSW (Direct Solar Warehouse)': '390fbfb9-1166-45a5-ba17-39c9c48d5f9a',
            'KVELL': 'e15518d2-39c2-4503-95bd-cb6f0b686022',
            'AI Project Pro': 'ec5d7aab-8d74-4ef2-9d92-01b143c68c82',
            'Veterans Health Centre (VHC)': '0b083ea5-ff45-4606-8cae-6ed387926641'
        }
        
        # Track processed emails to avoid duplicates
        self.processed_emails = set()
        
        print("🌐 Cloud Email Processor initialized!")
    
    def process_emails_job(self):
        """Check for new emails every 15 minutes - FIXED to get recent emails"""
        try:
            print(f"🔍 Checking emails at {datetime.now()}")
            
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            mail.login(self.gmail_user, self.gmail_password)
            mail.select('inbox')
            
            # FIXED: Search for emails from last 7 days regardless of read status
            aest = pytz.timezone('Australia/Brisbane')
            seven_days_ago = (datetime.now(aest) - timedelta(days=7)).strftime("%d-%b-%Y")
            
            # Search for emails since 7 days ago
            status, messages = mail.search(None, f'(SINCE {seven_days_ago})')
            
            if not messages[0]:
                print("📭 No emails in last 7 days")
                mail.close()
                mail.logout()
                return
            
            email_ids = messages[0].split()
            
            # Filter out already processed emails
            new_emails = [eid for eid in email_ids if eid not in self.processed_emails]
            
            if not new_emails:
                print("📭 No new emails (all already processed)")
                mail.close()
                mail.logout()
                return
            
            print(f"📬 Found {len(new_emails)} new emails to process")
            
            for msg_id in new_emails:
                try:
                    self.analyze_and_create_tasks(mail, msg_id)
                    self.processed_emails.add(msg_id)
                except Exception as e:
                    print(f"❌ Error processing email {msg_id}: {e}")
            
            mail.close()
            mail.logout()
            print("✅ Email processing completed")
            
        except Exception as e:
            print(f"❌ Email processing error: {e}")
    
    def analyze_and_create_tasks(self, mail, msg_id):
        """Use Claude AI to analyze and create tasks AUTOMATICALLY"""
        try:
            status, msg_data = mail.fetch(msg_id, '(RFC822)')
            email_body = email.message_from_bytes(msg_data[0][1])
            
            subject = decode_header(email_body['Subject'])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode()
            
            # Get email body
            body = ""
            if email_body.is_multipart():
                for part in email_body.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode()
                        break
            else:
                body = email_body.get_payload(decode=True).decode()
            
            sender = email_body.get('From', '')
            date_str = email_body.get('Date', '')
            
            print(f"📧 Processing: {subject[:50]}...")
            print(f"   From: {sender[:40]}")
            
            # Use Claude AI to analyze email
            prompt = f"""Analyze this email and extract task information.

Email Subject: {subject}
Email Body: {body}

Extract:
1. Is this a task-related email? (keywords: task, create, set, reminder, follow up, call, meeting, urgent)
2. If yes, extract:
   - Task title (concise, actionable)
   - Description
   - Business (choose from: Cloud Clean Energy, DSW, KVELL, AI Project Pro, VHC)
   - Priority (high/medium/low)
   - Due date (extract from email, format: YYYY-MM-DD)
   - Due time (if specified, format: HH:MM)

Respond ONLY with valid JSON:
{{
    "is_task": true/false,
    "tasks": [
        {{
            "title": "string",
            "description": "string",
            "business": "string",
            "priority": "string",
            "due_date": "YYYY-MM-DD or null",
            "due_time": "HH:MM or null"
        }}
    ]
}}"""

            # Call Claude AI
            response = self.claude.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse response
            analysis_text = response.content[0].text
            analysis = json.loads(analysis_text)
            
            # Create tasks automatically
            if analysis.get('is_task') and analysis.get('tasks'):
                for task_data in analysis['tasks']:
                    self.create_task_automatically(task_data, subject)
            else:
                print(f"   ⏭️  Not a task-related email")
            
        except json.JSONDecodeError as e:
            print(f"   ❌ AI response parsing error: {e}")
        except Exception as e:
            print(f"   ❌ Error in analyze_and_create_tasks: {e}")
    
    def create_task_automatically(self, task_data, email_subject):
        """Create task in database automatically (no approval needed)"""
        try:
            business_id = self.businesses.get(task_data['business'])
            if not business_id:
                print(f"   ⚠️  Unknown business '{task_data['business']}', using Cloud Clean Energy")
                business_id = self.businesses['Cloud Clean Energy']
            
            # Create task
            result = self.etm.supabase.table('tasks').insert({
                'business_id': business_id,
                'title': task_data['title'],
                'description': task_data.get('description', ''),
                'due_date': task_data.get('due_date'),
                'due_time': task_data.get('due_time'),
                'priority': task_data.get('priority', 'medium'),
                'status': 'pending',
                'email_source': email_subject
            }).execute()
            
            if result.data:
                task = result.data[0]
                print(f"   ✅ Task created: {task['title']}")
                print(f"      Business: {task_data['business']}")
                print(f"      Due: {task_data.get('due_date', 'None')} {task_data.get('due_time', '')}")
            else:
                print(f"   ❌ Failed to create task")
                
        except Exception as e:
            print(f"   ❌ Error creating task: {e}")
    
    def send_daily_summary_job(self):
        """Send daily summary at 8AM AEST"""
        try:
            print(f"📧 Sending daily summary at {datetime.now()}")
            self.etm.send_enhanced_daily_summary()
            print("✅ Daily summary sent")
        except Exception as e:
            print(f"❌ Daily summary failed: {e}")
    
    def start_cloud_scheduler(self):
        """Start 24/7 scheduler"""
        schedule.every(15).minutes.do(self.process_emails_job)
        schedule.every().day.at("22:00").do(self.send_daily_summary_job)
        
        # Process emails immediately on startup
        self.process_emails_job()
        
        print("🌐 Cloud scheduler started - Running 24/7!")
        print("📧 Email checks: Every 15 minutes")
        print("📊 Daily summaries: 8AM AEST")
        
        while True:
            schedule.run_pending()
            time.sleep(60)

if __name__ == "__main__":
    processor = CloudEmailProcessor()
    processor.start_cloud_scheduler()
