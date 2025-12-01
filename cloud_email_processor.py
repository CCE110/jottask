"""
Cloud Email Processor - AI-Powered Client Matching & Email Threading
Updated: November 28, 2025

Features:
- Smart client matching (email, name, project)
- Email threading (adds notes to existing tasks)
- AI extracts client info from emails
- Batch processing (newest 10 first)
- Message-ID deduplication
"""

import os
import time
import email
import imaplib
import json
from datetime import datetime, date, timedelta
from email.header import decode_header
import pytz
from anthropic import Anthropic

from task_manager import TaskManager
from enhanced_task_manager import EnhancedTaskManager


class CloudEmailProcessor:
    def __init__(self):
        print("🚀 Initializing Cloud Email Processor...")
        
        # Core services
        self.tm = TaskManager()
        self.etm = EnhancedTaskManager(self.tm)
        self.anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        
        # Email config
        self.gmail_user = 'robcrm.ai@gmail.com'
        self.gmail_pass = os.getenv('GMAIL_APP_PASSWORD')
        self.your_email = 'rob@cloudcleanenergy.com.au'
        
        # Action URL for email buttons
        self.action_url = os.getenv('TASK_ACTION_URL', 
            'https://rob-crm-tasks-production.up.railway.app/action')
        
        # Timezone
        self.aest = pytz.timezone('Australia/Brisbane')
        
        # Load processed emails from database
        self.processed_emails = self.load_processed_emails()
        
        # Default business ID (Cloud Clean Energy)
        self.default_business_id = 'feb14276-5c3d-4fcf-af06-9a8f54cf7159'
        
        print(f"✅ Processor initialized")
        print(f"📧 Gmail: {self.gmail_user}")
        print(f"🔗 Action URL: {self.action_url}")
    
    # ========================================
    # EMAIL DEDUPLICATION
    # ========================================
    
    def load_processed_emails(self):
        """Load processed email Message-IDs from database"""
        try:
            result = self.tm.supabase.table('processed_emails')\
                .select('email_id')\
                .execute()
            
            email_ids = set(e['email_id'] for e in result.data)
            print(f"📊 Loaded {len(email_ids)} processed emails")
            return email_ids
            
        except Exception as e:
            print(f"⚠️ Error loading processed emails: {e}")
            return set()
    
    def mark_email_processed(self, message_id):
        """Mark email as processed in database"""
        try:
            self.tm.supabase.table('processed_emails').insert({
                'email_id': message_id
            }).execute()
            self.processed_emails.add(message_id)
        except:
            pass  # Duplicate key - already processed
    
    # ========================================
    # AI CLIENT EXTRACTION & MATCHING
    # ========================================
    
    def extract_client_and_task_info(self, subject, content, sender_email, sender_name):
        """
        Use Claude AI to:
        1. Determine if this is a task-worthy email
        2. Extract client information
        3. Identify if this relates to existing project
        4. Parse task details
        """
        prompt = f"""Analyze this email and extract information.

FROM: {sender_name} <{sender_email}>
SUBJECT: {subject}

CONTENT:
{content[:2000]}

---

Return a JSON object with these fields:

{{
    "is_task": true/false,           // Is this something requiring action?
    "is_followup": true/false,       // Is this a reply/followup to existing conversation?
    
    "client_name": "Full Name",      // Best guess at client's full name
    "client_email": "email@x.com",   // Client's email address
    "client_phone": "phone",         // Phone number if mentioned (or null)
    
    "project_name": "Project Name",  // Project/job name if identifiable (or null)
    "project_keywords": ["solar", "battery"],  // Keywords to match existing projects
    
    "task_title": "Brief task title",
    "task_description": "What needs to be done",
    "task_priority": "high/medium/low",
    
    "suggested_status": "Remember to Callback/Research/Build Quotation/etc",
    
    "due_date": "YYYY-MM-DD",        // Suggested due date (or null for today)
    "due_time": "HH:MM:SS",          // Suggested time (or null for 09:00:00)
    
    "note_content": "Key points from email for notes"
}}

Rules:
- is_task = true if email requires any follow-up action
- Extract client name from signature, email address, or content
- project_keywords should help match this to existing tasks
- If "Re:" or "FW:" in subject, is_followup = true
- suggested_status: Use "Remember to Callback" for new inquiries, 
  "Build Quotation" for quote requests, "Research" for technical questions
- note_content should summarize the key points (max 500 chars)

Return ONLY valid JSON, no explanation."""

        try:
            response = self.anthropic.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            text = response.content[0].text.strip()
            
            # Clean up JSON if wrapped in code blocks
            if text.startswith('```'):
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]
            text = text.strip()
            
            return json.loads(text)
            
        except Exception as e:
            print(f"⚠️ AI extraction error: {e}")
            # Return minimal default
            return {
                "is_task": True,
                "is_followup": "re:" in subject.lower(),
                "client_name": sender_name or sender_email.split('@')[0],
                "client_email": sender_email,
                "task_title": subject,
                "task_description": content[:200],
                "task_priority": "medium",
                "suggested_status": "Remember to Callback",
                "note_content": content[:300]
            }
    
    def find_matching_task(self, extracted_info):
        """
        Smart matching to find existing task for this client/project.
        Uses multiple strategies.
        """
        client_email = extracted_info.get('client_email')
        client_name = extracted_info.get('client_name')
        project_name = extracted_info.get('project_name')
        project_keywords = extracted_info.get('project_keywords', [])
        
        # Strategy 1: Exact email match
        existing = self.tm.find_existing_task_by_client(
            client_email=client_email,
            client_name=client_name,
            project_name=project_name
        )
        
        if existing:
            return existing
        
        # Strategy 2: Keyword search in recent tasks
        if project_keywords:
            try:
                for keyword in project_keywords:
                    result = self.tm.supabase.table('tasks')\
                        .select('*')\
                        .neq('status', 'completed')\
                        .or_(f"title.ilike.%{keyword}%,project_name.ilike.%{keyword}%")\
                        .order('created_at', desc=True)\
                        .limit(1)\
                        .execute()
                    
                    if result.data:
                        # Verify it's the same client (if we have email)
                        task = result.data[0]
                        if client_email and task.get('client_email'):
                            if task['client_email'].lower() == client_email.lower():
                                print(f"🔗 Found match by keyword: {keyword}")
                                return task
                        elif client_name and task.get('client_name'):
                            if client_name.lower() in task['client_name'].lower():
                                print(f"🔗 Found match by keyword + name: {keyword}")
                                return task
            except Exception as e:
                print(f"⚠️ Keyword search error: {e}")
        
        return None
    
    # ========================================
    # EMAIL PROCESSING
    # ========================================
    
    def process_emails(self):
        """Process incoming emails with smart client matching"""
        try:
            print(f"\n🔍 Checking emails at {datetime.now(self.aest).strftime('%I:%M %p')}")
            
            # Connect to Gmail
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            mail.login(self.gmail_user, self.gmail_pass)
            mail.select('INBOX')
            
            # Search for recent emails (last 7 days)
            seven_days_ago = (date.today() - timedelta(days=7)).strftime("%d-%b-%Y")
            status, messages = mail.uid("search", None, f'(SINCE {seven_days_ago})')
            
            if status != 'OK':
                print("   ❌ Failed to search emails")
                return
            
            email_ids = messages[0].split()
            
            # Count new emails
            new_count = len([eid for eid in email_ids if eid.decode() not in self.processed_emails])
            
            if new_count == 0:
                print("📭 No new emails")
                mail.close()
                mail.logout()
                return
            
            print(f"📬 Found {len(email_ids)} total ({new_count} new)")
            
            # Process NEWEST 10 emails first (critical fix from Nov 12)
            processed_count = 0
            for msg_id in reversed(email_ids[-10:]):
                msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
                
                # Fetch email
                status, msg_data = mail.uid("fetch", msg_id, '(RFC822)')
                if status != 'OK':
                    continue
                
                email_body = email.message_from_bytes(msg_data[0][1])
                message_id = email_body.get('Message-ID', msg_id_str)
                
                # Skip if already processed
                if message_id in self.processed_emails:
                    continue
                
                # Process this email
                self.process_single_email(email_body, message_id)
                processed_count += 1
                
                # Mark as processed
                self.mark_email_processed(message_id)
            
            print(f"✅ Processed {processed_count} emails")
            
            mail.close()
            mail.logout()
            
        except Exception as e:
            print(f"❌ Email processing error: {e}")
            import traceback
            traceback.print_exc()
    
    def process_single_email(self, email_body, message_id):
        """Process a single email with AI client matching"""
        try:
            # Extract basic info
            subject = self.decode_email_header(email_body.get('Subject', 'No Subject'))
            from_header = email_body.get('From', '')
            sender_email, sender_name = self.parse_from_header(from_header)
            email_date = email_body.get('Date', '')
            
            print(f"\n📧 Processing: {subject[:50]}")
            print(f"   From: {sender_name} <{sender_email}>")
            
            # Get email content
            content = self.get_email_content(email_body)
            
            # Skip system emails
            if self.is_system_email(sender_email, subject):
                print(f"   ⭕ Skipping system email")
                return
            
            # AI extraction
            print(f"   🤖 Analyzing with AI...")
            extracted = self.extract_client_and_task_info(
                subject, content, sender_email, sender_name
            )
            
            if not extracted.get('is_task'):
                print(f"   ⭕ Not a task-worthy email")
                return
            
            # Check for existing task
            existing_task = self.find_matching_task(extracted)
            
            if existing_task:
                # Add note to existing task
                print(f"   📎 Adding note to existing task: {existing_task['title'][:40]}")
                
                self.tm.add_note(
                    task_id=existing_task['id'],
                    content=extracted.get('note_content', content[:500]),
                    source='email',
                    email_subject=subject,
                    email_from=sender_email,
                    email_date=email_date
                )
                
                # Update client info if missing
                if not existing_task.get('client_email') and sender_email:
                    self.tm.update_task_client_info(
                        existing_task['id'],
                        client_email=sender_email,
                        client_name=extracted.get('client_name')
                    )
                
                print(f"   ✅ Note added to existing task")
                
            else:
                # Create new task
                print(f"   🆕 Creating new task...")
                
                # Get status ID
                status_name = extracted.get('suggested_status', 'Remember to Callback')
                status = self.tm.get_status_by_name(status_name)
                
                # Build task data
                due_date = extracted.get('due_date') or date.today().isoformat()
                # Smart default: next business day 9 AM if no time specified and past 9 AM
                if extracted.get('due_time'):
                    due_time = extracted.get('due_time')
                else:
                    now_aest = datetime.now(pytz.timezone('Australia/Brisbane'))
                    if now_aest.hour < 9:
                        # Before 9 AM - use today 9 AM
                        due_time = '09:00:00'
                    else:
                        # After 9 AM - use next business day 9 AM
                        next_day = now_aest + timedelta(days=1)
                        # Skip weekends
                        while next_day.weekday() >= 5:  # 5=Sat, 6=Sun
                            next_day += timedelta(days=1)
                        due_date = next_day.date().isoformat()
                        due_time = '09:00:00'
                
                task = self.tm.create_task(
                    business_id=self.default_business_id,
                    title=extracted.get('task_title', subject)[:200],
                    description=extracted.get('task_description', ''),
                    due_date=due_date,
                    due_time=due_time,
                    priority=extracted.get('task_priority', 'medium'),
                    client_name=extracted.get('client_name'),
                    client_email=extracted.get('client_email'),
                    client_phone=extracted.get('client_phone'),
                    project_name=extracted.get('project_name'),
                    initial_note=extracted.get('note_content', content[:500]),
                    note_source='email'
                )
                
                if task:
                    # Update status only if statuses are available
                    if status and self.tm.statuses_available():
                        self.tm.update_task_status(task['id'], status['id'])
                
                print(f"   ✅ Task created: {extracted.get('task_title', subject)[:40]}")
            
        except Exception as e:
            print(f"   ❌ Error processing email: {e}")
            import traceback
            traceback.print_exc()
    
    def decode_email_header(self, header):
        """Decode email header (handles encoded subjects)"""
        if not header:
            return ''
        
        decoded_parts = decode_header(header)
        result = ''
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result += part.decode(encoding or 'utf-8', errors='replace')
            else:
                result += part
        return result
    
    def parse_from_header(self, from_header):
        """Extract email and name from From header"""
        import re
        
        # Pattern: "Name <email@domain.com>" or just "email@domain.com"
        match = re.search(r'<?([^<>\s]+@[^<>\s]+)>?', from_header)
        email_addr = match.group(1) if match else from_header
        
        # Extract name
        name_match = re.search(r'^([^<]+)<', from_header)
        name = name_match.group(1).strip().strip('"') if name_match else ''
        
        if not name:
            name = email_addr.split('@')[0].replace('.', ' ').title()
        
        return email_addr.lower(), name
    
    def get_email_content(self, email_body):
        """Extract text content from email"""
        content = ''
        
        if email_body.is_multipart():
            for part in email_body.walk():
                content_type = part.get_content_type()
                if content_type == 'text/plain':
                    try:
                        payload = part.get_payload(decode=True)
                        content = payload.decode('utf-8', errors='replace')
                        break
                    except:
                        pass
        else:
            try:
                payload = email_body.get_payload(decode=True)
                content = payload.decode('utf-8', errors='replace')
            except:
                content = str(email_body.get_payload())
        
        return content[:3000]  # Limit for AI processing
    
    def is_system_email(self, sender_email, subject):
        """Check if email is from system/notification or CRM-generated"""
        # Check sender patterns (automated senders)
        system_sender_patterns = [
            'noreply', 'no-reply', 'donotreply', 'mailer-daemon',
            'postmaster', 'notification@', 'alerts@', 'system@'
        ]
        
        sender_lower = sender_email.lower()
        for pattern in system_sender_patterns:
            if pattern in sender_lower:
                return True
        
        # Check if this is a CRM-generated email (reminders/summaries we sent)
        # These have specific subject patterns
        subject_lower = subject.lower()
        crm_subject_patterns = [
            '⏰',                    # Reminder emoji
            'task reminder',
            'daily summary',
            '📊 daily summary',
            'rob's ai task manager'
        ]
        
        for pattern in crm_subject_patterns:
            if pattern in subject_lower:
                return True
        
        # Skip emails FROM the CRM inbox itself (robcrm.ai@gmail.com)
        # But NOT from rob@cloudcleanenergy.com.au - that's where task requests come from!
        if sender_lower == self.gmail_user.lower():
            return True
        
        return False
    
    # ========================================
    # REMINDER SYSTEM
    # ========================================
    
    def send_task_reminders(self):
        """Check for tasks due soon and send reminders"""
        print(f"\n🔔 Checking reminders at {datetime.now(self.aest).strftime('%I:%M %p')}")
        
        try:
            now = datetime.now(self.aest)
            today_str = now.date().isoformat()
            
            # Get pending tasks due today with status info
            result = self.tm.supabase.table('tasks')\
                .select('*, project_statuses(*)')\
                .eq('status', 'pending')\
                .eq('due_date', today_str)\
                .execute()
            
            tasks = result.data
            print(f"   📋 Found {len(tasks)} tasks due today")
            
            # Filter tasks with due_time
            tasks_with_time = [t for t in tasks if t.get('due_time')]
            
            if not tasks_with_time:
                print("   ℹ️ No tasks with due times")
                return
            
            sent_count = 0
            
            for task in tasks_with_time:
                try:
                    # Parse due time
                    due_time_str = task['due_time']
                    parts = due_time_str.split(':')
                    hour, minute = int(parts[0]), int(parts[1])
                    second = int(float(parts[2])) if len(parts) > 2 else 0
                    
                    task_due = now.replace(
                        hour=hour, 
                        minute=minute, 
                        second=second, 
                        microsecond=0
                    )
                    
                    # Calculate time difference
                    time_diff = (task_due - now).total_seconds() / 60
                    
                    # 5-20 minute window
                    if -5 <= time_diff <= 20:  # Include recently overdue (up to 5 min late)
                        print(f"   ✅ Sending reminder: {task['title'][:40]}")
                        
                        self.etm.send_task_reminder(
                            task=task,
                            due_time=task_due,
                            action_url=self.action_url
                        )
                        sent_count += 1
                        
                        # Rate limit (Resend: 2/sec)
                        time.sleep(0.6)
                    
                except Exception as e:
                    print(f"   ⚠️ Error with task {task.get('id')}: {e}")
                    continue
            
            if sent_count > 0:
                print(f"   ✅ Sent {sent_count} reminder(s)")
            else:
                print(f"   ℹ️ No tasks in 5-20 min window")
                
        except Exception as e:
            print(f"❌ Reminder error: {e}")
            import traceback
            traceback.print_exc()
    
    # ========================================
    # MAIN SCHEDULER
    # ========================================
    
    def start(self):
        """Start the 24/7 scheduler daemon"""
        
        # Initialize timestamps
        last_email_check = datetime.now()
        last_reminder_check = datetime.now()
        last_summary_check = datetime.now()
        
        print("\n" + "="*50)
        print("🌐 Cloud Email Processor Started")
        print("="*50)
        print(f"📧 Email checks: Every 15 minutes")
        print(f"⏰ Reminder checks: Every 15 minutes")
        print(f"📊 Daily summary: 8:00 AM AEST")
        print(f"🎯 Smart client matching: ENABLED")
        print(f"🤖 AI summarization: ENABLED")
        print("="*50 + "\n")
        
        while True:
            now = datetime.now()
            
            # Email check every 15 minutes
            if (now - last_email_check).total_seconds() >= 900:
                print(f"\n⏰ 15 min elapsed - checking emails")
                self.process_emails()
                last_email_check = now
            
            # Reminder check every 15 minutes
            if (now - last_reminder_check).total_seconds() >= 900:
                print(f"\n⏰ 15 min elapsed - checking reminders")
                self.send_task_reminders()
                last_reminder_check = now
            
            # Daily summary at 8 AM AEST (22:00 UTC)
            if now.hour == 22 and now.minute < 15:
                if (now - last_summary_check).total_seconds() >= 3600:
                    print(f"\n⏰ 8 AM AEST - sending daily summary")
                    self.etm.send_enhanced_daily_summary()
                    last_summary_check = now
            
            # Sleep 60 seconds
            time.sleep(60)


# ========================================
# ENTRY POINT
# ========================================

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    processor = CloudEmailProcessor()
    processor.start()
