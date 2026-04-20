"""
Jottask Email Utilities
Shared email sending via Resend API with retry logic and monitoring
"""

import os
import time
import resend

FROM_EMAIL = os.getenv('FROM_EMAIL', 'jottask@flowquote.ai')

MAX_RETRIES = 2
BACKOFF_BASE = 1  # seconds

# Outbound routing: every email Rob should receive lands on his DSW inbox,
# never on his CCE inbox. Applied at the send boundary so individual callers
# don't all need to know about it.
_RECIPIENT_REWRITES = {
    'rob@cloudcleanenergy.com.au': 'rob.l@directsolarwholesaler.com.au',
}


def _rewrite_recipient(to_email):
    if not to_email:
        return to_email
    return _RECIPIENT_REWRITES.get(to_email.strip().lower(), to_email)


def send_email(to_email, subject, html_body, category=None, user_id=None, task_id=None):
    """
    Send an email via Resend API with retry logic.
    Returns: (success: bool, error: str or None)

    Optional params for monitoring (backward compatible):
      category: 'reminder', 'summary', 'confirmation', 'approval', 'system'
      user_id: which user this email is for
      task_id: which task this email relates to
    """
    to_email = _rewrite_recipient(to_email)

    # Read API key at call time (not import time) so env vars are always fresh
    api_key = os.getenv('RESEND_API_KEY')
    if not api_key:
        print("RESEND_API_KEY not configured — cannot send email")
        _log_send(False, to_email, subject, category, user_id, task_id, "RESEND_API_KEY not configured")
        return False, "RESEND_API_KEY not configured"

    resend.api_key = api_key

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            params = {
                "from": f"Jottask <{FROM_EMAIL}>",
                "to": [to_email],
                "subject": subject,
                "html": html_body,
            }
            resend.Emails.send(params)
            if attempt > 0:
                print(f"Email sent to {to_email} (retry #{attempt}): {subject}")
            else:
                print(f"Email sent to {to_email}: {subject}")
            _log_send(True, to_email, subject, category, user_id, task_id)
            return True, None
        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES:
                wait = BACKOFF_BASE * (2 ** attempt)  # 1s, 2s
                print(f"Email send attempt {attempt + 1} failed ({last_error}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"Failed to send email to {to_email} after {MAX_RETRIES + 1} attempts: {last_error}")

    _log_send(False, to_email, subject, category, user_id, task_id, last_error)
    return False, last_error


def _log_send(success, to_email, subject, category, user_id, task_id, error=None):
    """Log email send to monitoring (fire-and-forget)."""
    try:
        from monitoring import log_email_send
        log_email_send(success, to_email, subject, category=category, user_id=user_id, task_id=task_id, error=error)
    except Exception:
        pass  # Never let monitoring break email sending
