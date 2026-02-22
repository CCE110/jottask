"""
Jottask Email Utilities
Shared email sending via Resend API
"""

import os
import resend

FROM_EMAIL = os.getenv('FROM_EMAIL', 'jottask@flowquote.ai')


def send_email(to_email, subject, html_body):
    """
    Send an email via Resend API.
    Returns: (success: bool, error: str or None)
    """
    # Read API key at call time (not import time) so env vars are always fresh
    api_key = os.getenv('RESEND_API_KEY')
    if not api_key:
        print("RESEND_API_KEY not configured — cannot send email")
        return False, "RESEND_API_KEY not configured"

    resend.api_key = api_key

    try:
        params = {
            "from": f"Jottask <{FROM_EMAIL}>",
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        }
        resend.Emails.send(params)
        print(f"Email sent to {to_email}: {subject}")
        return True, None
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        return False, str(e)
