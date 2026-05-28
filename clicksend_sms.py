"""
ClickSend SMS gateway helpers.

Two-way SMS for Jottask:
- send_sms(): outbound via the ClickSend REST API. Used by saas_email_processor
  when Rob emails an "SMS: <number>" command.
- normalize_au_mobile(): coerce 04xx / +614xx / 614xx into E.164 (+614xxxxxxxx),
  which is what the ClickSend API expects.

Inbound SMS is handled by the /webhooks/clicksend/sms-inbound endpoint in
dashboard.py, which forwards each received message on to Rob as an email.
"""

import os
import re
import requests

CLICKSEND_SEND_URL = 'https://rest.clicksend.com/v3/sms/send'


def normalize_au_mobile(raw):
    """Normalize an Australian mobile number to E.164 (+614xxxxxxxx).

    Accepts forms like 0412345678, +61412345678, 61412345678, "0412 345 678".
    Returns the normalized string, or None if it doesn't look like an AU mobile.
    """
    if not raw:
        return None
    digits = re.sub(r'[^\d+]', '', str(raw).strip())
    if digits.startswith('+61'):
        rest = digits[3:]
    elif digits.startswith('61') and not digits.startswith('610'):
        rest = digits[2:]
    elif digits.startswith('0'):
        rest = digits[1:]
    else:
        rest = digits.lstrip('+')
    # AU mobile national number is 9 digits starting with 4
    if len(rest) == 9 and rest.startswith('4'):
        return '+61' + rest
    return None


def send_sms(to_number, message):
    """Send an SMS via ClickSend. Returns (success: bool, detail: str).

    Never raises — failures are returned as (False, reason) so callers can log
    and carry on.
    """
    username = os.getenv('CLICKSEND_USERNAME')
    api_key = os.getenv('CLICKSEND_API_KEY')
    if not username or not api_key:
        return False, 'CLICKSEND_USERNAME / CLICKSEND_API_KEY not configured'

    # ClickSend wants E.164; fall back to the raw value if it's not an AU mobile
    # (e.g. an already-international number) and let the API validate it.
    to = normalize_au_mobile(to_number) or str(to_number).strip()

    payload = {
        'messages': [
            {
                'source': 'jottask',
                'to': to,
                'body': message,
            }
        ]
    }

    try:
        resp = requests.post(
            CLICKSEND_SEND_URL,
            json=payload,
            auth=(username, api_key),
            timeout=30,
        )
    except Exception as e:
        return False, f'request failed: {e}'

    if resp.status_code in (200, 201):
        # ClickSend returns HTTP 200 with a per-message status string
        try:
            msg = resp.json().get('data', {}).get('messages', [{}])[0]
            return True, msg.get('status', 'SENT')
        except Exception:
            return True, 'sent'
    return False, f'HTTP {resp.status_code}: {resp.text[:300]}'
