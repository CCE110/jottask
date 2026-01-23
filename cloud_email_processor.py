"""
Cloud Email Processor - AI-Powered Client Matching & Email Threading
Updated: December 3, 2025

Features:
- Smart client matching (email, name, project)
- Email threading (adds notes to existing tasks)
- AI extracts client info from emails
- Batch processing (newest 10 first)
- Message-ID deduplication
- AEST timezone support for date parsing and scheduling
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
        self.etm.action_url = self.action_url
        
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
        # Get current AEST time for AI context
        now_aest = datetime.now(self.aest)
        current_datetime = now_aest.strftime('%A, %d %B %Y at %I:%M %p AEST')
        
        prompt = f"""Analyze this email and extract information.

IMPORTANT - CURRENT DATE/TIME: {current_datetime}
Interpret ALL relative dates (today, tomorrow, this afternoon, next week, etc.) based on this time.
All times are Australian Eastern Standard Time (AEST/Brisbane timezone).

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
        # Skip matching for owner emails (always create new tasks)
        owner_emails = [
            "rob@cloudcleanenergy.com.au",
            "rob.l@directsolarwholesaler.com.au", 
            "robcrm.ai@gmail.com"
        ]
        client_email_check = (extracted_info.get('client_email') or '').lower()
        if client_email_check in owner_emails:
            print("   👤 Owner email - skipping client match, creating new task")
            return None
        
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

            # Check for CC follow-up pattern (CRM in CC, client in To)
            is_cc, client_info = self.is_cc_followup_email(email_body)
            if is_cc and client_info:
                # This is a CC'd email - create follow-up task
                self.process_cc_followup(email_body, message_id, client_info, sender_email, sender_name)
                return

            # Check for Project email (subject contains "project")
            if self.is_project_email(subject):
                self.process_project_email(email_body, message_id, sender_email)
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
                    now_aest = datetime.now(self.aest)
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
                
                # Send confirmation email to sender
                self.send_task_confirmation(sender_email, task, due_date, due_time)
            
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

    def parse_email_addresses(self, header_value):
        """Parse multiple email addresses from To/CC header"""
        import re

        if not header_value:
            return []

        results = []
        # Split by comma for multiple recipients
        parts = header_value.split(',')

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Extract email address
            match = re.search(r'<?([^<>\s]+@[^<>\s]+)>?', part)
            if match:
                email_addr = match.group(1).lower()

                # Extract name
                name_match = re.search(r'^([^<]+)<', part)
                name = name_match.group(1).strip().strip('"') if name_match else ''

                if not name:
                    name = email_addr.split('@')[0].replace('.', ' ').title()

                results.append({'email': email_addr, 'name': name})

        return results

    def is_cc_followup_email(self, email_body):
        """
        Check if this email is a CC'd follow-up request.
        Returns (is_cc, recipient_info) where recipient_info is the client from To field.
        """
        to_header = email_body.get('To', '')
        cc_header = email_body.get('Cc', '')

        # Parse all addresses
        to_addresses = self.parse_email_addresses(to_header)
        cc_addresses = self.parse_email_addresses(cc_header)

        # Check if CRM email is in CC (not in To)
        crm_email = self.gmail_user.lower()

        crm_in_to = any(addr['email'] == crm_email for addr in to_addresses)
        crm_in_cc = any(addr['email'] == crm_email for addr in cc_addresses)

        if crm_in_cc and not crm_in_to:
            # This is a CC'd email - extract the client from the To field
            # Filter out the CRM email and owner emails from recipients
            owner_emails = [
                "rob@cloudcleanenergy.com.au",
                "rob.l@directsolarwholesaler.com.au",
                "robcrm.ai@gmail.com"
            ]

            client = None
            for addr in to_addresses:
                if addr['email'] not in owner_emails and addr['email'] != crm_email:
                    client = addr
                    break

            return True, client

        return False, None

    def is_project_email(self, subject):
        """Check if this email is for a project (contains 'project' in subject)"""
        return 'project' in subject.lower()

    def extract_project_items(self, subject, content):
        """
        Use Claude AI to extract project name and to-do items from email.
        Returns: {project_name, items: [list of to-do strings]}
        """
        prompt = f"""Extract project information from this email.

SUBJECT: {subject}

CONTENT:
{content[:2000]}

---

Return a JSON object with:
{{
    "project_name": "Name of the project (from subject, cleaned up)",
    "items": ["item 1", "item 2", "item 3"]  // Each to-do item as a separate string
}}

Rules for extracting items:
1. Split comma-separated items into individual to-dos
2. Split line-separated items into individual to-dos
3. Each item should be a clear, actionable task
4. Clean up each item (remove leading numbers, dashes, bullets)
5. Keep items concise but complete

Example input: "Add project lists, add to do list, add build the system"
Example output items: ["Add project lists", "Add to do list", "Add build the system"]

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
            print(f"⚠️ AI project extraction error: {e}")
            # Fallback: basic parsing
            items = []
            # Try comma split first
            if ',' in content:
                items = [item.strip() for item in content.split(',') if item.strip()]
            # Try line split
            elif '\n' in content:
                items = [line.strip() for line in content.split('\n') if line.strip() and len(line.strip()) > 2]

            return {
                "project_name": subject.replace('project', '').replace('Project', '').strip(),
                "items": items[:20]  # Limit to 20 items
            }

    def process_project_email(self, email_body, message_id, sender_email):
        """
        Process an email as a project update.
        Creates/finds project and adds items.
        """
        try:
            subject = self.decode_email_header(email_body.get('Subject', 'No Subject'))
            content = self.get_email_content(email_body)

            print(f"   📁 Project email detected!")

            # Extract project info with AI
            extracted = self.extract_project_items(subject, content)
            project_name = extracted.get('project_name', subject)
            items = extracted.get('items', [])

            print(f"   📋 Project: {project_name}")
            print(f"   📝 Found {len(items)} to-do items")

            if not items:
                print(f"   ⚠️ No items found in email")
                return False

            # Get or create project
            project = self.tm.get_or_create_project(
                name=project_name,
                business_id=self.default_business_id
            )

            if not project:
                print(f"   ❌ Failed to create/find project")
                return False

            # Add each item
            added_count = 0
            for item in items:
                if item and len(item) > 2:  # Skip very short items
                    result = self.tm.add_project_item(
                        project_id=project['id'],
                        item_text=item,
                        source='email',
                        source_email_subject=subject
                    )
                    if result:
                        added_count += 1

            print(f"   ✅ Added {added_count} items to project: {project_name}")

            # Send confirmation email
            self.send_project_confirmation(sender_email, project, items, added_count)

            return True

        except Exception as e:
            print(f"   ❌ Error processing project email: {e}")
            import traceback
            traceback.print_exc()
            return False

    def send_project_confirmation(self, sender_email, project, items, added_count):
        """Send confirmation when items are added to a project"""
        try:
            project_name = project.get('name', 'Project')
            project_id = project.get('id', '')

            # Build items list HTML
            items_html = ""
            for item in items[:10]:  # Show first 10
                items_html += f'<li style="margin: 5px 0;">☐ {item}</li>'
            if len(items) > 10:
                items_html += f'<li style="margin: 5px 0; color: #6b7280;">...and {len(items) - 10} more</li>'

            html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; padding: 20px; max-width: 600px; margin: 0 auto;">

<div style="background: #ede9fe; border-left: 4px solid #8b5cf6; padding: 20px; border-radius: 8px;">

    <h2 style="color: #5b21b6; margin: 0 0 15px 0;">
        📁 Project Updated
    </h2>

    <div style="font-size: 18px; font-weight: bold; margin: 10px 0; color: #111827;">
        {project_name}
    </div>

    <div style="margin: 15px 0; padding: 15px; background: white; border-radius: 5px;">
        <div style="margin: 5px 0; font-weight: bold;">✅ Added {added_count} new items:</div>
        <ul style="margin: 10px 0; padding-left: 20px;">
            {items_html}
        </ul>
    </div>

    <p style="color: #6b7280; font-size: 14px;">
        You'll receive a daily Projects Summary at 7:00 AM AEST.
    </p>

    <div style="margin-top: 20px;">
        <a href="{self.action_url}?action=view_project&project_id={project_id}"
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #8b5cf6; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            📋 View Project
        </a>
        <a href="{self.action_url}?action=complete_all_project&project_id={project_id}"
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #10b981; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            ✅ Mark All Complete
        </a>
    </div>
</div>

<p style="color: #9ca3af; font-size: 12px; margin-top: 20px;">
    Sent by Rob's AI Task Manager • Projects Feature
</p>

</body>
</html>"""

            plain = f"""📁 Project Updated

{project_name}

✅ Added {added_count} new items:
{chr(10).join(['☐ ' + item for item in items[:10]])}

You'll receive a daily Projects Summary at 7:00 AM AEST.
"""

            self.etm.send_html_email(
                sender_email,
                f"📁 Project Updated: {project_name}",
                html,
                plain
            )
            print(f"   📨 Project confirmation sent to {sender_email}")

        except Exception as e:
            print(f"   ⚠️ Project confirmation email failed: {e}")

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
    
    def process_cc_followup(self, email_body, message_id, client_info, sender_email, sender_name):
        """
        Process a CC'd email as a follow-up reminder.
        Creates a task to follow up with the client in 1 day.
        """
        try:
            subject = self.decode_email_header(email_body.get('Subject', 'No Subject'))
            content = self.get_email_content(email_body)

            client_name = client_info.get('name', 'Unknown')
            client_email = client_info.get('email', '')

            print(f"   📬 CC Follow-up detected!")
            print(f"   👤 Client: {client_name} <{client_email}>")

            # Calculate due date: next business day at 9 AM
            now_aest = datetime.now(self.aest)
            next_day = now_aest + timedelta(days=1)

            # Skip weekends
            while next_day.weekday() >= 5:  # 5=Sat, 6=Sun
                next_day += timedelta(days=1)

            due_date = next_day.date().isoformat()
            due_time = '09:00:00'

            # Get "Remember to Callback" status
            status = self.tm.get_status_by_name('Remember to Callback')

            # Create task title
            task_title = f"Follow up with {client_name}"

            # Create description from email context
            task_description = f"""CC Follow-up from email sent to {client_name}

Original Subject: {subject}

Email Preview:
{content[:500]}"""

            # Create the task
            task = self.tm.create_task(
                business_id=self.default_business_id,
                title=task_title[:200],
                description=task_description,
                due_date=due_date,
                due_time=due_time,
                priority='medium',
                client_name=client_name,
                client_email=client_email,
                client_phone=None,
                project_name=None,
                initial_note=f"Auto-created from CC'd email: {subject}\n\nSent by: {sender_name} <{sender_email}>",
                note_source='email'
            )

            if task:
                # Update status
                if status and self.tm.statuses_available():
                    self.tm.update_task_status(task['id'], status['id'])

                print(f"   ✅ Follow-up task created: {task_title}")
                print(f"   📅 Due: {due_date} at 9:00 AM")

                # Send confirmation to the sender (who CC'd the CRM)
                self.send_cc_followup_confirmation(sender_email, task, client_name, due_date)

                return True

            return False

        except Exception as e:
            print(f"   ❌ Error creating CC follow-up: {e}")
            import traceback
            traceback.print_exc()
            return False

    def send_cc_followup_confirmation(self, sender_email, task, client_name, due_date):
        """Send confirmation when a CC follow-up task is created"""
        try:
            # Format due date
            try:
                due_dt = datetime.strptime(str(due_date), '%Y-%m-%d')
                date_formatted = due_dt.strftime('%A, %d %b %Y')
            except:
                date_formatted = str(due_date)

            task_id = task.get('id', '')
            title = task.get('title', 'Task')

            html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; padding: 20px; max-width: 600px; margin: 0 auto;">

<div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 20px; border-radius: 8px;">

    <h2 style="color: #92400e; margin: 0 0 15px 0;">
        📞 Follow-up Reminder Created
    </h2>

    <div style="font-size: 18px; font-weight: bold; margin: 10px 0; color: #111827;">
        {title}
    </div>

    <div style="margin: 15px 0; padding: 15px; background: white; border-radius: 5px;">
        <div style="margin: 5px 0;">👤 <strong>Client:</strong> {client_name}</div>
        <div style="margin: 5px 0;">📅 <strong>Follow up:</strong> {date_formatted}</div>
        <div style="margin: 5px 0;">⏰ <strong>Time:</strong> 9:00 AM</div>
    </div>

    <p style="color: #6b7280; font-size: 14px;">
        You will receive a reminder when it's time to follow up.
    </p>

    <div style="margin-top: 20px;">
        <a href="{self.action_url}?action=complete&task_id={task_id}"
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #10b981; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            ✅ Complete
        </a>
        <a href="{self.action_url}?action=delay_1hour&task_id={task_id}"
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #6b7280; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            ⏰ +1 Hour
        </a>
        <a href="{self.action_url}?action=delay_1day&task_id={task_id}"
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #6b7280; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            📅 +1 Day
        </a>
        <a href="{self.action_url}?action=delay_custom&task_id={task_id}"
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #6b7280; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            🗓️ Change Time
        </a>
    </div>
</div>

<p style="color: #9ca3af; font-size: 12px; margin-top: 20px;">
    Sent by Rob's AI Task Manager • CC Follow-up Feature
</p>

</body>
</html>"""

            plain = f"""📞 Follow-up Reminder Created

{title}

👤 Client: {client_name}
📅 Follow up: {date_formatted}
⏰ Time: 9:00 AM

You will receive a reminder when it's time to follow up.

Actions:
- Complete: {self.action_url}?action=complete&task_id={task_id}
- +1 Hour: {self.action_url}?action=delay_1hour&task_id={task_id}
- +1 Day: {self.action_url}?action=delay_1day&task_id={task_id}
- Change Time: {self.action_url}?action=delay_custom&task_id={task_id}
"""

            # Send to the person who CC'd the CRM
            self.etm.send_html_email(
                sender_email,
                f"📞 Follow-up Set: {client_name}",
                html,
                plain
            )
            print(f"   📨 CC confirmation sent to {sender_email}")

        except Exception as e:
            print(f"   ⚠️ CC confirmation email failed: {e}")

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
            "rob's ai task manager"
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
    
    def send_task_confirmation(self, sender_email, task, due_date, due_time):
        """Send confirmation email when task is created"""
        try:
            # Parse due time for display
            if due_time:
                try:
                    parts = str(due_time).split(':')
                    hour, minute = int(parts[0]), int(parts[1])
                    time_12hr = datetime.now().replace(hour=hour, minute=minute).strftime('%I:%M %p')
                except:
                    time_12hr = str(due_time)
            else:
                time_12hr = "No time set"
            
            # Format due date
            try:
                due_dt = datetime.strptime(str(due_date), '%Y-%m-%d')
                date_formatted = due_dt.strftime('%A, %d %b %Y')
            except:
                date_formatted = str(due_date)
            
            task_id = task.get('id', '')
            title = task.get('title', 'Task')
            
            html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; padding: 20px; max-width: 600px; margin: 0 auto;">

<div style="background: #ecfdf5; border-left: 4px solid #10b981; padding: 20px; border-radius: 8px;">
    
    <h2 style="color: #065f46; margin: 0 0 15px 0;">
        ✅ Task Created
    </h2>
    
    <div style="font-size: 18px; font-weight: bold; margin: 10px 0; color: #111827;">
        {title}
    </div>
    
    <div style="margin: 15px 0; padding: 15px; background: white; border-radius: 5px;">
        <div style="margin: 5px 0;">📅 <strong>Due:</strong> {date_formatted}</div>
        <div style="margin: 5px 0;">⏰ <strong>Time:</strong> {time_12hr}</div>
    </div>
    
    <p style="color: #6b7280; font-size: 14px;">
        You will receive a reminder 5-20 minutes before this is due.
    </p>
    
    <div style="margin-top: 20px;">
        <a href="{self.action_url}?action=complete&task_id={task_id}" 
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #10b981; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            ✅ Complete
        </a>
        <a href="{self.action_url}?action=delay_1hour&task_id={task_id}" 
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #6b7280; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            ⏰ +1 Hour
        </a>
        <a href="{self.action_url}?action=delay_1day&task_id={task_id}" 
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #6b7280; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            📅 +1 Day
        </a>
        <a href="{self.action_url}?action=delay_custom&task_id={task_id}" 
           style="display: inline-block; padding: 10px 16px; margin: 5px; background: #6b7280; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
            🗓️ Change Time
        </a>
    </div>
</div>

<p style="color: #9ca3af; font-size: 12px; margin-top: 20px;">
    Sent by Rob's AI Task Manager
</p>

</body>
</html>"""

            plain = f"""✅ Task Created

{title}

📅 Due: {date_formatted}
⏰ Time: {time_12hr}

You will receive a reminder 5-20 minutes before this is due.

Actions:
- Complete: {self.action_url}?action=complete&task_id={task_id}
- +1 Hour: {self.action_url}?action=delay_1hour&task_id={task_id}
- +1 Day: {self.action_url}?action=delay_1day&task_id={task_id}
- Change Time: {self.action_url}?action=delay_custom&task_id={task_id}
"""

            # Send to the original sender
            self.etm.send_html_email(
                sender_email,
                f"✅ Task Set: {title[:50]}",
                html,
                plain
            )
            print(f"   📨 Confirmation sent to {sender_email}")
            
        except Exception as e:
            print(f"   ⚠️ Confirmation email failed: {e}")

    # ========================================
    # PROJECTS DAILY SUMMARY (7 AM AEST)
    # ========================================

    def send_projects_summary(self):
        """Send daily projects summary email at 7 AM AEST"""
        try:
            print(f"\n📁 Generating Projects Summary...")

            # Get all active projects
            projects = self.tm.get_active_projects(business_id=self.default_business_id)

            if not projects:
                print(f"   ℹ️ No active projects")
                return

            print(f"   📋 Found {len(projects)} active projects")

            # Build HTML email
            now = datetime.now(self.aest)
            date_str = now.strftime('%A, %d %B %Y')

            projects_html = ""
            for project in projects:
                project_id = project['id']
                project_name = project['name']
                items = project.get('items', [])
                progress = project.get('progress', {'total': 0, 'completed': 0, 'percent': 0})

                # Build items list
                items_html = ""
                incomplete_items = [i for i in items if not i['is_completed']]
                complete_items = [i for i in items if i['is_completed']]

                # Show incomplete items first
                for item in incomplete_items[:10]:
                    item_id = item['id']
                    items_html += f'''
                    <div style="margin: 8px 0; display: flex; align-items: center;">
                        <a href="{self.action_url}?action=complete_project_item&item_id={item_id}&project_id={project_id}"
                           style="text-decoration: none; color: #6b7280; margin-right: 8px;">☐</a>
                        <span>{item['item_text']}</span>
                    </div>'''

                # Show completed count if any
                if complete_items:
                    items_html += f'''
                    <div style="margin: 8px 0; color: #10b981; font-size: 13px;">
                        ✅ {len(complete_items)} completed items
                    </div>'''

                # Progress bar
                progress_color = '#10b981' if progress['percent'] >= 70 else '#f59e0b' if progress['percent'] >= 30 else '#6b7280'

                projects_html += f'''
                <div style="background: white; border-radius: 8px; padding: 20px; margin-bottom: 15px; border: 1px solid #e5e7eb;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <h3 style="margin: 0; color: #111827; font-size: 16px;">📁 {project_name}</h3>
                        <span style="font-size: 13px; color: #6b7280;">{progress['completed']}/{progress['total']} done</span>
                    </div>

                    <div style="background: #e5e7eb; border-radius: 4px; height: 6px; margin-bottom: 15px;">
                        <div style="background: {progress_color}; border-radius: 4px; height: 6px; width: {progress['percent']}%;"></div>
                    </div>

                    <div style="margin-bottom: 15px;">
                        {items_html}
                    </div>

                    <div>
                        <a href="{self.action_url}?action=view_project&project_id={project_id}"
                           style="display: inline-block; padding: 6px 12px; margin-right: 5px; background: #8b5cf6; color: white; text-decoration: none; border-radius: 4px; font-size: 13px;">
                            📋 View All
                        </a>
                        <a href="{self.action_url}?action=add_project_item&project_id={project_id}"
                           style="display: inline-block; padding: 6px 12px; margin-right: 5px; background: #3b82f6; color: white; text-decoration: none; border-radius: 4px; font-size: 13px;">
                            ➕ Add Item
                        </a>
                    </div>
                </div>'''

            # Calculate total stats
            total_items = sum(p['progress']['total'] for p in projects)
            total_completed = sum(p['progress']['completed'] for p in projects)
            overall_percent = int((total_completed / total_items * 100) if total_items > 0 else 0)

            html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; padding: 20px; max-width: 700px; margin: 0 auto; background: #f9fafb;">

<div style="background: linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%); color: white; padding: 25px; border-radius: 12px 12px 0 0;">
    <h1 style="margin: 0 0 5px 0; font-size: 24px;">📁 Projects Summary</h1>
    <p style="margin: 0; opacity: 0.9;">{date_str}</p>
</div>

<div style="background: white; padding: 20px; border-radius: 0 0 12px 12px; border: 1px solid #e5e7eb; border-top: none;">

    <div style="display: flex; justify-content: space-around; text-align: center; padding: 15px; background: #f3f4f6; border-radius: 8px; margin-bottom: 20px;">
        <div>
            <div style="font-size: 28px; font-weight: bold; color: #8b5cf6;">{len(projects)}</div>
            <div style="font-size: 13px; color: #6b7280;">Active Projects</div>
        </div>
        <div>
            <div style="font-size: 28px; font-weight: bold; color: #10b981;">{total_completed}</div>
            <div style="font-size: 13px; color: #6b7280;">Completed</div>
        </div>
        <div>
            <div style="font-size: 28px; font-weight: bold; color: #f59e0b;">{total_items - total_completed}</div>
            <div style="font-size: 13px; color: #6b7280;">Remaining</div>
        </div>
    </div>

    {projects_html}

</div>

<p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 20px;">
    Sent by Rob's AI Task Manager • Projects Summary at 7:00 AM AEST
</p>

</body>
</html>"""

            # Plain text version
            plain_lines = [f"📁 Projects Summary - {date_str}", ""]
            for project in projects:
                plain_lines.append(f"📁 {project['name']} ({project['progress']['completed']}/{project['progress']['total']} done)")
                for item in project.get('items', []):
                    if not item['is_completed']:
                        plain_lines.append(f"   ☐ {item['item_text']}")
                plain_lines.append("")

            plain = "\n".join(plain_lines)

            # Send email
            self.etm.send_html_email(
                self.your_email,
                f"📁 Projects Summary | {len(projects)} Projects | {date_str}",
                html,
                plain
            )
            print(f"   ✅ Projects summary sent ({len(projects)} projects)")

        except Exception as e:
            print(f"❌ Projects summary error: {e}")
            import traceback
            traceback.print_exc()

    # ========================================
    # MAIN SCHEDULER
    # ========================================

    def start(self):
        """Start the 24/7 scheduler daemon"""
        
        # Initialize timestamps with AEST timezone
        last_email_check = datetime.now(self.aest)
        last_reminder_check = datetime.now(self.aest)
        last_summary_check = datetime.now(self.aest)
        last_projects_check = datetime.now(self.aest)

        print("\n" + "="*50)
        print("🌐 Cloud Email Processor Started")
        print("="*50)
        print(f"📧 Email checks: Every 15 minutes")
        print(f"⏰ Reminder checks: Every 15 minutes")
        print(f"📁 Projects summary: 7:00 AM AEST")
        print(f"📊 Daily task summary: 8:00 AM AEST")
        print(f"🎯 Smart client matching: ENABLED")
        print(f"🤖 AI summarization: ENABLED")
        print("="*50 + "\n")
        
        while True:
            now = datetime.now(self.aest)  # AEST timezone!
            
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

            # Projects summary at 7 AM AEST
            if now.hour == 7 and now.minute < 15:
                if (now - last_projects_check).total_seconds() >= 3600:
                    print(f"\n⏰ 7 AM AEST - sending projects summary")
                    self.send_projects_summary()
                    last_projects_check = now

            # Daily task summary at 8 AM AEST
            if now.hour == 8 and now.minute < 15:
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