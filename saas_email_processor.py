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
from datetime import datetime, timedelta
from task_manager import TaskManager
from anthropic import Anthropic
from dotenv import load_dotenv
import os
import uuid
import hashlib
import resend

load_dotenv()


class AIEmailProcessor:
    def __init__(self):
        self.tm = TaskManager()
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

    def process_forwarded_emails(self):
        """Check for new forwarded emails and analyze them"""
        print("AI Email Processor v2 Starting...")
        print(f"Checking {self.email_user} on {self.imap_server} for emails...")

        try:
            mail = imaplib.IMAP4_SSL(self.imap_server)
            mail.login(self.email_user, self.email_password)
            mail.select('inbox')

            # Search for unread emails
            status, messages = mail.search(None, 'UNSEEN')

            if not messages[0]:
                print("No new emails to process")
                return

            email_count = len(messages[0].split())
            print(f"Found {email_count} new emails to analyze")

            for msg_id in messages[0].split():
                self.process_single_email(mail, msg_id)
                # Explicitly mark email as read to prevent duplicate processing
                mail.store(msg_id, '+FLAGS', '\\Seen')

            mail.close()
            mail.logout()

        except Exception as e:
            print(f"Error processing emails: {e}")

    def process_single_email(self, mail, msg_id):
        """Process one email — detect type, parse, classify actions, execute/queue"""
        try:
            # Get email content
            status, msg_data = mail.fetch(msg_id, '(RFC822)')
            email_body = email.message_from_bytes(msg_data[0][1])

            subject = decode_header(email_body['Subject'])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode()

            sender = email_body['From']
            content = self.extract_email_content(email_body)

            # Detect email type
            is_plaud = self.is_plaud_transcription(sender)
            email_type = 'plaud_transcription' if is_plaud else 'forwarded_email'

            print(f"{'[PLAUD]' if is_plaud else '[EMAIL]'} Analyzing: {subject}")

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
    "customer_name": "Customer name if mentioned, null if not",
    "actions": [
        {{
            "action_type": "create_task|update_crm|send_email|create_calendar_event|change_deal_status|set_callback",
            "title": "Clear actionable title",
            "description": "What needs to be done — include specifics from the memo",
            "customer_name": "Customer name this relates to",
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
- Extract EVERY action item, even if Rob mentions it casually
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
    "customer_name": "Customer name if this relates to a customer, null if not",
    "actions": [
        {{
            "action_type": "create_task|update_crm|send_email|create_calendar_event|change_deal_status|set_callback",
            "title": "Clear actionable title",
            "description": "What needs to be done",
            "customer_name": "Customer name if applicable",
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
            resend.Emails.send(params)
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
