"""
Squad Email Processor
Polls squadflowquote@gmail.com via Gmail IMAP, parses with Claude API,
stores results in squad_email_inbox Supabase table.

Env vars required:
    SQUAD_GMAIL_ADDRESS       — Gmail address to poll
    SQUAD_GMAIL_APP_PASSWORD  — Gmail App Password (not account password)
    ANTHROPIC_API_KEY         — Claude API key
    SUPABASE_URL / SUPABASE_KEY
"""

import imaplib
import email
import json
import os
import re
import uuid
import hashlib
from datetime import datetime
from email.header import decode_header

import pytz
from anthropic import Anthropic
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

AEST = pytz.timezone('Australia/Brisbane')
GMAIL_IMAP_SERVER = 'imap.gmail.com'
GMAIL_IMAP_PORT = 993

# ── Lazy Supabase init ──────────────────────────────────────────────────────

_supabase = None

def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
    return _supabase


# ── Email parsing helpers ───────────────────────────────────────────────────

def _decode_header_value(value: str) -> str:
    if not value:
        return ''
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or 'utf-8', errors='replace'))
        else:
            parts.append(str(chunk))
    return ''.join(parts)


def _get_email_body(msg) -> str:
    """Extract plaintext body, falling back to stripped HTML."""
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain':
                try:
                    body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                    break
                except Exception:
                    pass
            elif ct == 'text/html' and not body:
                try:
                    html = part.get_payload(decode=True).decode('utf-8', errors='replace')
                    body = re.sub(r'<[^>]+>', ' ', html)
                    body = re.sub(r'\s+', ' ', body).strip()
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode('utf-8', errors='replace')
        except Exception:
            pass
    return body.strip()


def _email_hash(subject: str, sender: str, date_str: str) -> str:
    content = f"{subject}:{sender}:{date_str}"
    return hashlib.md5(content.encode()).hexdigest()


def _already_processed(supabase: Client, email_hash: str) -> bool:
    result = supabase.table('squad_email_inbox').select('id').eq('email_hash', email_hash).execute()
    return len(result.data or []) > 0


# ── Claude parsing ──────────────────────────────────────────────────────────

def _classify_email(anthropic: Anthropic, subject: str, body: str) -> str:
    """Return 'club_update' | 'parent_message' | 'fixture_list'."""
    from squad_prompts import classify_email
    return classify_email(anthropic, subject, body)


def _parse_email(anthropic: Anthropic, email_type: str, subject: str, body: str) -> dict:
    from squad_prompts import parse_club_update, parse_parent_message, parse_fixture_list
    if email_type == 'club_update':
        return parse_club_update(anthropic, subject, body)
    elif email_type == 'parent_message':
        return parse_parent_message(anthropic, subject, body)
    elif email_type == 'fixture_list':
        return parse_fixture_list(anthropic, subject, body)
    return {}


# ── Main poller ─────────────────────────────────────────────────────────────

def poll_squad_inbox() -> int:
    """
    Poll squadflowquote@gmail.com for unseen emails, classify and parse each
    with Claude, then store results in squad_email_inbox.

    Returns the number of emails successfully processed.
    """
    gmail_address = os.getenv('SQUAD_GMAIL_ADDRESS')
    gmail_password = os.getenv('SQUAD_GMAIL_APP_PASSWORD')

    if not gmail_address or not gmail_password:
        print("⚠️  Squad: SQUAD_GMAIL_ADDRESS or SQUAD_GMAIL_APP_PASSWORD not set — skipping")
        return 0

    print(f"\n📬 Squad: Polling {gmail_address}...")

    supabase = _get_supabase()
    anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    processed = 0
    mail = None

    try:
        mail = imaplib.IMAP4_SSL(GMAIL_IMAP_SERVER, GMAIL_IMAP_PORT)
        mail.login(gmail_address, gmail_password)
        mail.select('INBOX')

        status, messages = mail.search(None, 'UNSEEN')
        if status != 'OK' or not messages[0]:
            print("   Squad: No unseen messages")
            return 0

        message_ids = messages[0].split()
        print(f"   Squad: {len(message_ids)} unseen email(s)")

        for msg_num in message_ids:
            try:
                status, msg_data = mail.fetch(msg_num, '(RFC822)')
                if status != 'OK':
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject  = _decode_header_value(msg.get('Subject', ''))
                sender   = _decode_header_value(msg.get('From', ''))
                date_str = msg.get('Date', '')
                body     = _get_email_body(msg)

                # Dedup
                h = _email_hash(subject, sender, date_str)
                if _already_processed(supabase, h):
                    print(f"   Squad: Skip (already processed) — '{subject[:40]}'")
                    mail.store(msg_num, '+FLAGS', '\\Seen')
                    continue

                print(f"   Squad: Parsing '{subject[:50]}' from {sender[:40]}")

                # Classify
                email_type = _classify_email(anthropic, subject, body)
                print(f"   Squad: → type={email_type}")

                # Parse
                parsed_data = _parse_email(anthropic, email_type, subject, body)

                # Store
                record = {
                    'id':            str(uuid.uuid4()),
                    'email_from':    sender,
                    'email_subject': subject,
                    'email_body':    body[:10000],
                    'email_date':    date_str,
                    'email_hash':    h,
                    'email_type':    email_type,
                    'parsed_data':   parsed_data,
                    'status':        'pending',
                    'created_at':    datetime.now(pytz.UTC).isoformat(),
                }
                supabase.table('squad_email_inbox').insert(record).execute()

                # Mark read
                mail.store(msg_num, '+FLAGS', '\\Seen')
                processed += 1
                print(f"   Squad: ✅ Stored — {email_type}")

            except Exception as e:
                print(f"   Squad: ❌ Error on message {msg_num}: {e}")
                import traceback
                traceback.print_exc()
                continue

    except imaplib.IMAP4.error as e:
        print(f"   Squad: ❌ IMAP error: {e}")
    except Exception as e:
        print(f"   Squad: ❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass

    print(f"   Squad: Done — {processed} email(s) processed")
    return processed


if __name__ == '__main__':
    load_dotenv()
    poll_squad_inbox()
