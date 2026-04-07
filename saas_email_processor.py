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
# install_order imported lazily inside _handle_opensolar_accepted()
# to isolate failures — a broken install_order.py won't kill reminders/email processing
import os
import uuid
import hashlib
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

        # Outbound emails routed through email_utils.send_email() (retries + monitoring)

        # Business IDs come from per-user ai_context (no hardcoded fallback)
        self.businesses = {}

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
            # Query connections separately from users to avoid RLS/JOIN issues
            result = self.tm.supabase.table('email_connections') \
                .select('*') \
                .eq('is_active', True) \
                .execute()

            if not result.data:
                print("  _get_active_connections: no rows in email_connections with is_active=true")
                return []

            print(f"  _get_active_connections: found {len(result.data)} active connection(s)")

            now = datetime.now(pytz.UTC)
            due_connections = []
            for conn in result.data:
                last_sync = conn.get('last_sync_at')
                freq_minutes = conn.get('sync_frequency_minutes', 15)

                if last_sync:
                    last_sync_dt = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
                    next_sync = last_sync_dt + timedelta(minutes=freq_minutes)
                    if now < next_sync:
                        mins_left = (next_sync - now).total_seconds() / 60
                        print(f"    Connection {conn.get('email_address')}: not due yet ({mins_left:.1f}m remaining)")
                        continue  # Not due yet

                # Fetch user data separately (avoids JOIN/RLS issues)
                user_id = conn.get('user_id')
                if user_id:
                    try:
                        user_result = self.tm.supabase.table('users') \
                            .select('id, email, full_name, company_name, timezone, ai_context') \
                            .eq('id', user_id) \
                            .single() \
                            .execute()
                        conn['users'] = user_result.data if user_result.data else {}
                    except Exception as ue:
                        print(f"    Warning: could not fetch user data for {user_id}: {ue}")
                        conn['users'] = {}

                due_connections.append(conn)

            return due_connections

        except Exception as e:
            print(f"Error fetching active connections: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _ensure_legacy_context(self):
        """Build a default UserContext for the legacy single-inbox fallback.
        Finds the first global_admin (or any user with ai_context) to use as default."""
        if getattr(self, '_legacy_context', None):
            return  # Already set
        try:
            # Find the admin user with ai_context
            result = self.tm.supabase.table('users') \
                .select('id, email, full_name, company_name, timezone, ai_context') \
                .eq('role', 'global_admin') \
                .limit(1) \
                .execute()
            if result.data:
                user = result.data[0]
                ai_ctx = user.get('ai_context') or {}
                businesses = ai_ctx.get('businesses', {})
                self._legacy_context = UserContext(
                    user_id=user['id'],
                    email_address=user.get('email', ''),
                    company_name=user.get('company_name', ''),
                    full_name=user.get('full_name', ''),
                    timezone=user.get('timezone', 'Australia/Brisbane'),
                    businesses=businesses,
                    ai_context=ai_ctx,
                    connection_id=None,
                )
                print(f"  Legacy context: using admin {user.get('full_name')} ({user['id'][:8]}...)")
            else:
                print("  WARNING: No global_admin user found for legacy context")
                self._legacy_context = None
        except Exception as e:
            print(f"  Error building legacy context: {e}")
            self._legacy_context = None

    def _find_user_by_email(self, sender_email):
        """Look up a registered user by their email or alternate_emails.
        Returns a UserContext if found, None otherwise."""
        if not sender_email:
            return None
        sender_lower = sender_email.lower()
        try:
            # Check primary email first
            result = self.tm.supabase.table('users') \
                .select('id, email, full_name, company_name, timezone, ai_context') \
                .eq('email', sender_lower) \
                .limit(1) \
                .execute()
            if result.data:
                user = result.data[0]
                ai_ctx = user.get('ai_context') or {}
                businesses = ai_ctx.get('businesses', {})
                print(f"  Sender match (primary): {sender_lower} → {user.get('full_name')} ({user['id'][:8]}...)")
                return UserContext(
                    user_id=user['id'],
                    email_address=user.get('email', ''),
                    company_name=user.get('company_name', ''),
                    full_name=user.get('full_name', ''),
                    timezone=user.get('timezone', 'Australia/Brisbane'),
                    businesses=businesses,
                    ai_context=ai_ctx,
                    connection_id=None,
                )

            # Check alternate_emails (stored as JSONB array)
            result = self.tm.supabase.table('users') \
                .select('id, email, full_name, company_name, timezone, ai_context, alternate_emails') \
                .not_.is_('alternate_emails', 'null') \
                .execute()
            for user in (result.data or []):
                alt_emails = user.get('alternate_emails') or []
                if sender_lower in [e.lower() for e in alt_emails]:
                    ai_ctx = user.get('ai_context') or {}
                    businesses = ai_ctx.get('businesses', {})
                    print(f"  Sender match (alternate): {sender_lower} → {user.get('full_name')} ({user['id'][:8]}...)")
                    return UserContext(
                        user_id=user['id'],
                        email_address=user.get('email', ''),
                        company_name=user.get('company_name', ''),
                        full_name=user.get('full_name', ''),
                        timezone=user.get('timezone', 'Australia/Brisbane'),
                        businesses=businesses,
                        ai_context=ai_ctx,
                        connection_id=None,
                    )
        except Exception as e:
            print(f"  Error looking up sender {sender_email}: {e}")
        return None

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

                sender_raw = email_body.get('From', '')
                sender_addr = self._get_sender_email_address(sender_raw)

                result = self.process_single_email_body(email_body, user_context=user_ctx)
                outcome, outcome_detail = result if result else ('error', 'No result returned')
                processed_count += 1

                self._mark_email_processed(
                    message_id, msg_id_str,
                    connection_id=user_ctx.connection_id, user_id=user_ctx.user_id,
                    sender_email=sender_addr, sender_name=sender_raw,
                    subject=raw_subject,
                    outcome=outcome, outcome_detail=outcome_detail,
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
            # Fallback: if no DB connections found (or none due), try legacy single-inbox
            # This keeps the system working even if email_connections table is empty
            if self.email_password:
                print("No DB connections due — falling back to legacy single-inbox mode")
                self._ensure_legacy_context()
                self.process_forwarded_emails()
            else:
                print("No active email connections found and no env credentials. Nothing to process.")

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

                # Decode subject
                raw_subject = email_body.get('Subject', '')
                if raw_subject:
                    decoded_parts = decode_header(raw_subject)
                    raw_subject = ''.join(
                        part.decode(enc or 'utf-8') if isinstance(part, bytes) else part
                        for part, enc in decoded_parts
                    )

                # Extract sender info for history tracking
                sender_raw = email_body.get('From', '')
                sender_addr = self._get_sender_email_address(sender_raw)

                # Match sender to a registered user; fall back to admin context
                matched_context = self._find_user_by_email(sender_addr)
                if not matched_context:
                    matched_context = getattr(self, '_legacy_context', None)

                # Process this email with the matched user's context
                result = self.process_single_email_body(email_body, user_context=matched_context)
                outcome, outcome_detail = result if result else ('error', 'No result returned')
                processed_count += 1

                # Mark as processed in Supabase with sender info + outcome
                self._mark_email_processed(
                    message_id, msg_id_str,
                    sender_email=sender_addr, sender_name=sender_raw,
                    subject=raw_subject,
                    outcome=outcome, outcome_detail=outcome_detail,
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
                              subject=None, outcome=None, outcome_detail=None):
        """Mark an email as processed in Supabase, with optional sender info and outcome tracking"""
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

            # Sender info columns (migration 017) + outcome columns (migration 020)
            # We try with them first; if the columns don't exist yet, retry without.
            extra_data = {}
            if sender_email:
                extra_data['sender_email'] = sender_email.lower()
            if sender_name:
                extra_data['sender_name'] = sender_name
            if subject:
                extra_data['subject'] = subject[:500]
            if outcome:
                extra_data['outcome'] = outcome
            if outcome_detail:
                extra_data['outcome_detail'] = str(outcome_detail)[:500]

            insert_data = {**data, **extra_data}

            try:
                self.tm.supabase.table('processed_emails').insert(insert_data).execute()
            except Exception as col_err:
                if 'column' in str(col_err).lower() or 'schema' in str(col_err).lower():
                    # Migration 017/020 not run yet — retry without new columns
                    self.tm.supabase.table('processed_emails').insert(data).execute()
                else:
                    raise col_err

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
        """Process one email from an already-fetched email body.

        Returns (outcome, detail) tuple:
        - ('task_created', 'title...') — AI created task(s)
        - ('note_added', 'Added note to existing task') — AI routed to existing task
        - ('approval_queued', 'N actions queued') — Tier 2 actions sent for approval
        - ('opensolar', 'Install order for ...') — OpenSolar detection path
        - ('no_action', 'AI found no actionable items') — AI returned empty actions
        - ('error', 'Error: ...') — exception during processing
        """
        try:

            subject = decode_header(email_body['Subject'])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode()

            sender = email_body['From']
            content = self.extract_email_content(email_body)

            # --- OpenSolar "Customer Accepted" detection (before AI analysis) ---
            # Lazy import: if install_order.py is broken, only this path fails
            try:
                from install_order import is_opensolar_accepted
            except Exception as _io_err:
                print(f"  [OPENSOLAR] install_order import failed: {_io_err}")
                is_opensolar_accepted = lambda s, subj: False

            if is_opensolar_accepted(sender, subject):
                print(f"[OPENSOLAR] Detected: {subject}")
                self._handle_opensolar_accepted(subject, content, user_context=user_context)
                return ('opensolar', f'Install order: {subject[:200]}')

            # Detect email type
            is_plaud = self.is_plaud_transcription(sender)
            email_type = 'plaud_transcription' if is_plaud else 'forwarded_email'

            print(f"{'[PLAUD]' if is_plaud else '[EMAIL]'} Analyzing: {subject}")
            sender_email_addr = self._get_sender_email_address(sender)
            subject_lower = (subject or '').lower()
            # Self-generated lead from rob.l@directsolarwholesaler.com.au — "New Lead: ..."
            if subject_lower.startswith('new lead:') and 're:' not in subject_lower:
                if handle_dsw_new_lead(subject, content, sender_email_addr):
                    return ("task_created", f"DSW self-generated lead: {subject[:80]}")
            # Reply from DSW with call notes — "Re: New Lead: ..."
            if 're:' in subject_lower and 'new lead:' in subject_lower:
                if handle_dsw_reply(subject, content, sender_email_addr):
                    return ("dsw_reply", "DSW lead notes updated")
            # Forwarded/unstructured lead from rob.l — FW:/Fwd: or body has AU phone
            if handle_dsw_forward(subject, content, sender_email_addr):
                return ("task_created", f"DSW forward lead: {subject[:80]}")

            # Parse with appropriate prompt
            analysis = self.analyze_with_claude(subject, sender, content, email_type, user_context=user_context)

            if not analysis or not analysis.get('actions'):
                print(f"  No actionable items found")
                summary = (analysis or {}).get('summary', 'AI found no actionable items')
                return ('no_action', summary[:500])

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

            # Track what we did for outcome reporting
            outcomes = []

            # Execute Tier 1 actions immediately
            # batch_created tracks tasks created in this email to prevent within-batch duplicates
            batch_created = {}
            if auto_actions:
                print(f"  Auto-executing {len(auto_actions)} low-risk action(s)...")
                for action in auto_actions:
                    self.execute_action(action, user_context=user_context, batch_created=batch_created)
                    atype = action.get('action_type', '')
                    if atype == 'update_task_notes':
                        outcomes.append('note_added')
                    else:
                        outcomes.append('task_created')

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
                outcomes.append('approval_queued')

            # Determine primary outcome
            if 'task_created' in outcomes:
                titles = ', '.join(a.get('title', '')[:60] for a in auto_actions if a.get('action_type') != 'update_task_notes')
                return ('task_created', titles[:500] or 'Task created')
            elif 'approval_queued' in outcomes:
                return ('approval_queued', f'{len(approval_actions)} action(s) queued for approval')
            elif 'note_added' in outcomes:
                return ('note_added', f'Added note to existing task')
            else:
                return ('no_action', 'No actions executed')

        except Exception as e:
            print(f"Error processing email: {e}")
            return ('error', f'Error: {str(e)[:480]}')

    # =========================================================================
    # PLAUD DETECTION
    # =========================================================================

    def is_plaud_transcription(self, sender):
        """Detect if email is a Plaud voice transcription"""
        sender_lower = sender.lower()
        return any(plaud in sender_lower for plaud in self.plaud_senders)

    # =========================================================================
    # OPENSOLAR — "Customer Accepted" install order automation
    # =========================================================================

    def _handle_opensolar_accepted(self, subject, content, user_context=None):
        """Handle an OpenSolar 'Customer Accepted' email:
        parse → CRM lookup → format WhatsApp draft → create task → send email"""
        from install_order import (
            parse_opensolar_email, lookup_crm_by_address,
            format_install_order_draft, send_install_order_email,
        )

        if not user_context:
            print("  [OPENSOLAR] No user context — skipping")
            return

        # 1. Parse the email
        notification = parse_opensolar_email(subject, content)
        if not notification:
            print(f"  [OPENSOLAR] Could not parse project details from email")
            return

        print(f"  [OPENSOLAR] Project {notification.project_id}: {notification.address}")

        # 2. Look up CRM/task context by address
        crm_context = lookup_crm_by_address(
            user_id=user_context.user_id,
            address=notification.address,
            tm=self.tm,
        )
        if crm_context.customer_name:
            print(f"  [OPENSOLAR] Found customer: {crm_context.customer_name}")
        else:
            print(f"  [OPENSOLAR] No customer name found — draft will have placeholder")

        # 3. Format WhatsApp draft (equipment=None in Phase 1)
        whatsapp_draft = format_install_order_draft(
            notification=notification,
            crm_context=crm_context,
            equipment=None,
        )

        # 4. Create a task in the dashboard
        customer_display = crm_context.customer_name or notification.address
        task_title = f"Install Order - {customer_display}"

        task_description = (
            f"Customer accepted proposal on OpenSolar.\n"
            f"Project: {notification.address}\n"
            f"OpenSolar: {notification.project_link}\n"
        )
        if crm_context.customer_name:
            task_description += f"Customer: {crm_context.customer_name}\n"

        self._create_task({
            'action_type': 'create_task',
            'title': task_title,
            'description': task_description,
            'business': self._get_default_business(user_context),
            'priority': 'high',
            'due_date': _now_local(user_context).strftime('%Y-%m-%d'),
            'due_time': '09:00',
            'category': 'Install Order',
            'customer_name': crm_context.customer_name,
            'email_address': crm_context.client_email,
        }, user_context=user_context)

        # 5. Send install order email with WhatsApp draft
        send_install_order_email(
            recipient_email=user_context.email_address,
            notification=notification,
            whatsapp_draft=whatsapp_draft,
            crm_context=crm_context,
            user_name=user_context.full_name or 'User',
        )

        print(f"  [OPENSOLAR] Install order processed for {notification.address}")

    def _get_default_business(self, user_context):
        """Get the default business name from user context"""
        if user_context and user_context.ai_context:
            return user_context.ai_context.get('default_business', '')
        if user_context and user_context.businesses:
            return list(user_context.businesses.keys())[0]
        return ''

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
            # Log what we're sending to AI for debugging
            print(f"  [AI INPUT] Subject: {subject}")
            print(f"  [AI INPUT] Content length: {len(content)} chars")
            print(f"  [AI INPUT] Content preview: {content[:200]}")

            response = self.claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                system="You are a task extraction assistant. Your job is to ALWAYS create tasks from emails. NEVER return empty actions unless the email is pure spam. The subject line alone is enough to create a task.",
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse JSON response
            raw = response.content[0].text
            print(f"  [AI OUTPUT] Raw response: {raw[:500]}")
            # Handle markdown code blocks
            if '```json' in raw:
                raw = raw.split('```json')[1].split('```')[0]
            elif '```' in raw:
                raw = raw.split('```')[1].split('```')[0]

            parsed = json.loads(raw.strip())
            actions_count = len(parsed.get('actions', []))
            print(f"  [AI OUTPUT] Parsed {actions_count} actions")
            if actions_count == 0:
                print(f"  [AI WARNING] Zero actions returned! Summary: {parsed.get('summary', 'none')}")
            return parsed

        except json.JSONDecodeError:
            print(f"  Could not parse AI response: {raw[:300]}")
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
- ALWAYS set due_date — if no date mentioned, use today for urgent/high or next business day for medium/low
- ALWAYS set due_time — if no time mentioned, use "09:00" for morning tasks, "14:00" for afternoon follow-ups. Never leave null
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

        # Detect if this is an outgoing email CC'd to Jottask
        user_email = ''
        if user_context and user_context.email_address:
            user_email = user_context.email_address.lower()

        outgoing_note = ''
        if user_email and user_email in sender_email:
            outgoing_note = f"""
CC'D OUTGOING EMAIL DETECTED:
This email is FROM {ctx['user_name']} (the user) TO a customer. {ctx['user_name']} CC'd Jottask to track this outreach. This is NOT a forwarded email — it's {ctx['user_name']}'s own sent email.
You MUST create a follow-up task so {ctx['user_name']} remembers to check if the customer replied.
"""

        return f"""You are {ctx['user_name']}'s AI task assistant for their business.

Analyze this email and extract any action items.

EMAIL DETAILS:
From: {sender}
Subject: {subject}
Content: {content}
{outgoing_note}{sender_context}
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
            "client_phone": "Phone number if found anywhere in the email (body, signature, contact info), null if not",
            "client_address": "Street address or suburb if found in the email, null if not",
            "system_size": "Solar system size if mentioned (e.g. '6.6kW', '10kW + 13.5kWh battery'), null if not",
            "electricity_bill": "Electricity bill amount or usage if mentioned (e.g. '$400/qtr', '30kWh/day'), null if not",
            "roof_type": "Roof type if mentioned (e.g. 'tin', 'tile', 'colorbond', 'flat'), null if not",
            "referral_source": "How the lead found them (e.g. 'SolarQuotes', 'Google', 'referral from John', 'Facebook'), null if not",
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
- CRITICAL — GOLDEN RULE: Every email forwarded to Jottask is an INSTRUCTION to create a task. NEVER return empty actions unless the email is genuinely automated spam/marketing with zero human intent. If in doubt, CREATE THE TASK. The user forwarded it for a reason.
- CRITICAL: The SUBJECT LINE is often the entire instruction. If the subject contains any person name, time, action, or reminder — that IS the task. Create it even if the email body is empty or just a signature. Examples: "Mal smith send quote again and call 5pm today", "Rie Shresta site visit 1pm today", "Tony Mason - remind me 6pm today", "John 3pm", "Call Sarah back". ALL of these MUST create tasks.
- CRITICAL: If a time (e.g. "5pm", "1pm", "6pm", "3pm", "10am") appears ANYWHERE in the subject or body, ALWAYS set that as due_time with today as due_date. If "remind me" appears, this is an explicit reminder request — priority high.
- CRITICAL: Task titles MUST use format "[Full Name]- [concise status/action]". Examples: "Graham Kildey- awaiting site photos and electricity bills", "Paul Thompson- follow up on battery quote", "Todd McHenry- site visit 8am Black Milk". NO space before the dash. Never use generic prefixes like "CRM Update:" or "New Lead:" — put the customer name first, then what's happening
- CRITICAL: If the email is FROM {ctx['user_name']} (CC'd to Jottask), this is an OUTGOING email. ALWAYS create a follow-up task like "[Customer Name]- follow up if no reply" with category "Remember to Callback", due in 2 business days, priority medium. Extract the customer name from the To field or subject line. This is the most common way {ctx['user_name']} uses Jottask — NEVER return empty actions for CC'd outgoing emails.
- If only a first name appears in the subject, look in the email body/content for the full name
- Always scrape and capture email addresses — check the From header, To header, email body, signatures, and any contact info in the content
- LEAD DETAILS: For new leads or enquiries, extract ALL available details: phone numbers (Australian mobile 04xx, landline 07/02/03/08), street addresses or suburbs, system size requests, electricity bill amounts, roof type, and how they found us (referral source). These fields help auto-populate CRM entries. Check the entire email body and signature for this info.
- If existing open tasks are listed above and this email is a follow-up, use action_type "update_task_notes" with the existing_task_id
- New lead assignment emails → create_task with category "New Lead", priority "high", due today
- Customer replies about quotes → create_task with category "Quote Follow Up"
- If customer says yes/accepts → change_deal_status + create_task for next steps
- If customer asks questions → create_task to respond, priority medium
- Internal/admin emails → lower priority unless time-sensitive
- Default business is "{ctx['default_business']}" unless email content clearly relates to another business
- Today's date: {_now_local(user_context).strftime('%Y-%m-%d')}
- ALWAYS set due_date — if no date is mentioned, use today's date for urgent/high or next business day for medium/low
- ALWAYS set due_time — if no time is mentioned, use "09:00" for morning tasks, "14:00" for afternoon follow-ups. Never leave due_time as null
- Only return empty actions for bulk marketing emails, automated system notifications with no human action needed, or out-of-office replies. Everything else gets a task.
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

    def execute_action(self, action, user_context=None, batch_created=None):
        """Execute a Tier 1 (auto) action.

        batch_created: shared dict for within-batch dedup (passed to _create_task).
        """
        action_type = action.get('action_type', '')

        try:
            if action_type == 'update_task_notes':
                self._update_existing_task(action, user_context=user_context)
            elif action_type in ['create_task', 'set_callback', 'set_reminder']:
                self._create_task(action, user_context=user_context, batch_created=batch_created)
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

    def _create_task(self, action, user_context=None, batch_created=None):
        """Create a task in Supabase, or add a note to an existing task if the
        same client already has an open task.

        batch_created: dict mapping client_name_lower -> task dict, for within-batch
        dedup when a single email produces multiple actions for the same client.
        """
        if user_context:
            business_id = user_context.businesses.get(
                action.get('business', ''),
                list(user_context.businesses.values())[0] if user_context.businesses else None
            )
            user_id = user_context.user_id
        else:
            print("[WARNING] _create_task called without user_context — skipping")
            return None

        if batch_created is None:
            batch_created = {}

        client_name = action.get('customer_name') or ''
        client_email = action.get('email_address') or ''

        # --- Within-batch dedup: check if we already created a task for this client in this email ---
        client_key = (client_name.strip().lower() or client_email.strip().lower())
        if client_key and client_key in batch_created:
            existing_batch_task = batch_created[client_key]
            note_content = f"Email update: {action['title']}"
            if action.get('description'):
                note_content += f"\n{action['description']}"
            self.tm.add_note(
                task_id=existing_batch_task['id'],
                content=note_content,
                source='email',
            )
            print(f"  [AUTO] Note added to batch task '{existing_batch_task['title'][:40]}' (within-batch dedup)")
            return None

        # --- Smart routing: check for existing open task for this client ---
        existing_task = None
        if client_email or client_name:
            try:
                existing_task = self.tm.find_existing_task_by_client(
                    client_email=client_email or None,
                    client_name=client_name or None,
                    user_id=user_id,
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
        # Default due_date to today and due_time to 09:00 if AI didn't extract them
        # This ensures every task gets a reminder from the scheduler
        due_date = action.get('due_date')
        due_time = action.get('due_time')
        if not due_date:
            due_date = _now_local(user_context).strftime('%Y-%m-%d')
        if not due_time:
            due_time = '09:00:00'
        elif len(due_time) == 5:  # "09:00" → "09:00:00"
            due_time = due_time + ':00'

        task_data = {
            'business_id': business_id,
            'user_id': user_id,
            'title': action['title'],
            'description': action.get('description', ''),
            'due_date': due_date,
            'due_time': due_time,
            'priority': action.get('priority', 'medium'),
            'category': action.get('category') or 'DSW Solar',
            'is_meeting': action.get('action_type') == 'create_calendar_event',
            'status': 'pending',
        }

        # Add client info to the task record (columns may not exist pre-migration)
        if client_name:
            task_data['client_name'] = client_name
        if client_email:
            task_data['client_email'] = client_email.lower()

        # Add phone if extracted
        client_phone = action.get('client_phone')
        if client_phone:
            task_data['client_phone'] = client_phone

        # Build structured lead details block and append to description
        lead_fields = []
        for field_key, field_label in [
            ('client_address', 'Address'),
            ('system_size', 'System Size'),
            ('electricity_bill', 'Electricity Bill'),
            ('roof_type', 'Roof Type'),
            ('referral_source', 'Referral Source'),
        ]:
            val = action.get(field_key)
            if val and val != 'null':
                lead_fields.append(f"{field_label}: {val}")

        if lead_fields:
            lead_block = "\n\n--- LEAD DETAILS ---\n" + "\n".join(lead_fields)
            task_data['description'] = (task_data.get('description') or '') + lead_block

        try:
            result = self.tm.supabase.table('tasks').insert(task_data).execute()
        except Exception as col_err:
            if 'column' in str(col_err).lower() or 'schema' in str(col_err).lower():
                # client columns may not exist yet — retry without them
                task_data.pop('client_email', None)
                task_data.pop('client_phone', None)
                result = self.tm.supabase.table('tasks').insert(task_data).execute()
            else:
                raise col_err

        if result.data:
            task = result.data[0]
            print(f"  [AUTO] Task created: {task['title']}")

            # Track in batch for within-batch dedup
            if client_key:
                batch_created[client_key] = task

            # Send confirmation email to user
            if user_context and user_context.email_address:
                self._send_task_confirmation(
                    user_email=user_context.email_address,
                    user_name=user_context.full_name,
                    task=task,
                )

            # Increment usage meter
            self._increment_task_count(user_id)
        else:
            print(f"  Failed to create task: {action['title']}")

    def _increment_task_count(self, user_id):
        """Increment tasks_this_month for usage metering"""
        try:
            current_month_str = _now_local().strftime('%Y-%m')
            current_month_date = _now_local().strftime('%Y-%m-01')
            # Fetch current counter
            result = self.tm.supabase.table('users') \
                .select('tasks_this_month, tasks_month_reset') \
                .eq('id', user_id).execute()

            if result.data:
                row = result.data[0]
                count = row.get('tasks_this_month') or 0
                reset_month = str(row.get('tasks_month_reset') or '')[:7]

                if reset_month != current_month_str:
                    # New month — reset counter
                    count = 0

                self.tm.supabase.table('users').update({
                    'tasks_this_month': count + 1,
                    'tasks_month_reset': current_month_date,
                }).eq('id', user_id).execute()
        except Exception as e:
            print(f"  Warning: Could not update task count: {e}")

    def _send_task_confirmation(self, user_email, user_name, task):
        """Send confirmation email when worker auto-creates a task from email"""
        try:
            from email_utils import send_email

            task_id = task['id']
            task_title = task.get('title', 'Untitled')
            due_date = task.get('due_date', '')
            due_time = task.get('due_time', '')

            action_base = os.getenv('TASK_ACTION_URL', 'https://www.jottask.app/action')
            complete_url = f"{action_base}?action=complete&task_id={task_id}"
            delay_1hour_url = f"{action_base}?action=delay_1hour&task_id={task_id}"
            delay_1day_url = f"{action_base}?action=delay_1day&task_id={task_id}"

            greeting = f"Hi {user_name}," if user_name else "Hi,"
            due_display = f"{due_date} at {due_time[:5]}" if due_time else due_date

            html = f"""
            <html>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%); padding: 24px; border-radius: 12px 12px 0 0;">
                    <h1 style="color: white; margin: 0; font-size: 24px;">Task Created</h1>
                </div>
                <div style="background: #f9fafb; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
                    <p style="color: #374151;">{greeting}</p>
                    <p style="color: #374151;">A new task was created from your forwarded email:</p>
                    <div style="background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin: 16px 0;">
                        <a href="https://www.jottask.app/tasks/{task_id}/edit" style="color: #111827; text-decoration: none;">
                            <h3 style="margin: 0 0 8px 0; color: #111827;">{task_title}</h3>
                        </a>
                        <p style="margin: 0; color: #6b7280; font-size: 14px;">Due: {due_display}</p>
                    </div>
                    <div style="margin-top: 16px; text-align: center;">
                        <a href="{complete_url}" style="display: inline-block; background: #10B981; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">Complete</a>
                        <a href="{delay_1hour_url}" style="display: inline-block; background: #6b7280; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">+1 Hour</a>
                        <a href="{delay_1day_url}" style="display: inline-block; background: #6b7280; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">+1 Day</a>
                    </div>
                    <p style="color: #6b7280; font-size: 13px; margin-top: 16px;">You'll get a reminder before this task is due.</p>
                </div>
                <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 24px;">
                    Jottask - AI-Powered Task Management
                </p>
            </body>
            </html>
            """

            success, error = send_email(
                user_email,
                f"Task Created: {task_title}",
                html,
                category='confirmation',
                task_id=task_id,
            )

            if success:
                print(f"  [AUTO] Confirmation email sent to {user_email}")
            else:
                print(f"  [AUTO] Confirmation email failed: {error}")

        except Exception as e:
            print(f"  Warning: Could not send confirmation email: {e}")

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
            print("[WARNING] send_approval_email called without user_context — skipping")
            return

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

        # Send via email_utils (retries + monitoring)
        from email_utils import send_email
        subject = f"Jottask Approval: {' '.join(email_subject.split())}"
        success, error = send_email(
            recipient_email, subject, email_html,
            category='approval',
            user_id=user_context.user_id if user_context else None,
        )
        if success:
            print(f"  Approval email sent to {recipient_email} for {len(actions)} action(s)")
        else:
            print(f"  Error sending approval email: {error}")

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
    from saas_scheduler import check_and_send_reminders, check_and_send_dsw_reminders, get_users_needing_summary, send_daily_summary
    from monitoring import log_heartbeat, log_error, send_self_alert, cleanup_old_events, check_reminder_health, check_and_send_canary, check_email_processing_health, send_daily_health_digest

    processor = AIEmailProcessor()
    poll_interval = int(os.getenv('POLL_INTERVAL_SECONDS', '60'))
    print(f"Starting worker (email processor + scheduler) every {poll_interval}s...")
    print(f"  Email processing: every cycle")
    print(f"  Reminders + daily summaries: every cycle")
    print(f"  Monitoring: heartbeat every cycle, cleanup daily")

    tick = 0
    consecutive_failures = 0
    last_cleanup_date = None
    last_audit_time = None

    while True:
        tick += 1
        tick_errors = 0
        emails_processed = 0
        reminders_sent = 0
        summaries_sent = 0

        try:
            # 1. Process emails
            processor.process_all_connections()
        except Exception as e:
            print(f"Error in email processing: {e}")
            log_error('email_processing', e, category='email')
            tick_errors += 1

        try:
            # 2. Check and send task reminders
            reminders_sent = check_and_send_reminders() or 0
        except Exception as e:
            print(f"Error in reminders: {e}")
            log_error('reminders', e, category='reminder')
            tick_errors += 1

        try:
            # 2a. Check and send DSW Solar lead reminders
            check_and_send_dsw_reminders()
        except Exception as e:
            print(f"Error in DSW reminders: {e}")
            log_error('dsw_reminders', e, category='reminder')

        try:
            # 2b. Functional reminder health check
            if not check_reminder_health():
                print("WARNING: Reminder health check detected a problem")
                tick_errors += 1
        except Exception as e:
            print(f"Error in reminder health check: {e}")
            log_error('reminder_health_check', e, category='reminder')

        try:
            # 2c. Canary email delivery check (7 AM + 5 PM AEST)
            canary_result = check_and_send_canary()
            if canary_result == 'sent':
                print("Canary email sent — email delivery verified")
            elif canary_result == 'failed':
                print("WARNING: Canary email FAILED — email delivery may be broken")
                send_self_alert(
                    "Canary email failed — outbound email may be down",
                    "check_and_send_canary() returned 'failed'. Resend API may be unreachable or misconfigured. "
                    "Check RESEND_API_KEY and Railway logs."
                )
                tick_errors += 1
        except Exception as e:
            print(f"Error in canary check: {e}")
            log_error('canary_check', e, category='canary')
            tick_errors += 1

        try:
            # 2e. Daily health digest (8 AM AEST)
            digest_result = send_daily_health_digest()
            if digest_result == 'sent':
                print("Daily health digest sent")
            elif digest_result == 'failed':
                print("WARNING: Failed to send daily health digest")
                tick_errors += 1
        except Exception as e:
            print(f"Error in health digest: {e}")
            log_error('health_digest', e, category='system')

        try:
            # 2d. Email processing audit (every 30 minutes)
            now_utc = datetime.now(pytz.UTC)
            if last_audit_time is None or (now_utc - last_audit_time).total_seconds() >= 1800:
                audit_result = check_email_processing_health()
                if audit_result == 'warning':
                    print("WARNING: Email processing audit detected silent failures")
                    tick_errors += 1
                last_audit_time = now_utc
        except Exception as e:
            print(f"Error in email processing audit: {e}")
            log_error('email_audit', e, category='audit')

        try:
            # 3. Check and send daily summaries
            users = get_users_needing_summary()
            if users:
                print(f"📬 Found {len(users)} user(s) needing daily summary")
                for user in users:
                    send_daily_summary(user)
                    summaries_sent += 1
        except Exception as e:
            print(f"Error in daily summaries: {e}")
            log_error('daily_summaries', e, category='summary')
            tick_errors += 1

        # Track consecutive failures for self-alerting
        if tick_errors > 0:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                send_self_alert(
                    f"Worker has {consecutive_failures} consecutive failed ticks",
                    f"Tick #{tick}: {tick_errors} error(s) this cycle. "
                    f"Check Railway logs for details."
                )
        else:
            consecutive_failures = 0

        # Log heartbeat every cycle
        log_heartbeat(tick, emails_processed=emails_processed, reminders_sent=reminders_sent,
                       summaries_sent=summaries_sent, errors=tick_errors)

        # Daily cleanup of old monitoring events
        from datetime import date as _date
        today = _date.today()
        if last_cleanup_date != today:
            cleanup_old_events(days=30)
            last_cleanup_date = today

        print(f"Sleeping {poll_interval}s until next check... (tick #{tick})")
        time.sleep(poll_interval)


def handle_dsw_forward(subject, body_text, sender_email):
    """Handle a forwarded or unstructured lead email from rob.l@directsolarwholesaler.com.au.

    Triggered when subject contains FW:/Fwd: OR body contains an Australian phone number.
    Uses Claude Haiku to extract contact details (name, phone, email, address, referred_by,
    notes, lead_source) from whatever is in the body — no fixed format required.

    Steps:
      1. Claude extracts contact details
      2. Create/update Pipereply contact
      3. Call dsw_lead_poller.process() → OpenSolar, Mac contact, CRM note, email, task
      4. Patch the created task's due time to 30 min from now (urgent)
    """
    import re, os, requests, json, importlib.util as ilu
    from datetime import datetime, timedelta
    from anthropic import Anthropic

    sender_lower = (sender_email or '').lower()
    if 'rob.l@directsolarwholesaler.com.au' not in sender_lower:
        return False

    subject_lower = (subject or '').lower()
    body_lower = (body_text or '').lower()

    is_forward = any(subject_lower.startswith(p) for p in ('fw:', 'fwd:'))
    has_au_phone = bool(re.search(r'\b0[0-9]{9}\b', body_text or ''))

    # Skip known system/non-lead subject prefixes (new lead: and re: new lead: are
    # already handled before this function is called, so only skip DSW system ones)
    SKIP_PREFIXES = ('dsw reminder:', 'dsw:')
    if any(subject_lower.startswith(p) for p in SKIP_PREFIXES):
        print(f"[DSW FORWARD] SKIP — system subject prefix. Subject: '{subject}'")
        return False

    # Also fire for unstructured lead notes that don't have FW: or a phone number,
    # e.g. "Joe hill referral aw his neighbour to send bill" or
    # "Aw meeting energy retailer - Future X with Jack"
    LEAD_KEYWORDS = (
        'solar', 'battery', 'panel', 'inverter', 'energy', 'retailer',
        'power', 'kwh', 'system', 'quote', 'install', 'referral', 'lead',
        'customer', 'client', 'neighbour', 'neighbor', 'bill', 'meeting',
    )
    has_lead_keywords = any(kw in body_lower or kw in subject_lower for kw in LEAD_KEYWORDS)

    if not is_forward and not has_au_phone and not has_lead_keywords:
        print(f"[DSW FORWARD] SKIP — no FW:/Fwd: prefix, no AU phone, no solar/lead keywords. Subject: '{subject}'")
        return False

    trigger_reason = []
    if is_forward:        trigger_reason.append('FW:/Fwd: prefix')
    if has_au_phone:      trigger_reason.append('AU phone in body')
    if has_lead_keywords: trigger_reason.append('solar/lead keywords')
    print(f"[DSW FORWARD] Triggered by: {', '.join(trigger_reason)}. Subject: '{subject}'")

    # ── 1. Claude Haiku extracts contact details ───────────────────────────
    claude = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    extraction_prompt = f"""Extract contact details from this email and return JSON only. No explanation.

Subject: {subject}
Body:
{(body_text or '')[:3000]}

Return exactly this JSON:
{{
  "name": "full name or null",
  "phone": "digits only (e.g. 0412345678) or null",
  "email": "email address or null",
  "address": "full street address with suburb, state, postcode or null",
  "referred_by": "person/source that referred them or null",
  "notes": "solar/battery interest, system size, urgency, bill amount, anything relevant or null",
  "lead_source": "referral|website|sign|social_media|facebook|google|other or null"
}}

Rules:
- Extract real data only, never invent anything
- phone: Australian format — starts with 04 (mobile) or 02/03/07/08 (landline), 10 digits
- Return null for any field not found in the email"""

    try:
        resp = claude.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=400,
            messages=[{'role': 'user', 'content': extraction_prompt}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
        extracted = json.loads(raw)
    except Exception as e:
        print(f"[DSW FORWARD] Claude extraction failed: {e}")
        return False

    name   = (extracted.get('name') or '').strip()
    phone  = (extracted.get('phone') or '').strip()
    email_addr = (extracted.get('email') or '').strip()
    address    = (extracted.get('address') or '').strip()
    referred_by = (extracted.get('referred_by') or '').strip()
    notes       = (extracted.get('notes') or '').strip()
    lead_source = (extracted.get('lead_source') or 'referral').strip()

    # Never skip — even with no name/phone, create a task so the lead isn't lost.
    # Fall back to subject line as the name so the task is identifiable.
    if not name:
        # Strip FW:/Fwd: prefix and use the remainder as a best-guess name
        name = re.sub(r'^(fw|fwd)\s*:\s*', '', subject or 'Unknown Lead', flags=re.IGNORECASE).strip() or 'Unknown Lead'
        print(f"[DSW FORWARD] No name extracted — using subject as fallback: '{name}'")

    print(f"[DSW FORWARD] Extracted: {name} | {phone or '—'} | {address[:40] if address else '—'}")

    TOKEN       = os.getenv('PIPEREPLY_TOKEN')
    LOCATION_ID = os.getenv('PIPEREPLY_LOCATION_ID')
    BASE = 'https://services.leadconnectorhq.com'
    H = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json', 'Version': '2021-07-28'}

    # ── 2. Create/update Pipereply contact ────────────────────────────────
    parts = name.strip().split() if name else []
    first_name = parts[0] if parts else (name or 'Unknown')
    last_name  = ' '.join(parts[1:]) if len(parts) > 1 else ''

    # Build contact notes for Claude summarisation in dsw.process()
    note_parts = []
    if referred_by:   note_parts.append(f'Referred by: {referred_by}')
    if lead_source:   note_parts.append(f'Source: {lead_source}')
    if notes:         note_parts.append(notes)
    contact_notes = '\n'.join(note_parts)

    cid = None

    # ── Phone-first dedup search ───────────────────────────────────────────
    if phone:
        r_phone = requests.get(f'{BASE}/contacts/', headers=H,
                               params={'locationId': LOCATION_ID, 'query': phone, 'limit': 5},
                               timeout=10)
        phone_contacts = r_phone.json().get('contacts', []) if r_phone.ok else []
        phone_match = next(
            (c for c in phone_contacts
             if re.sub(r'\D', '', c.get('phone', '')) == re.sub(r'\D', '', phone)),
            None,
        )
        if phone_match:
            cid = phone_match['id']
            print(f"[DSW FORWARD] Found existing contact by phone: "
                  f"{phone_match.get('contactName','?')} ({cid[:8]})")

    # ── Name fallback search (case-insensitive) ───────────────────────────
    if not cid and name:
        r_name = requests.get(f'{BASE}/contacts/', headers=H,
                              params={'locationId': LOCATION_ID, 'query': name, 'limit': 3},
                              timeout=10)
        name_contacts = r_name.json().get('contacts', []) if r_name.ok else []
        name_match = next(
            (c for c in name_contacts
             if name.lower() in (c.get('contactName') or '').lower()
             or (c.get('contactName') or '').lower() in name.lower()),
            None,
        )
        if name_match:
            cid = name_match['id']
            print(f"[DSW FORWARD] Found existing contact by name: "
                  f"{name_match.get('contactName','?')} ({cid[:8]})")

    if cid:
        # Update existing contact with any new details
        update_payload = {}
        if phone:         update_payload['phone']    = phone
        if email_addr:    update_payload['email']    = email_addr
        if address:       update_payload['address1'] = address
        if contact_notes: update_payload['notes']    = contact_notes
        if update_payload:
            requests.put(f'{BASE}/contacts/{cid}', headers=H, json=update_payload, timeout=10)
        print(f"[DSW FORWARD] Updated existing contact: {name} ({cid[:8]})")
    else:
        # No match — create new contact
        contact_payload = {
            'locationId': LOCATION_ID,
            'firstName': first_name,
            'lastName':  last_name,
            'phone':     phone,
            'email':     email_addr,
            'address1':  address,
            'tags':      [lead_source if lead_source else 'referral'],
            'source':    lead_source.replace('_', ' ').title() if lead_source else 'Referral',
            'notes':     contact_notes,
        }
        r_create = requests.post(f'{BASE}/contacts/', headers=H, json=contact_payload, timeout=10)
        if r_create.ok:
            created = r_create.json()
            cid = (created.get('contact') or created).get('id', '')
            print(f"[DSW FORWARD] Created new contact: {name} ({(cid or '?')[:8]})")
        else:
            print(f"[DSW FORWARD] Contact creation failed: {r_create.status_code} {r_create.text[:120]}")

    # ── 3. Save CRM note with all extracted info ───────────────────────────
    if cid:
        crm_note_lines = [f'Source: {lead_source.replace("_"," ").title()} — {datetime.now().strftime("%d %b %Y")}']
        if referred_by:  crm_note_lines.append(f'Referred by: {referred_by}')
        if phone:        crm_note_lines.append(f'Phone: {phone}')
        if email_addr:   crm_note_lines.append(f'Email: {email_addr}')
        if address:      crm_note_lines.append(f'Address: {address}')
        if notes:        crm_note_lines.append(f'\nNotes:\n{notes}')
        requests.post(f'{BASE}/contacts/{cid}/notes', headers=H,
                      json={'body': '\n'.join(crm_note_lines)}, timeout=10)

    if not cid:
        print(f"[DSW FORWARD] No contact ID — cannot proceed")
        return False

    # ── 4+5+6+7. dsw.process() → OpenSolar, Mac contact, task, email ──────
    # process() creates task with due=tomorrow 09:00; we patch it after.
    try:
        _spec = ilu.spec_from_file_location('dsw_lead_poller',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dsw_lead_poller.py'))
        dsw = ilu.module_from_spec(_spec)
        _spec.loader.exec_module(dsw)

        minimal_contact = {
            'id': cid, 'contactName': name, 'phone': phone,
            'email': email_addr, 'address1': address,
            'tags': [lead_source if lead_source else 'referral'],
        }
        dsw.process(minimal_contact, task_id=None, lead_status=None)

    except Exception as e:
        print(f"[DSW FORWARD] dsw.process error: {e}")
        return True  # Contact + note already created, still return True

    # ── Patch task due time to 30 minutes from now ─────────────────────────
    try:
        from supabase import create_client
        sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
        due_dt = datetime.now() + timedelta(minutes=30)
        task_search = sb.table('tasks')\
            .select('id')\
            .eq('category', 'DSW Solar')\
            .eq('status', 'pending')\
            .ilike('client_name', f'%{name}%')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
        if task_search.data:
            tid = task_search.data[0]['id']
            sb.table('tasks').update({
                'due_date': due_dt.strftime('%Y-%m-%d'),
                'due_time': due_dt.strftime('%H:%M:%S'),
            }).eq('id', tid).execute()
            print(f"[DSW FORWARD] Task due time patched to {due_dt.strftime('%H:%M')} ({tid[:8]})")
    except Exception as e:
        print(f"[DSW FORWARD] Task due-time patch error: {e}")

    return True


def handle_dsw_new_lead(subject, body_text, sender_email):
    """Handle a self-generated lead email from rob.l@directsolarwholesaler.com.au.

    Triggered when subject starts with "New Lead:" (not a reply).
    Parses Name/Phone/Email/Address/Referred by/Notes from body, then:
      a) Creates/updates Pipereply contact
      b) Saves CRM note with referral source + notes
      c) Calls dsw_lead_poller.process() → creates OpenSolar project, Mac contact,
         Jottask task (due tomorrow 9am), and sends full DSW lead email.
    """
    import re, os, requests, importlib.util as ilu
    from datetime import datetime, timedelta

    sender_lower = (sender_email or '').lower()
    if 'rob.l@directsolarwholesaler.com.au' not in sender_lower:
        return False
    subject_lower = (subject or '').lower()
    # Must start with "new lead:" but must NOT be a reply (re:)
    if not subject_lower.startswith('new lead:'):
        return False

    print(f"[DSW NEW LEAD] Processing self-generated lead: {subject}")

    def parse_field(text, field_name):
        m = re.search(rf'^{re.escape(field_name)}\s*:?\s*(.+)$', text, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ''

    body = body_text or ''
    name       = parse_field(body, 'Name')
    phone      = parse_field(body, 'Phone')
    email_addr = parse_field(body, 'Email')
    address    = parse_field(body, 'Address')
    referred_by = parse_field(body, 'Referred by')
    notes      = parse_field(body, 'Notes')

    if not name:
        print("[DSW NEW LEAD] No Name: field found in body — skipping")
        return False

    TOKEN       = os.getenv('PIPEREPLY_TOKEN')
    LOCATION_ID = os.getenv('PIPEREPLY_LOCATION_ID')
    BASE = 'https://services.leadconnectorhq.com'
    H = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json', 'Version': '2021-07-28'}

    # a) Create/update Pipereply contact
    parts = name.strip().split()
    first_name = parts[0] if parts else name
    last_name  = ' '.join(parts[1:]) if len(parts) > 1 else ''

    # Search for existing contact
    r_search = requests.get(f'{BASE}/contacts/', headers=H,
                            params={'locationId': LOCATION_ID, 'query': name, 'limit': 3},
                            timeout=10)
    contacts_found = r_search.json().get('contacts', []) if r_search.ok else []
    match = next((c for c in contacts_found
                  if name.lower() in (c.get('contactName') or '').lower()), None)

    if match:
        cid = match['id']
        update_payload = {}
        if phone:      update_payload['phone']    = phone
        if email_addr: update_payload['email']    = email_addr
        if address:    update_payload['address1'] = address
        if update_payload:
            requests.put(f'{BASE}/contacts/{cid}', headers=H, json=update_payload, timeout=10)
        print(f"[DSW NEW LEAD] Updated existing contact: {name} ({cid[:8]})")
    else:
        contact_payload = {
            'locationId': LOCATION_ID,
            'firstName': first_name,
            'lastName':  last_name,
            'phone':     phone or '',
            'email':     email_addr or '',
            'address1':  address or '',
            'tags':      ['referral'],
            'source':    'Referral',
        }
        if referred_by or notes:
            note_parts = []
            if referred_by: note_parts.append(f'Referred by: {referred_by}')
            if notes:        note_parts.append(notes)
            contact_payload['notes'] = '\n'.join(note_parts)
        r_create = requests.post(f'{BASE}/contacts/', headers=H,
                                 json=contact_payload, timeout=10)
        if r_create.ok:
            created = r_create.json()
            cid = (created.get('contact') or created).get('id', '')
            print(f"[DSW NEW LEAD] Created contact: {name} ({(cid or '?')[:8]})")
        else:
            print(f"[DSW NEW LEAD] Contact creation failed: {r_create.status_code} {r_create.text[:120]}")
            cid = None

    # b) Save CRM note with referral source + notes
    if cid:
        note_lines = [f'Source: Referral — {datetime.now().strftime("%d %b %Y")}']
        if referred_by: note_lines.append(f'Referred by: {referred_by}')
        if notes:       note_lines.append(f'Notes: {notes}')
        requests.post(f'{BASE}/contacts/{cid}/notes', headers=H,
                      json={'body': '\n'.join(note_lines)}, timeout=10)

    if not cid:
        print("[DSW NEW LEAD] No contact ID — cannot proceed")
        return False

    # c+d) Run dsw_lead_poller.process() — creates OpenSolar, Mac contact, task, email
    try:
        _spec = ilu.spec_from_file_location('dsw_lead_poller',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dsw_lead_poller.py'))
        dsw = ilu.module_from_spec(_spec)
        _spec.loader.exec_module(dsw)
        minimal_contact = {'id': cid, 'contactName': name, 'phone': phone,
                           'email': email_addr, 'address1': address, 'tags': ['referral']}
        dsw.process(minimal_contact, task_id=None, lead_status=None)
    except Exception as e:
        print(f"[DSW NEW LEAD] dsw.process error: {e}")

    return True


def handle_dsw_reply(subject, body_text, sender_email):
    import re, os, requests
    from datetime import datetime
    if 'directsolarwholesaler' not in (sender_email or '').lower():
        return False
    m = re.search(r'New Lead:\s+([A-Za-z\s]+?)\s*-\s*Call ASAP', subject or '', re.IGNORECASE)
    if not m:
        return False
    name = m.group(1).strip()
    print(f"[DSW REPLY] Notes update for: {name}")
    clean_lines = []
    for line in (body_text or '').split('\n'):
        if line.strip().startswith('>') or line.strip().startswith('On ') or line.strip() == '---':
            break
        clean_lines.append(line)
    notes_text = '\n'.join(clean_lines).strip()
    if not notes_text:
        print("[DSW REPLY] No text found"); return False
    TOKEN = os.getenv('PIPEREPLY_TOKEN')
    LOCATION_ID = os.getenv('PIPEREPLY_LOCATION_ID')
    BASE = 'https://services.leadconnectorhq.com'
    H = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json', 'Version': '2021-07-28'}
    r = requests.get(f'{BASE}/contacts/', headers=H, params={'locationId': LOCATION_ID, 'query': name, 'limit': 3})
    contacts = r.json().get('contacts', [])
    match = next((c for c in contacts if name.lower() in (c.get('contactName') or '').lower()), None)
    if not match and contacts: match = contacts[0]
    if not match: print(f"[DSW REPLY] Not found: {name}"); return False
    cid = match['id']
    r_notes = requests.get(f'{BASE}/contacts/{cid}/notes', headers=H)
    existing_id = existing_body = None
    if r_notes.ok:
        for note in (r_notes.json().get('notes') or []):
            if 'OpenSolar' in (note.get('body') or '') or 'CUSTOMER REQUIREMENTS' in (note.get('body') or ''):
                existing_id = note.get('id'); existing_body = note.get('body', ''); break
    ts = datetime.now().strftime('%d %b %Y %I:%M %p')
    new_body = (existing_body or '') + f'\n\n--- Call Notes ({ts}) ---\n{notes_text}'
    if existing_id:
        r2 = requests.put(f'{BASE}/contacts/{cid}/notes/{existing_id}', headers=H, json={'body': new_body})
    else:
        r2 = requests.post(f'{BASE}/contacts/{cid}/notes', headers=H, json={'body': new_body})
    print(f"[DSW REPLY] CRM updated: {r2.status_code}")
    try:
        from supabase import create_client
        sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
        tasks = sb.table('tasks').select('id,description').eq('category','DSW Solar').ilike('title',f'%{name}%').eq('status','pending').order('created_at',desc=True).limit(1).execute()
        if tasks.data:
            tid = tasks.data[0]['id']
            new_desc = (tasks.data[0].get('description') or '') + f'\n\n--- Call Notes ({ts}) ---\n{notes_text}'
            sb.table('tasks').update({'description': new_desc}).eq('id', tid).execute()
            print(f"[DSW REPLY] Task updated: {tid[:8]}")
    except Exception as e:
        print(f"[DSW REPLY] Task error: {e}")
    return True
