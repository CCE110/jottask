#!/usr/bin/env python3
"""
AI Email Task Processor v3 — Multi-Tenant
Upgraded with:
- Solar sales pipeline awareness (DSW/Cloud Clean Energy workflow)
- Plaud voice transcription detection & multi-action parsing
- Tiered action system (auto-execute vs email approval)
- Email-based approval flow (replaces terminal input)
- Jottask category intelligence
- Multi-tenant: processes all active email_connections, per-user AI context
- Backward compatible: falls back to env-var single-inbox when no connections exist
"""

import imaplib
import email
from email.header import decode_header
import json
from dataclasses import dataclass, field
from typing import Optional, Dict
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

AEST = pytz.timezone('Australia/Brisbane')

def _now_local(user_context=None):
    """Get current time in the user's timezone (defaults to AEST)."""
    tz = AEST
    if user_context and hasattr(user_context, 'timezone') and user_context.timezone:
        try:
            tz = pytz.timezone(user_context.timezone)
        except:
            pass
    return datetime.now(tz)


# =========================================================================
# USER CONTEXT — per-tenant data passed through the processing pipeline
# =========================================================================

@dataclass
class UserContext:
    """Per-user context for multi-tenant email processing"""
    user_id: str
    email_address: str
    company_name: str = ''
    full_name: str = ''
    timezone: str = 'Australia/Brisbane'
    businesses: Dict[str, str] = field(default_factory=dict)
    ai_context: Optional[dict] = None
    connection_id: Optional[str] = None


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

        # Business IDs (env-var fallback for legacy single-tenant mode)
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
            'update_task_notes',  # AI-routed note to existing task
        ]
        self.approval_action_types = [
            'update_crm',           # Writing to PipeReply — hard to undo
            'send_email',           # Sending to a customer
            'create_calendar_event', # Booking time with client
            'change_deal_status',   # Won/lost — significant
            'delete_task',          # Destructive
        ]

    # =========================================================================
    # MULTI-TENANT MAIN LOOP
    # =========================================================================

    def _get_active_connections(self):
        """Query email_connections for active connections due for sync"""
        try:
            result = self.tm.supabase.table('email_connections') \
                .select('*, users(id, email, full_name, company_name, timezone, ai_context)') \
                .eq('is_active', True) \
                .execute()

            if not result.data:
                return []

            now = datetime.now(pytz.UTC)
            due_connections = []
            for conn in result.data:
                last_sync = conn.get('last_sync_at')
                freq_minutes = conn.get('sync_frequency_minutes', 15)

                if last_sync:
                    last_sync_dt = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
                    next_sync = last_sync_dt + timedelta(minutes=freq_minutes)
                    if now < next_sync:
                        continue  # Not due yet

                due_connections.append(conn)

            return due_connections

        except Exception as e:
            print(f"Error fetching active connections: {e}")
            return []

    def _build_user_context(self, connection):
        """Build a UserContext from an email_connections row (with joined user data)"""
        user_data = connection.get('users') or {}
        ai_context = user_data.get('ai_context') or {}

        # Build businesses dict from ai_context, fall back to env vars
        businesses = ai_context.get('businesses', {})
        if not businesses:
            businesses = self.businesses.copy()

        return UserContext(
            user_id=connection['user_id'],
            email_address=connection['email_address'],
            company_name=user_data.get('company_name', ''),
            full_name=user_data.get('full_name', ''),
            timezone=user_data.get('timezone', 'Australia/Brisbane'),
            businesses=businesses,
            ai_context=ai_context,
            connection_id=str(connection['id']),
        )

    def process_connection(self, connection):
        """Process emails for a single connection"""
        user_ctx = self._build_user_context(connection)
        use_env = connection.get('use_env_credentials', False)

        if use_env:
            imap_server = os.getenv('IMAP_SERVER', 'mail.privateemail.com')
            imap_user = os.getenv('JOTTASK_EMAIL', 'jottask@flowquote.ai')
            imap_password = os.getenv('JOTTASK_EMAIL_PASSWORD')
        else:
            imap_server = connection.get('imap_server', 'imap.gmail.com')
            imap_user = connection['email_address']
            imap_password = connection.get('imap_password', '')

        if not imap_password:
            print(f"  Skipping {user_ctx.email_address}: no IMAP password configured")
            return

        print(f"Processing connection: {user_ctx.email_address} (user: {user_ctx.full_name})")

        # Load processed emails scoped to this connection
        processed = self._load_processed_emails(connection_id=user_ctx.connection_id)

        try:
            mail = imaplib.IMAP4_SSL(imap_server)
            mail.login(imap_user, imap_password)
            mail.select('inbox')

            seven_days_ago = (date.today() - timedelta(days=7)).strftime("%d-%b-%Y")
            status, messages = mail.uid("search", None, f'(SINCE {seven_days_ago})')

            if not messages[0]:
                print(f"  No emails in last 7 days for {user_ctx.email_address}")
                mail.close()
                mail.logout()
                self._update_last_sync(user_ctx.connection_id)
                return

            email_ids = messages[0].split()

            unprocessed = []
            for eid in email_ids:
                uid_str = eid.decode() if isinstance(eid, bytes) else str(eid)
                if uid_str not in processed:
                    unprocessed.append(eid)

            if not unprocessed:
                print(f"  No new emails for {user_ctx.email_address}")
                mail.close()
                mail.logout()
                self._update_last_sync(user_ctx.connection_id)
                return

            print(f"  Found {len(email_ids)} total ({len(unprocessed)} new)")

            processed_count = 0
            skipped_dupes = 0
            seen_subjects = set()

            for msg_id in reversed(unprocessed[-20:]):
                msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)

                status, msg_data = mail.uid("fetch", msg_id, '(RFC822)')
                if status != 'OK':
                    self._mark_email_processed(
                        f'fetch-fail-{msg_id_str}', msg_id_str,
                        connection_id=user_ctx.connection_id, user_id=user_ctx.user_id
                    )
                    continue

                email_body = email.message_from_bytes(msg_data[0][1])
                message_id = email_body.get('Message-ID', msg_id_str)

                if msg_id_str in processed or message_id in processed:
                    continue

                raw_subject = email_body.get('Subject', '')
                if raw_subject:
                    decoded_parts = decode_header(raw_subject)
                    raw_subject = ''.join(
                        part.decode(enc or 'utf-8') if isinstance(part, bytes) else part
                        for part, enc in decoded_parts
                    )
                norm_subject = self._normalize_subject(raw_subject)
                if norm_subject and norm_subject in seen_subjects:
                    print(f"  Skipping duplicate subject: {raw_subject[:60]}")
                    self._mark_email_processed(
                        message_id, msg_id_str,
                        connection_id=user_ctx.connection_id, user_id=user_ctx.user_id
                    )
                    skipped_dupes += 1
                    continue
                if norm_subject:
                    seen_subjects.add(norm_subject)

                sender_raw = email_body.get('From', '')
                sender_addr = self._get_sender_email_address(sender_raw)

                self.process_single_email_body(email_body, user_context=user_ctx)
                processed_count += 1

                self._mark_email_processed(
                    message_id, msg_id_str,
                    connection_id=user_ctx.connection_id, user_id=user_ctx.user_id,
                    sender_email=sender_addr, sender_name=sender_raw,
                    subject=raw_subject,
                )

                mail.uid('store', msg_id, '+FLAGS', '\\Seen')

            print(f"  Processed {processed_count} emails ({skipped_dupes} duplicates skipped) for {user_ctx.email_address}")

            mail.close()
            mail.logout()

        except Exception as e:
            print(f"  Error processing connection {user_ctx.email_address}: {e}")
            import traceback
            traceback.print_exc()

        self._update_last_sync(user_ctx.connection_id)

    def _update_last_sync(self, connection_id):
        """Stamp last_sync_at on the connection after processing"""
        try:
            self.tm.supabase.table('email_connections').update({
                'last_sync_at': datetime.now(pytz.UTC).isoformat(),
            }).eq('id', connection_id).execute()
        except Exception as e:
            print(f"  Error updating last_sync_at: {e}")

    def process_all_connections(self):
        """Main entry point: process all active connections, fall back to legacy single-inbox"""
        connections = self._get_active_connections()

        if connections:
            print(f"Multi-tenant mode: {len(connections)} connection(s) due for sync")
            for conn in connections:
                try:
                    self.process_connection(conn)
                except Exception as e:
                    email_addr = conn.get('email_address', 'unknown')
                    print(f"Error processing connection {email_addr}: {e}")
                    import traceback
                    traceback.print_exc()
        else:
            # Fallback: no connections in DB, use legacy env-var single-inbox
            print("No active connections found, falling back to legacy single-inbox mode")
            self.process_forwarded_emails()

    # =========================================================================
    # LEGACY SINGLE-INBOX PROCESSING (preserved as fallback)
    # =========================================================================

    @staticmethod
    def _normalize_subject(subject):
        """Strip Re:/Fwd:/FW: prefixes and whitespace to get the core subject"""
        import re
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
        """Check for new forwarded emails and analyze them (legacy single-inbox mode)"""
        print("AI Email Processor v2 Starting (legacy mode)...")
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
                    print(f"  Skipping duplicate subject: {raw_subject[:60]}")
                    self._mark_email_processed(message_id, msg_id_str)
                    skipped_dupes += 1
                    continue
                if norm_subject:
                    seen_subjects.add(norm_subject)

                # Extract sender info for history tracking
                sender_raw = email_body.get('From', '')
                sender_addr = self._get_sender_email_address(sender_raw)

                # Process this email
                self.process_single_email_body(email_body)
                processed_count += 1

                # Mark as processed in Supabase with sender info
                self._mark_email_processed(
                    message_id, msg_id_str,
                    sender_email=sender_addr, sender_name=sender_raw,
                    subject=raw_subject,
                )

                # Also mark as read on the server
                mail.uid('store', msg_id, '+FLAGS', '\\Seen')

            print(f"Processed {processed_count} emails ({skipped_dupes} duplicates skipped)")

            mail.close()
            mail.logout()

        except Exception as e:
            print(f"Error processing emails: {e}")
            import traceback
            traceback.print_exc()

    # =========================================================================
    # CONNECTION-AWARE DEDUP
    # =========================================================================

    def _load_processed_emails(self, connection_id=None):
        """Load set of already-processed email IDs and UIDs from Supabase"""
        try:
            query = self.tm.supabase.table('processed_emails').select('email_id,uid')

            if connection_id:
                query = query.eq('connection_id', connection_id)

            result = query.execute()
            ids = set()
            for row in (result.data or []):
                if row.get('email_id'):
                    ids.add(row['email_id'])
                if row.get('uid'):
                    ids.add(row['uid'])
            print(f"Loaded {len(ids)} processed email IDs" + (f" for connection {connection_id[:8]}..." if connection_id else ""))
            return ids
        except Exception as e:
            print(f"Warning: Could not load processed emails: {e}")
            return set()

    def _mark_email_processed(self, message_id, uid_str='', connection_id=None,
                              user_id=None, sender_email=None, sender_name=None,
                              subject=None):
        """Mark an email as processed in Supabase, with optional sender info for history"""
        try:
            data = {
                'email_id': message_id,
                'uid': uid_str,
                'processed_at': datetime.now(pytz.UTC).isoformat(),
            }
            if connection_id:
                data['connection_id'] = connection_id
            if user_id:
                data['user_id'] = user_id
            if sender_email:
                data['sender_email'] = sender_email.lower()
            if sender_name:
                data['sender_name'] = sender_name
            if subject:
                data['subject'] = subject[:500]  # Cap length

            self.tm.supabase.table('processed_emails').insert(data).execute()
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
                print(f"Failed to mark email processed: {e}")

    # =========================================================================
    # EMAIL PROCESSING
    # =========================================================================

    def process_single_email_body(self, email_body, user_context=None):
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

            # Parse with appropriate prompt
            analysis = self.analyze_with_claude(subject, sender, content, email_type, user_context=user_context)

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
                    self.execute_action(action, user_context=user_context)

            # Queue Tier 2 actions for email approval
            if approval_actions:
                print(f"  Queuing {len(approval_actions)} action(s) for approval...")
                self.send_approval_email(
                    email_subject=subject,
                    email_sender=sender,
                    actions=approval_actions,
                    context=analysis.get('summary', ''),
                    user_context=user_context,
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
    # CLAUDE AI ANALYSIS — with per-user prompt context
    # =========================================================================

    def _build_user_prompt_context(self, user_context=None):
        """Build prompt variables from user context. Falls back to Rob's defaults."""
        if user_context and user_context.ai_context:
            ctx = user_context.ai_context
            return {
                'user_name': user_context.full_name or 'the user',
                'company_name': user_context.company_name or ctx.get('company_name', 'the company'),
                'role_description': ctx.get('role_description', 'a sales professional'),
                'crm_name': ctx.get('crm_name', 'their CRM'),
                'workflow': ctx.get('workflow', 'Lead → Follow Up → Close'),
                'businesses': user_context.businesses,
                'default_business': ctx.get('default_business', list(user_context.businesses.keys())[0] if user_context.businesses else 'Default'),
                'categories': ctx.get('categories', ['Remember to Callback', 'Quote Follow Up', 'CRM Update', 'New Lead', 'Research', 'General']),
                'extra_context': ctx.get('extra_context', ''),
            }

        # Rob's hardcoded defaults (zero behavior change for legacy mode)
        return {
            'user_name': 'Rob Lowe',
            'company_name': 'Direct Solar Wholesalers (DSW)',
            'role_description': 'a solar & battery sales engineer at Direct Solar Wholesalers (DSW), QLD Australia',
            'crm_name': 'PipeReply',
            'workflow': 'Lead → Scoping Call → Quote (OpenSolar) → Price (DSW Tool) → Send Proposal → Follow Up → Close',
            'businesses': self.businesses,
            'default_business': 'Cloud Clean Energy',
            'categories': ['Remember to Callback', 'Quote Follow Up', 'CRM Update', 'Site Visit', 'New Lead', 'Research', 'General'],
            'extra_context': '',
        }

    def _get_sender_email_address(self, sender_raw):
        """Extract clean email address from a From header like 'John Smith <john@example.com>'"""
        import re
        match = re.search(r'<([^>]+)>', sender_raw)
        if match:
            return match.group(1).lower()
        # Maybe it's just a bare email
        if '@' in sender_raw:
            return sender_raw.strip().lower()
        return ''

    def _get_existing_tasks_for_sender(self, sender_email, user_context=None):
        """Query open tasks that match this sender's email or name."""
        if not sender_email:
            return []
        try:
            query = self.tm.supabase.table('tasks')\
                .select('id, title, due_date, status, client_name, client_email, created_at')\
                .eq('status', 'pending')\
                .eq('client_email', sender_email)\
                .order('created_at', desc=True)\
                .limit(5)

            if user_context:
                query = query.eq('user_id', user_context.user_id)

            result = query.execute()
            return result.data or []
        except Exception as e:
            print(f"  Warning: could not query sender tasks: {e}")
            return []

    def analyze_with_claude(self, subject, sender, content, email_type, user_context=None):
        """Use Claude to analyze email with solar-sales-aware prompt"""

        # For regular emails, look up existing tasks for this sender
        sender_tasks = []
        sender_email = ''
        if email_type != 'plaud_transcription':
            sender_email = self._get_sender_email_address(sender)
            if sender_email:
                sender_tasks = self._get_existing_tasks_for_sender(sender_email, user_context)

        if email_type == 'plaud_transcription':
            prompt = self._build_plaud_prompt(subject, content, user_context=user_context)
        else:
            prompt = self._build_email_prompt(subject, sender, content, user_context=user_context,
                                              sender_email=sender_email, sender_tasks=sender_tasks)

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

    def _build_plaud_prompt(self, subject, content, user_context=None):
        """Build Claude prompt for Plaud voice transcription parsing"""
        ctx = self._build_user_prompt_context(user_context)

        businesses_list = '\n'.join(f'- {name}' for name in ctx['businesses'].keys())
        categories_list = '|'.join(ctx['categories'])

        return f"""You are {ctx['user_name']}'s AI task assistant for their business.

{ctx['user_name']} just recorded a voice memo after a call/site visit using their Plaud device.
The transcription below may contain MULTIPLE action items. Extract ALL of them.

TRANSCRIPTION:
{content}

{ctx['user_name'].upper()}'S BUSINESS CONTEXT:
- {ctx['user_name']} is {ctx['role_description']}
- Workflow: {ctx['workflow']}
- CRM: {ctx['crm_name']}
- Personal task manager: Jottask (jottask.app)
{ctx['extra_context']}

COMMON ACTION PATTERNS:
- "Call back [name]" → create_task (callback reminder)
- "Update CRM for [name]" or "add notes to [name]'s CRM" → update_crm
- "Send quote to [name]" → send_email (needs approval)
- "[Name] is going with option [X]" → change_deal_status + update_crm
- "Book site visit for [name] on [day]" → create_calendar_event
- "Follow up with [name] in [X] days" → create_task with due date
- "Need to check [something]" → create_task (research)
- "[Name] wants [change to quote]" → create_task (quote revision)

BUSINESSES:
{businesses_list}

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
            "business": "{ctx['default_business']}",
            "priority": "low|medium|high|urgent",
            "due_date": "YYYY-MM-DD or null",
            "due_time": "HH:MM or null",
            "category": "{categories_list}",
            "crm_notes": "If action_type is update_crm, the exact text to add as a CRM note. Keep {ctx['user_name']}'s voice — punchy, not formal.",
            "calendar_details": "If action_type is create_calendar_event: location, duration, attendees"
        }}
    ]
}}

Rules:
- CRITICAL: Task titles MUST use format "[Full Name]- [concise status/action]". Examples: "Graham Kildey- awaiting site photos and electricity bills", "Paul Thompson- follow up on battery quote", "Paul Van Zijl- call 8am re solar battery referral". NO space before the dash. Never use generic prefixes like "CRM Update:" or vague titles like "Follow up with Paul"
- If only a first name is in the voice memo, use the full name from the email subject line or content
- Extract EVERY action item, even if {ctx['user_name']} mentions it casually
- If an email address is visible anywhere in the email content, headers, or signature, capture it in the email_address field
- If {ctx['user_name']} says "call back" or "follow up", create a callback task with the right date
- If {ctx['user_name']} mentions updating CRM or adding notes, set action_type to "update_crm" and write the crm_notes in {ctx['user_name']}'s voice (short, punchy, no corporate speak)
- "Going with option X" = deal won, needs both change_deal_status and update_crm
- Default business is "{ctx['default_business']}" unless another business is mentioned
- For callbacks without a specific date, default to next business day
- For follow-ups, "in X days" means X calendar days from today ({_now_local(user_context).strftime('%Y-%m-%d')})
"""

    def _build_email_prompt(self, subject, sender, content, user_context=None,
                            sender_email='', sender_tasks=None):
        """Build Claude prompt for regular forwarded emails"""
        ctx = self._build_user_prompt_context(user_context)

        businesses_list = '\n'.join(f'- {name}' for name in ctx['businesses'].keys())
        categories_list = '|'.join(ctx['categories'])

        # Build sender context section if we have existing tasks
        sender_context = ''
        if sender_tasks:
            task_lines = []
            for t in sender_tasks:
                task_lines.append(f'  - [{t["id"][:8]}] "{t["title"]}" (due: {t.get("due_date", "none")}, status: {t["status"]})')
            sender_context = f"""
EXISTING OPEN TASKS FOR THIS SENDER ({sender_email}):
{chr(10).join(task_lines)}

IMPORTANT: If this email is a reply/update related to one of the above tasks, set action_type to "update_task_notes" and include "existing_task_id" with the task ID. Only create a NEW task if this email is about a genuinely different topic or request.
"""
        elif sender_email:
            sender_context = f"""
SENDER EMAIL: {sender_email}
No existing open tasks found for this sender — treat as a new enquiry if actionable.
"""

        return f"""You are {ctx['user_name']}'s AI task assistant for their business.

Analyze this forwarded email and extract any action items.

EMAIL DETAILS:
From: {sender}
Subject: {subject}
Content: {content}
{sender_context}
{ctx['user_name'].upper()}'S BUSINESS CONTEXT:
- {ctx['user_name']} is {ctx['role_description']}
- Workflow: {ctx['workflow']}
- CRM: {ctx['crm_name']}
- Personal task manager: Jottask (jottask.app)
{ctx['extra_context']}

BUSINESSES:
{businesses_list}

EXTRACT actions as JSON:
{{
    "summary": "One-line summary of what this email is about",
    "customer_name": "Customer FULL NAME (first + last) if this relates to a customer, null if not",
    "email_address": "{sender_email or 'null'}",
    "actions": [
        {{
            "action_type": "create_task|update_task_notes|update_crm|send_email|create_calendar_event|change_deal_status|set_callback",
            "existing_task_id": "If action_type is update_task_notes, the task ID to add notes to. null otherwise.",
            "title": "[Customer FULL NAME]- [concise status or action needed]",
            "description": "What needs to be done — include useful context like referral source, what they're waiting on, etc",
            "customer_name": "Customer FULL NAME (first + last)",
            "email_address": "Customer email if visible anywhere in the email headers, body, or signature, null if not",
            "business": "{ctx['default_business']}",
            "priority": "low|medium|high|urgent",
            "due_date": "YYYY-MM-DD or null",
            "due_time": "HH:MM or null",
            "category": "{categories_list}",
            "crm_notes": "If update_crm: the note text to add. null otherwise.",
            "calendar_details": "If create_calendar_event: details. null otherwise."
        }}
    ]
}}

Rules:
- CRITICAL: Task titles MUST use format "[Full Name]- [concise status/action]". Examples: "Graham Kildey- awaiting site photos and electricity bills", "Paul Thompson- follow up on battery quote", "Todd McHenry- site visit 8am Black Milk". NO space before the dash. Never use generic prefixes like "CRM Update:" or "New Lead:" — put the customer name first, then what's happening
- If only a first name appears in the subject, look in the email body/content for the full name
- Always scrape and capture email addresses — check the From header, email body, signatures, and any contact info in the content
- If existing open tasks are listed above and this email is a follow-up, use action_type "update_task_notes" with the existing_task_id
- New lead assignment emails → create_task with category "New Lead", priority "high", due today
- Customer replies about quotes → create_task with category "Quote Follow Up"
- If customer says yes/accepts → change_deal_status + create_task for next steps
- If customer asks questions → create_task to respond, priority medium
- Internal/admin emails → lower priority unless time-sensitive
- Default business is "{ctx['default_business']}" unless email content clearly relates to another business
- Today's date: {_now_local(user_context).strftime('%Y-%m-%d')}
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
        if action_type in ['create_task', 'set_callback', 'set_reminder', 'update_task_notes']:
            return self.TIER_1_AUTO

        return self.TIER_2_APPROVE

    # =========================================================================
    # ACTION EXECUTION (TIER 1 — AUTO)
    # =========================================================================

    def execute_action(self, action, user_context=None):
        """Execute a Tier 1 (auto) action"""
        action_type = action.get('action_type', '')

        try:
            if action_type == 'update_task_notes':
                self._update_existing_task(action, user_context=user_context)
            elif action_type in ['create_task', 'set_callback', 'set_reminder']:
                self._create_task(action, user_context=user_context)
            else:
                print(f"  Unknown auto action type: {action_type}")

        except Exception as e:
            print(f"  Error executing action '{action.get('title', '')}': {e}")

    def _update_existing_task(self, action, user_context=None):
        """Add a note to an existing task (AI-routed update, not a new task)"""
        existing_task_id = action.get('existing_task_id', '')
        if not existing_task_id:
            # Fallback: create a new task if no ID provided
            print(f"  Warning: update_task_notes without existing_task_id, creating new task")
            self._create_task(action, user_context=user_context)
            return

        note_content = f"{action.get('title', 'Email update')}"
        if action.get('description'):
            note_content += f"\n{action['description']}"

        self.tm.add_note(
            task_id=existing_task_id,
            content=note_content,
            source='email',
        )

        # Update client email on the task if we now have it
        client_email = action.get('email_address') or ''
        if client_email:
            try:
                self.tm.update_task_client_info(
                    existing_task_id,
                    client_email=client_email,
                )
            except Exception:
                pass

        print(f"  [AUTO] Note added to task {existing_task_id[:8]}: {action.get('title', '')[:40]}")

    def _create_task(self, action, user_context=None):
        """Create a task in Supabase, or add a note to an existing task if the
        same client already has an open task."""
        if user_context:
            business_id = user_context.businesses.get(
                action.get('business', ''),
                list(user_context.businesses.values())[0] if user_context.businesses else None
            )
            user_id = user_context.user_id
        else:
            business_id = self.businesses.get(
                action.get('business', 'Cloud Clean Energy')
            )
            user_id = os.getenv('ROB_USER_ID', 'e515407e-dbd6-4331-a815-1878815c89bc')

        client_name = action.get('customer_name') or ''
        client_email = action.get('email_address') or ''

        # --- Smart routing: check for existing open task for this client ---
        existing_task = None
        if client_email or client_name:
            try:
                existing_task = self.tm.find_existing_task_by_client(
                    client_email=client_email or None,
                    client_name=client_name or None,
                )
            except Exception as e:
                print(f"  Warning: client match lookup failed: {e}")

        if existing_task:
            # Add a note to the existing task instead of creating a duplicate
            note_content = f"Email update: {action['title']}"
            if action.get('description'):
                note_content += f"\n{action['description']}"

            self.tm.add_note(
                task_id=existing_task['id'],
                content=note_content,
                source='email',
            )

            # Update client_email on the existing task if we now have it
            if client_email and not existing_task.get('client_email'):
                try:
                    self.tm.update_task_client_info(
                        existing_task['id'],
                        client_email=client_email,
                    )
                except Exception:
                    pass

            print(f"  [AUTO] Note added to existing task '{existing_task['title'][:40]}' instead of creating duplicate")
            return

        # --- No existing task: create a new one ---
        task_data = {
            'business_id': business_id,
            'user_id': user_id,
            'title': action['title'],
            'description': action.get('description', ''),
            'due_date': action.get('due_date'),
            'due_time': action.get('due_time'),
            'priority': action.get('priority', 'medium'),
            'is_meeting': action.get('action_type') == 'create_calendar_event',
            'status': 'pending',
        }

        # Add client info to the task record
        if client_name:
            task_data['client_name'] = client_name
        if client_email:
            task_data['client_email'] = client_email.lower()

        result = self.tm.supabase.table('tasks').insert(task_data).execute()

        if result.data:
            task = result.data[0]
            print(f"  [AUTO] Task created: {task['title']}")

            # Increment usage meter
            self._increment_task_count(user_id)
        else:
            print(f"  Failed to create task: {action['title']}")

    def _increment_task_count(self, user_id):
        """Increment tasks_this_month for usage metering"""
        try:
            current_month = _now_local().strftime('%Y-%m')
            # Fetch current counter
            result = self.tm.supabase.table('users') \
                .select('tasks_this_month, tasks_month_reset') \
                .eq('id', user_id).execute()

            if result.data:
                row = result.data[0]
                count = row.get('tasks_this_month') or 0
                reset_month = row.get('tasks_month_reset') or ''

                if reset_month != current_month:
                    # New month — reset counter
                    count = 0

                self.tm.supabase.table('users').update({
                    'tasks_this_month': count + 1,
                    'tasks_month_reset': current_month,
                }).eq('id', user_id).execute()
        except Exception as e:
            print(f"  Warning: Could not update task count: {e}")

    # =========================================================================
    # APPROVAL FLOW (TIER 2 — EMAIL APPROVAL)
    # =========================================================================

    def send_approval_email(self, email_subject, email_sender, actions, context, user_context=None):
        """Send approval email with action buttons for Tier 2 actions"""

        # Determine recipient and company name
        if user_context:
            recipient_email = user_context.email_address
            company_name = user_context.company_name or 'Jottask'
            user_name = user_context.full_name or 'User'
        else:
            recipient_email = os.getenv('ROB_EMAIL', 'rob@cloudcleanenergy.com.au')
            company_name = 'Cloud Clean Energy'
            user_name = 'Rob'

        # Generate approval tokens for each action
        action_items_html = ""
        for i, action in enumerate(actions):
            token = self._generate_action_token(action)

            # Store pending action in Supabase
            self._store_pending_action(token, action, user_context=user_context)

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
                    {user_name}'s AI Task Manager &bull; {company_name}
                </div>
            </div>
        </div>
        """

        # Send via Resend
        try:
            params = {
                "from": self.from_email,
                "to": [recipient_email],
                "subject": f"Jottask Approval: {' '.join(email_subject.split())}",
                "html": email_html
            }
            response = resend.Emails.send(params)
            print(f"  Resend response: {response}")
            print(f"  Approval email sent to {recipient_email} for {len(actions)} action(s)")
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
        raw = f"{action.get('title', '')}-{datetime.now(pytz.UTC).isoformat()}-{uuid.uuid4()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _store_pending_action(self, token, action, user_context=None):
        """Store a pending action in Supabase for later approval/execution"""
        try:
            data = {
                'token': token,
                'action_type': action.get('action_type'),
                'action_data': json.dumps(action),
                'status': 'pending',
                'created_at': datetime.now(pytz.UTC).isoformat(),
                'expires_at': (datetime.now(pytz.UTC) + timedelta(days=7)).isoformat(),
            }
            if user_context:
                data['user_id'] = user_context.user_id

            self.tm.supabase.table('pending_actions').insert(data).execute()
        except Exception as e:
            print(f"  Error storing pending action: {e}")

    # =========================================================================
    # APPROVAL EXECUTION (called when user clicks Approve)
    # =========================================================================

    def execute_approved_action(self, token):
        """Execute an action that has been approved via email button"""
        try:
            # Fetch pending action
            result = self.tm.supabase.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()

            if not result.data:
                return {'success': False, 'message': 'Action not found or already processed'}

            pending = result.data[0]
            action = json.loads(pending['action_data'])
            action_type = action.get('action_type', '')

            # Build user_context from pending action's user_id if available
            user_context = self._user_context_from_pending(pending)

            # Execute based on type
            success = False
            message = ''

            if action_type == 'update_crm':
                success, message = self._execute_crm_update(action, user_context=user_context)

            elif action_type == 'send_email':
                success, message = self._execute_send_email(action, user_context=user_context)

            elif action_type == 'create_calendar_event':
                success, message = self._execute_calendar_event(action, user_context=user_context)

            elif action_type == 'change_deal_status':
                success, message = self._execute_deal_status_change(action, user_context=user_context)

            else:
                # Fallback: create as a task
                self._create_task(action, user_context=user_context)
                success = True
                message = f"Created task: {action.get('title', '')}"

            # Mark as processed
            self.tm.supabase.table('pending_actions').update({
                'status': 'approved' if success else 'failed',
                'processed_at': datetime.now(pytz.UTC).isoformat(),
            }).eq('token', token).execute()

            return {'success': success, 'message': message}

        except Exception as e:
            return {'success': False, 'message': str(e)}

    def _user_context_from_pending(self, pending_row):
        """Build a minimal UserContext from a pending_actions row (for approval execution)"""
        pending_user_id = pending_row.get('user_id')
        if not pending_user_id:
            return None

        try:
            result = self.tm.supabase.table('users') \
                .select('id, email, full_name, company_name, timezone, ai_context') \
                .eq('id', pending_user_id).execute()

            if result.data:
                user = result.data[0]
                ai_ctx = user.get('ai_context') or {}
                businesses = ai_ctx.get('businesses', {})
                if not businesses:
                    businesses = self.businesses.copy()

                return UserContext(
                    user_id=str(user['id']),
                    email_address=user.get('email', ''),
                    company_name=user.get('company_name', ''),
                    full_name=user.get('full_name', ''),
                    timezone=user.get('timezone', 'Australia/Brisbane'),
                    businesses=businesses,
                    ai_context=ai_ctx,
                )
        except Exception as e:
            print(f"  Warning: Could not load user context for pending action: {e}")

        return None

    def reject_action(self, token):
        """Mark an action as rejected (user clicked Skip)"""
        try:
            self.tm.supabase.table('pending_actions').update({
                'status': 'rejected',
                'processed_at': datetime.now(pytz.UTC).isoformat(),
            }).eq('token', token).execute()
            return {'success': True, 'message': 'Action skipped'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # =========================================================================
    # TIER 2 ACTION EXECUTORS
    # =========================================================================

    def _execute_crm_update(self, action, user_context=None):
        """Update CRM with notes — tries CRM push first, falls back to task creation"""
        customer = action.get('customer_name', 'Unknown')
        notes = action.get('crm_notes', '')

        # Try CRM connector push first
        if user_context and user_context.user_id:
            try:
                from crm_manager import CRMManager
                crm = CRMManager()
                result = crm.execute_crm_update(
                    user_id=user_context.user_id,
                    customer_name=customer,
                    crm_notes=notes,
                    customer_email=action.get('customer_email', ''),
                )
                if result.success:
                    print(f"  CRM sync success for {customer}: {result.message}")
                    return True, f"CRM updated for {customer}: {result.message}"
                else:
                    print(f"  CRM sync unavailable for {customer}: {result.message} — falling back to task")
            except Exception as e:
                print(f"  CRM sync error for {customer}: {e} — falling back to task")

        # Fallback: create reminder task (original behavior)
        default_business = 'Cloud Clean Energy'
        if user_context and user_context.ai_context:
            default_business = user_context.ai_context.get('default_business', default_business)

        self._create_task({
            'title': f"CRM Update: {customer}",
            'description': f"Add to CRM notes:\n{notes}",
            'business': default_business,
            'priority': 'high',
            'due_date': _now_local(user_context).strftime('%Y-%m-%d'),
            'category': 'CRM Update',
        }, user_context=user_context)

        return True, f"CRM update task created for {customer}"

    def _execute_send_email(self, action, user_context=None):
        """Draft an email (creates task — actual sending done by user)"""
        customer = action.get('customer_name', 'Unknown')

        default_business = 'Cloud Clean Energy'
        if user_context and user_context.ai_context:
            default_business = user_context.ai_context.get('default_business', default_business)

        self._create_task({
            'title': f"Send email to {customer}",
            'description': action.get('description', ''),
            'business': default_business,
            'priority': 'high',
            'due_date': _now_local(user_context).strftime('%Y-%m-%d'),
            'category': 'Quote Follow Up',
        }, user_context=user_context)

        return True, f"Email task created for {customer}"

    def _execute_calendar_event(self, action, user_context=None):
        """Create a calendar event (creates task for now)"""
        customer = action.get('customer_name', 'Unknown')
        details = action.get('calendar_details', '')

        default_business = 'Cloud Clean Energy'
        if user_context and user_context.ai_context:
            default_business = user_context.ai_context.get('default_business', default_business)

        self._create_task({
            'title': f"Calendar: {action.get('title', '')}",
            'description': f"Customer: {customer}\nDetails: {details}",
            'business': default_business,
            'priority': 'high',
            'due_date': action.get('due_date'),
            'due_time': action.get('due_time'),
            'category': 'Site Visit',
            'action_type': 'create_calendar_event',
        }, user_context=user_context)

        return True, f"Calendar event task created for {customer}"

    def _execute_deal_status_change(self, action, user_context=None):
        """Change deal status (creates task for now)"""
        customer = action.get('customer_name', 'Unknown')

        default_business = 'Cloud Clean Energy'
        if user_context and user_context.ai_context:
            default_business = user_context.ai_context.get('default_business', default_business)

        self._create_task({
            'title': f"Deal Update: {customer}",
            'description': action.get('description', ''),
            'business': default_business,
            'priority': 'urgent',
            'due_date': _now_local(user_context).strftime('%Y-%m-%d'),
            'category': 'General',
        }, user_context=user_context)

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
    poll_interval = int(os.getenv('POLL_INTERVAL_SECONDS', '60'))  # 60s loop, per-connection frequency managed by last_sync_at
    print(f"Starting multi-tenant email polling loop (every {poll_interval}s)...")
    while True:
        try:
            processor.process_all_connections()
        except Exception as e:
            print(f"Error in polling cycle: {e}")
        print(f"Sleeping {poll_interval}s until next check...")
        time.sleep(poll_interval)
