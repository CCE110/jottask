#!/usr/bin/env python3
"""
AI Email Task Processor v2
Upgraded with:
- Solar sales pipeline awareness (DSW/Cloud Clean Energy workflow)
- Plaud voice transcription detection & multi-action parsing
- Tiered action system (auto-execute vs email approval)
- Email-based approval flow (replaces terminal input)
- Jottask category intelligence
"""

import imaplib
import email
from email.header import decode_header
import json
import re
from datetime import datetime, date, timedelta
from task_manager import TaskManager
from anthropic import Anthropic
from dotenv import load_dotenv
import os
import uuid
import hashlib
import resend
import pytz

load_dotenv()


class AIEmailProcessor:
    def __init__(self):
        self.tm = TaskManager()
        self.aest = pytz.timezone('Australia/Brisbane')
        self.claude = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

        # Email settings - Jottask inbound email (jottask@flowquote.ai on privateemail.com)
        self.email_user = os.getenv('JOTTASK_EMAIL', 'jottask@flowquote.ai')
        self.email_password = os.getenv('JOTTASK_EMAIL_PASSWORD')
        self.imap_server = os.getenv('IMAP_SERVER', 'mail.privateemail.com')

        # Resend for outbound emails (approval emails etc)
        resend.api_key = os.getenv('RESEND_API_KEY')
        self.from_email = os.getenv('FROM_EMAIL', 'admin@flowquote.ai')

        # Business IDs
        self.businesses = {
            'Cloud Clean Energy': os.getenv('BUSINESS_ID_CCE', 'feb14276-5c3d-4fcf-af06-9a8f54cf7159'),
            'AI Project Pro': os.getenv('BUSINESS_ID_AIPP', 'ec5d7aab-8d74-4ef2-9d92-01b143c68c82')
        }

        # Plaud detection
        self.plaud_senders = ['no-reply@plaud.ai', 'noreply@plaud.ai']

        # Processed emails tracking (prevents duplicates)
        self.processed_emails = self._load_processed_emails()

        # Action tiers
        self.TIER_1_AUTO = 'auto'      # Low-risk: auto-execute
        self.TIER_2_APPROVE = 'approve'  # Higher-risk: email approval first

        # Tier classification rules
        self.auto_action_types = [
            'create_task',
            'set_callback',
            'set_reminder',
            'categorise_task',
            'snooze_task',
            'update_task_notes',
        ]
        self.approval_action_types = [
            'update_crm',           # Writing to PipeReply — hard to undo
            'send_email',           # Sending to a customer
            'create_calendar_event', # Booking time with client
            'change_deal_status',   # Won/lost — significant
            'delete_task',          # Destructive
        ]

    # =========================================================================
    # MAIN PROCESSING LOOP
    # =========================================================================

    @staticmethod
    def _normalize_subject(subject):
        """Strip Re:/Fwd:/FW: prefixes and whitespace to get the core subject"""
        if not subject:
            return ''
        # Remove common prefixes repeatedly
        cleaned = subject.strip()
        while True:
            new = re.sub(r'^(re|fwd|fw)\s*:\s*', '', cleaned, flags=re.IGNORECASE).strip()
            if new == cleaned:
                break
            cleaned = new
        return cleaned.lower()

    def process_forwarded_emails(self):
        """Check for new forwarded emails and analyze them"""
        print("AI Email Processor v2 Starting...")
        print(f"Checking {self.email_user} on {self.imap_server} for emails...")

        # Reload processed emails from DB each cycle (catches entries from other services)
        self.processed_emails = self._load_processed_emails()

        try:
            mail = imaplib.IMAP4_SSL(self.imap_server)
            mail.login(self.email_user, self.email_password)
            mail.select('inbox')

            # Search for ALL emails in last 7 days (not just UNSEEN)
            # This is more robust — works even if forwarding rules mark emails as read
            seven_days_ago = (date.today() - timedelta(days=7)).strftime("%d-%b-%Y")
            status, messages = mail.uid("search", None, f'(SINCE {seven_days_ago})')

            if not messages[0]:
                print("No emails in last 7 days")
                mail.close()
                mail.logout()
                return

            email_ids = messages[0].split()

            # Build list of genuinely unprocessed UIDs
            unprocessed = []
            for eid in email_ids:
                uid_str = eid.decode() if isinstance(eid, bytes) else str(eid)
                if uid_str not in self.processed_emails:
                    unprocessed.append(eid)

            if not unprocessed:
                print("No new emails to process")
                mail.close()
                mail.logout()
                return

            print(f"Found {len(email_ids)} total ({len(unprocessed)} new)")

            processed_count = 0
            skipped_dupes = 0
            seen_subjects = set()  # Subject-level dedup within this cycle

            # Process only genuinely unprocessed emails, newest first, max 20
            for msg_id in reversed(unprocessed[-20:]):
                msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)

                # Fetch email
                status, msg_data = mail.uid("fetch", msg_id, '(RFC822)')
                if status != 'OK':
                    self._mark_email_processed(f'fetch-fail-{msg_id_str}', msg_id_str)
                    continue

                email_body = email.message_from_bytes(msg_data[0][1])
                message_id = email_body.get('Message-ID', msg_id_str)

                # Skip if already processed (check both UID and Message-ID)
                if msg_id_str in self.processed_emails or message_id in self.processed_emails:
                    continue

                # Subject-level dedup: skip duplicate forwards of the same email
                raw_subject = email_body.get('Subject', '')
                if raw_subject:
                    decoded_parts = decode_header(raw_subject)
                    raw_subject = ''.join(
                        part.decode(enc or 'utf-8') if isinstance(part, bytes) else part
                        for part, enc in decoded_parts
                    )
                norm_subject = self._normalize_subject(raw_subject)
                if norm_subject and norm_subject in seen_subjects:
                    print(f"  ⏭️ Skipping duplicate subject: {raw_subject[:60]}")
                    self._mark_email_processed(message_id, msg_id_str)
                    skipped_dupes += 1
                    continue
                if norm_subject:
                    seen_subjects.add(norm_subject)

                # Process this email
                self.process_single_email_body(email_body)
                processed_count += 1

                # Mark as processed in Supabase
                self._mark_email_processed(message_id, msg_id_str)

                # Also mark as read on the server
                mail.uid('store', msg_id, '+FLAGS', '\\Seen')

            print(f"Processed {processed_count} emails ({skipped_dupes} duplicates skipped)")

            mail.close()
            mail.logout()

        except Exception as e:
            print(f"Error processing emails: {e}")
            import traceback
            traceback.print_exc()

    def _load_processed_emails(self):
        """Load set of already-processed email IDs and UIDs from Supabase"""
        try:
            result = self.tm.supabase.table('processed_emails') \
                .select('email_id,uid') \
                .execute()
            ids = set()
            for row in (result.data or []):
                if row.get('email_id'):
                    ids.add(row['email_id'])
                if row.get('uid'):
                    ids.add(row['uid'])
            print(f"📊 Loaded {len(ids)} processed email IDs")
            return ids
        except Exception as e:
            print(f"Warning: Could not load processed emails: {e}")
            return set()

    def _mark_email_processed(self, message_id, uid_str=''):
        """Mark an email as processed in Supabase"""
        try:
            self.tm.supabase.table('processed_emails').insert({
                'email_id': message_id,
                'uid': uid_str,
                'processed_at': datetime.now().isoformat(),
            }).execute()
            self.processed_emails.add(message_id)
            if uid_str:
                self.processed_emails.add(uid_str)
        except Exception as e:
            err_msg = str(e).lower()
            if 'duplicate' in err_msg or '409' in err_msg or '23505' in err_msg:
                # Already exists — that's fine, add to in-memory set
                self.processed_emails.add(message_id)
                if uid_str:
                    self.processed_emails.add(uid_str)
            else:
                print(f"❌ Failed to mark email processed: {e}")

    def process_single_email_body(self, email_body):
        """Process one email from an already-fetched email body"""
        try:

            subject = decode_header(email_body['Subject'])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode()

            sender = email_body['From']
            content = self.extract_email_content(email_body)

            # Detect email type
            is_plaud = self.is_plaud_transcription(sender)
            email_type = 'plaud_transcription' if is_plaud else 'forwarded_email'

            print(f"{'[PLAUD]' if is_plaud else '[EMAIL]'} Analyzing: {subject}")

            # Rob forwarding his own leads/emails — use the DSW forward handler
            # which strips his signature and validates the customer name.
            if not is_plaud and self._is_from_rob(sender):
                self.handle_dsw_forward(subject, sender, content)
                return

            # Parse with appropriate prompt
            analysis = self.analyze_with_claude(subject, sender, content, email_type)

            if not analysis or not analysis.get('actions'):
                print(f"  No actionable items found")
                return

            # Process each action based on tier
            auto_actions = []
            approval_actions = []

            for action in analysis['actions']:
                tier = self.classify_action_tier(action)
                action['tier'] = tier

                if tier == self.TIER_1_AUTO:
                    auto_actions.append(action)
                else:
                    approval_actions.append(action)

            # Execute Tier 1 actions immediately
            if auto_actions:
                print(f"  Auto-executing {len(auto_actions)} low-risk action(s)...")
                for action in auto_actions:
                    self.execute_action(action)

            # Queue Tier 2 actions for email approval
            if approval_actions:
                print(f"  Queuing {len(approval_actions)} action(s) for approval...")
                self.send_approval_email(
                    email_subject=subject,
                    email_sender=sender,
                    actions=approval_actions,
                    context=analysis.get('summary', '')
                )

        except Exception as e:
            print(f"Error processing email: {e}")

    # =========================================================================
    # PLAUD DETECTION
    # =========================================================================

    def is_plaud_transcription(self, sender):
        """Detect if email is a Plaud voice transcription"""
        sender_lower = sender.lower()
        return any(plaud in sender_lower for plaud in self.plaud_senders)

    # =========================================================================
    # CLAUDE AI ANALYSIS
    # =========================================================================

    def analyze_with_claude(self, subject, sender, content, email_type):
        """Use Claude to analyze email with solar-sales-aware prompt"""

        if email_type == 'plaud_transcription':
            prompt = self._build_plaud_prompt(subject, content)
        else:
            prompt = self._build_email_prompt(subject, sender, content)

        try:
            response = self.claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse JSON response
            raw = response.content[0].text
            # Handle markdown code blocks
            if '```json' in raw:
                raw = raw.split('```json')[1].split('```')[0]
            elif '```' in raw:
                raw = raw.split('```')[1].split('```')[0]

            return json.loads(raw.strip())

        except json.JSONDecodeError:
            print(f"  Could not parse AI response")
            return None
        except Exception as e:
            print(f"  Claude API error: {e}")
            return None

    def _build_plaud_prompt(self, subject, content):
        """Build Claude prompt for Plaud voice transcription parsing"""
        return f"""You are Rob Lowe's AI task assistant for his solar battery sales business.

Rob just recorded a voice memo after a call/site visit using his Plaud device.
The transcription below may contain MULTIPLE action items. Extract ALL of them.

TRANSCRIPTION:
{content}

ROB'S BUSINESS CONTEXT:
- Rob is a solar & battery sales engineer at Direct Solar Wholesalers (DSW), QLD Australia
- He sells residential solar panel + battery systems (GoodWe, SolaX brands)
- His workflow: Lead → Scoping Call → Quote (OpenSolar) → Price (DSW Tool) → Send Proposal → Follow Up → Close
- CRM: PipeReply (company CRM at app.pipereply.com) — used for contact details and notes
- Personal CRM: Jottask (his own task manager at jottask.app)
- Quoting: OpenSolar (app.opensolar.com) + DSW Quoting Tool (dswenergygroup.com.au)

COMMON ACTION PATTERNS IN ROB'S VOICE MEMOS:
- "Call back [name]" → create_task (callback reminder)
- "Update CRM for [name]" or "add notes to [name]'s CRM" → update_crm
- "Send quote to [name]" → send_email (needs approval)
- "[Name] is going with option [X]" → change_deal_status + update_crm
- "Book site visit for [name] on [day]" → create_calendar_event
- "Follow up with [name] in [X] days" → create_task with due date
- "Need to check [something]" → create_task (research)
- "[Name] wants [change to quote]" → create_task (quote revision)

EXTRACT actions as JSON:
{{
    "summary": "One-line summary of the voice memo",
    "customer_name": "Customer FULL NAME (first + last) if mentioned, null if not",
    "actions": [
        {{
            "action_type": "create_task|update_crm|send_email|create_calendar_event|change_deal_status|set_callback",
            "title": "[Customer FULL NAME]- [concise status or action needed]",
            "description": "What needs to be done — include specifics from the memo plus any useful context (e.g. referral source)",
            "customer_name": "Customer FULL NAME (first + last)",
            "email_address": "Customer email if visible anywhere in the content, null if not",
            "business": "Cloud Clean Energy",
            "priority": "low|medium|high|urgent",
            "due_date": "YYYY-MM-DD or null",
            "due_time": "HH:MM or null",
            "category": "Remember to Callback|Quote Follow Up|CRM Update|Site Visit|Research|General",
            "crm_notes": "If action_type is update_crm, the exact text to add as a CRM note. Keep Rob's voice — punchy, not formal.",
            "calendar_details": "If action_type is create_calendar_event: location, duration, attendees"
        }}
    ]
}}

Rules:
- CRITICAL: Task titles MUST use format "[Full Name]- [concise status/action]". Examples: "Graham Kildey- awaiting site photos and electricity bills", "Paul Thompson- follow up on battery quote", "Paul Van Zijl- call 8am re solar battery referral". NO space before the dash. Never use generic prefixes like "CRM Update:" or vague titles like "Follow up with Paul"
- If only a first name is in the voice memo, use the full name from the email subject line or content
- Extract EVERY action item, even if Rob mentions it casually
- If an email address is visible anywhere in the email content, headers, or signature, capture it in the email_address field
- If Rob says "call back" or "follow up", create a callback task with the right date
- If Rob mentions updating CRM or adding notes, set action_type to "update_crm" and write the crm_notes in Rob's voice (short, punchy, no corporate speak)
- "Going with option X" = deal won, needs both change_deal_status and update_crm
- Default business is "Cloud Clean Energy" unless Rob mentions AI Project Pro
- For callbacks without a specific date, default to next business day
- For follow-ups, "in X days" means X calendar days from today ({datetime.now().strftime('%Y-%m-%d')})
"""

    def _build_email_prompt(self, subject, sender, content):
        """Build Claude prompt for regular forwarded emails"""
        return f"""You are Rob Lowe's AI task assistant for his solar battery sales business.

Analyze this forwarded email and extract any action items.

EMAIL DETAILS:
From: {sender}
Subject: {subject}
Content: {content}

ROB'S BUSINESS CONTEXT:
- Rob is a solar & battery sales engineer at Direct Solar Wholesalers (DSW), QLD Australia
- He sells residential solar panel + battery systems (GoodWe, SolaX brands)
- His workflow: Lead → Scoping Call → Quote (OpenSolar) → Price (DSW Tool) → Send Proposal → Follow Up → Close
- CRM: PipeReply (company CRM) for contact details and lead notes
- Personal CRM: Jottask for task tracking and follow-ups

BUSINESSES:
- Cloud Clean Energy (solar energy sales — main business)
- AI Project Pro (AI consulting side business)

COMMON EMAIL TYPES ROB RECEIVES:
- New lead notifications from DSW ("Hi Rob, [name] has been assigned to you")
- Customer replies to quotes (questions, acceptance, rejection)
- Supplier/installer comms (scheduling, parts, delivery)
- Internal DSW emails (team updates, policy changes)
- SolarQuotes lead assignments
- Customer follow-up enquiries

EXTRACT actions as JSON:
{{
    "summary": "One-line summary of what this email is about",
    "customer_name": "Customer FULL NAME (first + last) if this relates to a customer, null if not",
    "actions": [
        {{
            "action_type": "create_task|update_crm|send_email|create_calendar_event|change_deal_status|set_callback",
            "title": "[Customer FULL NAME]- [concise status or action needed]",
            "description": "What needs to be done — include useful context like referral source, what they're waiting on, etc",
            "customer_name": "Customer FULL NAME (first + last)",
            "email_address": "Customer email if visible anywhere in the email headers, body, or signature, null if not",
            "business": "Cloud Clean Energy or AI Project Pro",
            "priority": "low|medium|high|urgent",
            "due_date": "YYYY-MM-DD or null",
            "due_time": "HH:MM or null",
            "category": "Remember to Callback|Quote Follow Up|CRM Update|New Lead|Research|General",
            "crm_notes": "If update_crm: the note text to add. null otherwise.",
            "calendar_details": "If create_calendar_event: details. null otherwise."
        }}
    ]
}}

Rules:
- CRITICAL: Task titles MUST use format "[Full Name]- [concise status/action]". Examples: "Graham Kildey- awaiting site photos and electricity bills", "Paul Thompson- follow up on battery quote", "Todd McHenry- site visit 8am Black Milk". NO space before the dash. Never use generic prefixes like "CRM Update:" or "New Lead:" — put the customer name first, then what's happening
- If only a first name appears in the subject, look in the email body/content for the full name
- Always scrape and capture email addresses — check the From header, email body, signatures, and any contact info in the content
- New lead assignment emails → create_task with category "New Lead", priority "high", due today
- Customer replies about quotes → create_task with category "Quote Follow Up"
- If customer says yes/accepts → change_deal_status + create_task for next steps
- If customer asks questions → create_task to respond, priority medium
- Internal/admin emails → lower priority unless time-sensitive
- Default business is "Cloud Clean Energy" unless email content clearly relates to AI Project Pro
- Today's date: {datetime.now().strftime('%Y-%m-%d')}
- If no actions needed, return {{"summary": "...", "customer_name": null, "actions": []}}
"""

    # =========================================================================
    # ACTION TIER CLASSIFICATION
    # =========================================================================

    def classify_action_tier(self, action):
        """Classify action as auto-execute (Tier 1) or needs-approval (Tier 2)"""
        action_type = action.get('action_type', '')

        if action_type in self.approval_action_types:
            return self.TIER_2_APPROVE

        # Default: auto-execute for known safe types, approve for unknown
        if action_type in ['create_task', 'set_callback', 'set_reminder']:
            return self.TIER_1_AUTO

        return self.TIER_2_APPROVE

    # =========================================================================
    # ACTION EXECUTION (TIER 1 — AUTO)
    # =========================================================================

    def execute_action(self, action):
        """Execute a Tier 1 (auto) action"""
        action_type = action.get('action_type', '')

        try:
            if action_type in ['create_task', 'set_callback', 'set_reminder']:
                self._create_task(action)
            else:
                print(f"  Unknown auto action type: {action_type}")

        except Exception as e:
            print(f"  Error executing action '{action.get('title', '')}': {e}")

    def _create_task(self, action):
        """Create a task in Supabase"""
        business_id = self.businesses.get(
            action.get('business', 'Cloud Clean Energy')
        )

        # Map category to Jottask categories
        category = action.get('category', 'General')

        task_data = {
            'business_id': business_id,
            'title': action['title'],
            'description': action.get('description', ''),
            'due_date': action.get('due_date'),
            'due_time': action.get('due_time'),
            'priority': action.get('priority', 'medium'),
            'is_meeting': action.get('action_type') == 'create_calendar_event',
            'status': 'pending',
        }

        result = self.tm.supabase.table('tasks').insert(task_data).execute()

        if result.data:
            task = result.data[0]
            print(f"  [AUTO] Task created: {task['title']}")

            # Send confirmation email
            try:
                self.tm.send_task_confirmation_email(task['id'])
            except Exception:
                pass  # Don't fail if confirmation email fails
        else:
            print(f"  Failed to create task: {action['title']}")

    # =========================================================================
    # APPROVAL FLOW (TIER 2 — EMAIL APPROVAL)
    # =========================================================================

    def send_approval_email(self, email_subject, email_sender, actions, context):
        """Send approval email with action buttons for Tier 2 actions"""

        # Generate approval tokens for each action
        action_items_html = ""
        for i, action in enumerate(actions):
            token = self._generate_action_token(action)

            # Store pending action in Supabase
            self._store_pending_action(token, action)

            # Build approval URL
            base_url = os.getenv('APP_URL', 'https://www.jottask.app')
            approve_url = f"{base_url}/action/approve?token={token}"
            edit_url = f"{base_url}/action/edit?token={token}"
            reject_url = f"{base_url}/action/reject?token={token}"

            # Action description based on type
            action_desc = self._format_action_description(action)

            action_items_html += f"""
            <div style="border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 12px 0; background: #fafafa;">
                <div style="font-size: 14px; color: #666; margin-bottom: 4px;">
                    {action.get('action_type', '').replace('_', ' ').upper()}
                    {' — ' + action.get('customer_name', '') if action.get('customer_name') else ''}
                </div>
                <div style="font-size: 16px; font-weight: bold; margin-bottom: 8px;">
                    {action['title']}
                </div>
                <div style="font-size: 14px; color: #444; margin-bottom: 12px;">
                    {action_desc}
                </div>
                <div>
                    <a href="{approve_url}" style="display: inline-block; padding: 8px 20px; background: #22c55e; color: white; text-decoration: none; border-radius: 6px; margin-right: 8px; font-weight: bold;">Approve</a>
                    <a href="{edit_url}" style="display: inline-block; padding: 8px 20px; background: #3b82f6; color: white; text-decoration: none; border-radius: 6px; margin-right: 8px; font-weight: bold;">Edit</a>
                    <a href="{reject_url}" style="display: inline-block; padding: 8px 20px; background: #ef4444; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">Skip</a>
                </div>
            </div>
            """

        # Build full email HTML
        email_html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #1e3a5f; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
                <h2 style="margin: 0; font-size: 18px;">Jottask — Actions Need Your Approval</h2>
            </div>

            <div style="padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 8px 8px;">
                <div style="font-size: 14px; color: #666; margin-bottom: 16px;">
                    <strong>From:</strong> {email_sender}<br>
                    <strong>Subject:</strong> {email_subject}<br>
                    <strong>Summary:</strong> {context}
                </div>

                <h3 style="font-size: 16px; color: #333; margin-bottom: 8px;">
                    {len(actions)} action(s) need your approval:
                </h3>

                {action_items_html}

                <div style="font-size: 12px; color: #999; margin-top: 20px; text-align: center;">
                    Rob's AI Task Manager &bull; Cloud Clean Energy
                </div>
            </div>
        </div>
        """

        # Send via Resend
        try:
            params = {
                "from": self.from_email,
                "to": [os.getenv('ROB_EMAIL', 'rob@cloudcleanenergy.com.au')],
                "subject": f"Jottask Approval: {' '.join(email_subject.split())}",
                "html": email_html
            }
            response = resend.Emails.send(params)
            print(f"  Resend response: {response}")
            print(f"  Approval email sent for {len(actions)} action(s)")
        except Exception as e:
            print(f"  Error sending approval email: {e}")

    def _format_action_description(self, action):
        """Format a human-readable description for the approval email"""
        action_type = action.get('action_type', '')

        if action_type == 'update_crm':
            crm_notes = action.get('crm_notes', 'No notes specified')
            return f"Add to CRM notes: <em>\"{crm_notes}\"</em>"

        elif action_type == 'send_email':
            return f"Draft and send email to {action.get('customer_name', 'customer')}: {action.get('description', '')}"

        elif action_type == 'create_calendar_event':
            details = action.get('calendar_details', '')
            due = action.get('due_date', 'TBD')
            time = action.get('due_time', '')
            return f"Create calendar event on {due}{' at ' + time if time else ''}: {details}"

        elif action_type == 'change_deal_status':
            return f"Change deal status for {action.get('customer_name', 'customer')}: {action.get('description', '')}"

        elif action_type == 'delete_task':
            return f"Delete task: {action.get('description', '')}"

        return action.get('description', '')

    def _generate_action_token(self, action):
        """Generate a unique token for an action approval"""
        raw = f"{action.get('title', '')}-{datetime.now().isoformat()}-{uuid.uuid4()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _store_pending_action(self, token, action):
        """Store a pending action in Supabase for later approval/execution"""
        try:
            self.tm.supabase.table('pending_actions').insert({
                'token': token,
                'action_type': action.get('action_type'),
                'action_data': json.dumps(action),
                'status': 'pending',
                'created_at': datetime.now().isoformat(),
                'expires_at': (datetime.now() + timedelta(days=7)).isoformat(),
            }).execute()
        except Exception as e:
            print(f"  Error storing pending action: {e}")

    # =========================================================================
    # APPROVAL EXECUTION (called when Rob clicks Approve)
    # =========================================================================

    def execute_approved_action(self, token):
        """Execute an action that Rob has approved via email button"""
        try:
            # Fetch pending action
            result = self.tm.supabase.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()

            if not result.data:
                return {'success': False, 'message': 'Action not found or already processed'}

            pending = result.data[0]
            action = json.loads(pending['action_data'])
            action_type = action.get('action_type', '')

            # Execute based on type
            success = False
            message = ''

            if action_type == 'update_crm':
                success, message = self._execute_crm_update(action)

            elif action_type == 'send_email':
                success, message = self._execute_send_email(action)

            elif action_type == 'create_calendar_event':
                success, message = self._execute_calendar_event(action)

            elif action_type == 'change_deal_status':
                success, message = self._execute_deal_status_change(action)

            else:
                # Fallback: create as a task
                self._create_task(action)
                success = True
                message = f"Created task: {action.get('title', '')}"

            # Mark as processed
            self.tm.supabase.table('pending_actions').update({
                'status': 'approved' if success else 'failed',
                'processed_at': datetime.now().isoformat(),
            }).eq('token', token).execute()

            return {'success': success, 'message': message}

        except Exception as e:
            return {'success': False, 'message': str(e)}

    def reject_action(self, token):
        """Mark an action as rejected (Rob clicked Skip)"""
        try:
            self.tm.supabase.table('pending_actions').update({
                'status': 'rejected',
                'processed_at': datetime.now().isoformat(),
            }).eq('token', token).execute()
            return {'success': True, 'message': 'Action skipped'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # =========================================================================
    # TIER 2 ACTION EXECUTORS
    # =========================================================================

    def _execute_crm_update(self, action):
        """Update PipeReply CRM with notes"""
        # TODO: Implement PipeReply API integration
        # For now, create a task to remind Rob to update CRM manually
        customer = action.get('customer_name', 'Unknown')
        notes = action.get('crm_notes', '')

        self._create_task({
            'title': f"CRM Update: {customer}",
            'description': f"Add to CRM notes:\n{notes}",
            'business': 'Cloud Clean Energy',
            'priority': 'high',
            'due_date': datetime.now().strftime('%Y-%m-%d'),
            'category': 'CRM Update',
        })

        return True, f"CRM update task created for {customer}"

    def _execute_send_email(self, action):
        """Draft an email (creates task — actual sending done by Rob via Apple Mail)"""
        customer = action.get('customer_name', 'Unknown')

        self._create_task({
            'title': f"Send email to {customer}",
            'description': action.get('description', ''),
            'business': 'Cloud Clean Energy',
            'priority': 'high',
            'due_date': datetime.now().strftime('%Y-%m-%d'),
            'category': 'Quote Follow Up',
        })

        return True, f"Email task created for {customer}"

    def _execute_calendar_event(self, action):
        """Create a calendar event"""
        # TODO: Implement Google Calendar API integration
        # For now, create a task with the calendar details
        customer = action.get('customer_name', 'Unknown')
        details = action.get('calendar_details', '')

        self._create_task({
            'title': f"Calendar: {action.get('title', '')}",
            'description': f"Customer: {customer}\nDetails: {details}",
            'business': 'Cloud Clean Energy',
            'priority': 'high',
            'due_date': action.get('due_date'),
            'due_time': action.get('due_time'),
            'category': 'Site Visit',
            'action_type': 'create_calendar_event',
        })

        return True, f"Calendar event task created for {customer}"

    def _execute_deal_status_change(self, action):
        """Change deal status"""
        customer = action.get('customer_name', 'Unknown')

        self._create_task({
            'title': f"Deal Update: {customer}",
            'description': action.get('description', ''),
            'business': 'Cloud Clean Energy',
            'priority': 'urgent',
            'due_date': datetime.now().strftime('%Y-%m-%d'),
            'category': 'General',
        })

        return True, f"Deal status task created for {customer}"

    # =========================================================================
    # DSW FORWARD HANDLING (Rob-forwarded / self-generated leads)
    # =========================================================================

    # Patterns that mark the start of Rob's email signature
    _ROB_SIG_PATTERNS = [
        r'^Best Regards\b',
        r'^Rob Lowe\b',
        r'^M:\s',
        r'^E:\s',
        r'^W:\s',
        r'^(QLD|SA|VIC|NSW)\s*:',
        r'^--\s*$',
    ]

    def _is_from_rob(self, sender):
        """Return True if the sender is Rob himself"""
        rob_email = os.getenv('ROB_EMAIL', 'rob@cloudcleanenergy.com.au').lower()
        rob_emails = {rob_email}
        alt = os.getenv('ROB_ALT_EMAIL', '')
        if alt:
            rob_emails.add(alt.strip().lower())
        sender_lower = sender.lower()
        return any(r in sender_lower for r in rob_emails)

    def strip_rob_signature(self, content):
        """
        Strip Rob's email signature from content before AI analysis.
        Cuts at the first line matching any of _ROB_SIG_PATTERNS.
        """
        lines = content.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            for pattern in self._ROB_SIG_PATTERNS:
                if re.match(pattern, stripped, re.IGNORECASE):
                    return '\n'.join(lines[:i]).strip()
        return content.strip()

    def _is_rob_name(self, name):
        """Return True if the extracted name is Rob's own name, not a customer"""
        if not name:
            return False
        return name.strip().lower() in ('rob lowe', 'rob')

    def _extract_name_from_subject(self, subject):
        """
        Try to extract a customer name from the email subject line.
        Looks for a 'First Last' pattern after stripping Fwd:/Re: prefixes.
        """
        cleaned = re.sub(r'^(fwd?|re)\s*:\s*', '', subject, flags=re.IGNORECASE).strip()
        match = re.match(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', cleaned)
        if match:
            return match.group(1)
        return None

    def handle_dsw_forward(self, subject, sender, content):
        """
        Process an email that Rob forwarded or self-generated as a lead.

        Key differences from the standard flow:
        1. Rob's signature is stripped BEFORE passing content to Claude.
        2. If Claude returns Rob's own name as the customer, fall back to
           extracting a name from the subject line.
        3. The approval/confirmation email subject uses
           '{customer_name} — {first requirement}' instead of the raw
           forwarded subject.
        """
        # 1. Strip Rob's signature
        stripped = self.strip_rob_signature(content)
        removed = len(content) - len(stripped)
        if removed:
            print(f"  [DSW Fwd] Stripped {removed} chars of Rob's signature")

        # 2. Analyze with Claude
        analysis = self.analyze_with_claude(subject, sender, stripped, 'forwarded_email')

        if not analysis or not analysis.get('actions'):
            print("  [DSW Fwd] No actionable items found")
            return

        # 3. Validate customer name — reject if it's Rob
        customer_name = analysis.get('customer_name')
        if self._is_rob_name(customer_name):
            fallback = self._extract_name_from_subject(subject)
            print(f"  [DSW Fwd] Rejected extracted name '{customer_name}' — "
                  f"falling back to subject name: '{fallback}'")
            customer_name = fallback
            analysis['customer_name'] = customer_name
            # Fix name in each action too
            for action in analysis['actions']:
                if self._is_rob_name(action.get('customer_name')):
                    action['customer_name'] = customer_name
                    # Rebuild title if it starts with Rob's name
                    title = action.get('title', '')
                    if re.match(r'^Rob\b', title, re.IGNORECASE):
                        action_part = title.split('-', 1)[-1].strip() if '-' in title else title
                        action['title'] = f"{customer_name or subject[:40]}- {action_part}"

        # 4. Build a meaningful subject for the generated email
        first_action = analysis['actions'][0]
        first_req = (first_action.get('description') or first_action.get('title') or '').strip()
        # Truncate requirement to keep subject concise
        if len(first_req) > 60:
            first_req = first_req[:57].rstrip() + '...'
        if customer_name:
            generated_subject = f"{customer_name} \u2014 {first_req}" if first_req else customer_name
        else:
            # Fallback: use de-prefixed original subject
            generated_subject = re.sub(r'^(fwd?|re)\s*:\s*', '', subject, flags=re.IGNORECASE).strip()

        print(f"  [DSW Fwd] Customer: {customer_name!r}  |  Email subject: {generated_subject!r}")

        # 5. Standard tier routing
        auto_actions = []
        approval_actions = []
        for action in analysis['actions']:
            tier = self.classify_action_tier(action)
            action['tier'] = tier
            if tier == self.TIER_1_AUTO:
                auto_actions.append(action)
            else:
                approval_actions.append(action)

        if auto_actions:
            print(f"  Auto-executing {len(auto_actions)} action(s)...")
            for action in auto_actions:
                self.execute_action(action)

        if approval_actions:
            print(f"  Queuing {len(approval_actions)} action(s) for approval...")
            self.send_approval_email(
                email_subject=generated_subject,
                email_sender=sender,
                actions=approval_actions,
                context=analysis.get('summary', '')
            )

    # =========================================================================
    # EMAIL CONTENT EXTRACTION
    # =========================================================================

    def extract_email_content(self, email_body):
        """Extract text content from email"""
        content = ""

        if email_body.is_multipart():
            for part in email_body.walk():
                if part.get_content_type() == "text/plain":
                    content += part.get_payload(decode=True).decode('utf-8', errors='ignore')
        else:
            content = email_body.get_payload(decode=True).decode('utf-8', errors='ignore')

        # Increased from 2000 to 5000 for Plaud transcriptions
        return content[:5000]


# =============================================================================
# MIGRATION: pending_actions table
# =============================================================================
PENDING_ACTIONS_MIGRATION = """
-- Migration: Add pending_actions table for Tier 2 approval flow
-- Run this in your Supabase SQL editor

CREATE TABLE IF NOT EXISTS pending_actions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    action_type TEXT NOT NULL,
    action_data JSONB NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'failed', 'expired')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    user_id UUID REFERENCES auth.users(id)
);

-- Index for fast token lookups
CREATE INDEX idx_pending_actions_token ON pending_actions(token);
CREATE INDEX idx_pending_actions_status ON pending_actions(status);

-- RLS policy
ALTER TABLE pending_actions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own pending actions"
    ON pending_actions FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "System can insert pending actions"
    ON pending_actions FOR INSERT
    WITH CHECK (true);

CREATE POLICY "System can update pending actions"
    ON pending_actions FOR UPDATE
    USING (true);
"""


# =============================================================================
# FLASK ROUTES (add to app.py)
# =============================================================================
FLASK_ROUTES = """
# Add these routes to app.py for handling approval clicks

@app.route('/action/approve')
def approve_action():
    token = request.args.get('token')
    if not token:
        return 'Invalid request', 400

    processor = AIEmailProcessor()
    result = processor.execute_approved_action(token)

    if result['success']:
        return render_template('action_result.html',
            status='approved',
            message=result['message'])
    else:
        return render_template('action_result.html',
            status='error',
            message=result['message'])

@app.route('/action/reject')
def reject_action():
    token = request.args.get('token')
    if not token:
        return 'Invalid request', 400

    processor = AIEmailProcessor()
    result = processor.reject_action(token)

    return render_template('action_result.html',
        status='skipped',
        message='Action skipped')

@app.route('/action/edit')
def edit_action():
    token = request.args.get('token')
    if not token:
        return 'Invalid request', 400

    # Load the pending action for editing
    processor = AIEmailProcessor()
    result = processor.tm.supabase.table('pending_actions').select('*').eq('token', token).execute()

    if result.data:
        action = json.loads(result.data[0]['action_data'])
        return render_template('edit_action.html', action=action, token=token)

    return 'Action not found', 404
"""


if __name__ == "__main__":
    import time
    processor = AIEmailProcessor()
    poll_interval = int(os.getenv('POLL_INTERVAL_SECONDS', '900'))  # Default 15 minutes
    print(f"Starting email polling loop (every {poll_interval}s)...")
    while True:
        try:
            processor.process_forwarded_emails()
        except Exception as e:
            print(f"Error in polling cycle: {e}")
        print(f"Sleeping {poll_interval}s until next check...")
        time.sleep(poll_interval)
