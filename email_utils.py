"""
Jottask Email Utilities
Shared email sending via Resend API
"""

import os
import resend

RESEND_API_KEY = os.getenv('RESEND_API_KEY')
FROM_EMAIL = os.getenv('FROM_EMAIL', 'jottask@flowquote.ai')


def send_email(to_email, subject, html_body):
    """
    Send an email via Resend API.
    Returns: (success: bool, error: str or None)
    """
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not configured")
        return False, "RESEND_API_KEY not configured"

    resend.api_key = RESEND_API_KEY

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
