"""
Mobile Message SMS gateway helpers (mobilemessage.com.au).

Two-way SMS for Jottask:
- send_sms(): outbound via the Mobile Message REST API. Used by
  saas_email_processor when Rob emails an "SMS: <number>" command.
- normalize_au_mobile(): coerce 04xx / +614xx / 614xx into E.164 (+614xxxxxxxx).

Inbound SMS is handled by the https://www.jottask.app/webhooks/mobilemessage/sms-inbound
endpoint in dashboard.py, which forwards each received message on to Rob as an email.

API reference: https://www.mobilemessage.com.au/api-documentation
  POST https://api.mobilemessage.com.au/v1/messages
  Auth: HTTP Basic (username:api_key)
  Body: {"messages": [{"to", "message", "sender", "custom_ref"}]}
  Per-message result status is "success" or "error".
"""

import os
import re
import requests

MOBILEMESSAGE_SEND_URL = 'https://api.mobilemessage.com.au/v1/messages'


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


def send_sms(to, message, from_number=None):
    """Send an SMS via Mobile Message. Returns (success: bool, detail: str).

    The sender ID resolves to from_number, then MOBILEMESSAGE_FROM_NUMBER. This
    is the dedicated number assigned after the first credit purchase, so until
    it's set, sends fail with a clear reason.

    Never raises — failures are returned as (False, reason) so callers can log
    and carry on.
    """
    username = os.getenv('MOBILEMESSAGE_USERNAME')
    api_key = os.getenv('MOBILEMESSAGE_API_KEY')
    if not username or not api_key:
        return False, 'MOBILEMESSAGE_USERNAME / MOBILEMESSAGE_API_KEY not configured'

    sender = from_number or os.getenv('MOBILEMESSAGE_FROM_NUMBER')
    if not sender:
        return False, 'MOBILEMESSAGE_FROM_NUMBER not configured (dedicated number assigned after first credit purchase)'

    # Mobile Message accepts AU local or international; send E.164 when we can
    # recognise an AU mobile, otherwise pass the raw value through.
    to_clean = normalize_au_mobile(to) or str(to).strip()

    payload = {
        'messages': [
            {
                'to': to_clean,
                'message': message,
                'sender': sender,
                'custom_ref': 'jottask',
            }
        ]
    }

    try:
        resp = requests.post(
            MOBILEMESSAGE_SEND_URL,
            json=payload,
            auth=(username, api_key),
            timeout=30,
        )
    except Exception as e:
        return False, f'request failed: {e}'

    if resp.status_code == 200:
        # HTTP 200 means the request was processed; success/failure is per
        # message in the results array.
        try:
            result = (resp.json().get('results') or [{}])[0]
            if result.get('status') == 'success':
                return True, result.get('message_id') or 'success'
            return False, f"rejected: status={result.get('status')!r} {resp.text[:200]}"
        except Exception:
            return True, 'sent'
    return False, f'HTTP {resp.status_code}: {resp.text[:300]}'
