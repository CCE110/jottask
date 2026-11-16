"""
Signup-form abuse protection.

Pure functions (no I/O) so they are trivially testable. Used by the /signup
route in dashboard.py. Added after bots injected scam text/URLs into the
"full name" field using disposable email domains (merepost.com, fextemp.com).
"""

import re

# Hard-blocked disposable / temp-mail domains. Subdomains also match.
DISPOSABLE_DOMAINS = {
    'merepost.com', 'fextemp.com', 'mailinator.com', 'guerrillamail.com',
    'guerrillamail.net', 'guerrillamail.org', 'sharklasers.com', 'grr.la',
    '10minutemail.com', '10minutemail.net', 'temp-mail.org', 'tempmail.com',
    'tempmail.net', 'tempmailo.com', 'tempr.email', 'yopmail.com', 'yopmail.net',
    'trashmail.com', 'trashmail.net', 'getnada.com', 'nada.email',
    'dispostable.com', 'maildrop.cc', 'throwawaymail.com', 'fakeinbox.com',
    'mohmal.com', 'emailondeck.com', 'moakt.com', 'mailnesia.com',
    'mintemail.com', 'spambox.us', 'dropmail.me', 'inboxkitten.com',
    '1secmail.com', '1secmail.net', '1secmail.org', 'mail.tm', 'harakirimail.com',
    'burnermail.io', 'mytemp.email', 'tmpmail.org', 'tmpmail.net', 'tempinbox.com',
    'emailfake.com', 'fakemail.net', 'luxusmail.org', 'spamgourmet.com',
    'anonbox.net', 'mailcatch.com', 'tempail.com', 'tmail.ws', 'discard.email',
}

_URL_RE = re.compile(
    r'(https?:|ftp:|www\.|//|<\s*a\b|<[^>]+>|\b[\w-]+\.(?:com|net|org|cc|xyz|top|ru|click|site|online|info|biz|link|icu|shop)\b)',
    re.IGNORECASE,
)
_SPAM_RE = re.compile(
    r'(scam|24\s*/\s*7|\*\s*\*\s*\*|\bhs=|click here|deposit|bitcoin|crypto|casino|'
    r'\bbtc\b|\bwinner\b|you have (won|received)|\$\s?\d[\d,]{2,})',
    re.IGNORECASE,
)

MAX_NAME_LEN = 80
MAX_EMAIL_LEN = 254
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")


def is_disposable_email(email):
    domain = (email or '').rsplit('@', 1)[-1].strip().lower()
    return any(domain == d or domain.endswith('.' + d) for d in DISPOSABLE_DOMAINS)


def check_signup(full_name, email, honeypot=''):
    """Return (ok, reason). `reason` is for server logs only, never shown to the
    submitter (don't teach bots which rule they tripped)."""
    name = (full_name or '').strip()
    email = (email or '').strip().lower()

    if honeypot and honeypot.strip():
        return False, 'honeypot-filled'
    if not name or len(name) > MAX_NAME_LEN:
        return False, 'bad-name-length'
    if _URL_RE.search(name):
        return False, 'url-or-html-in-name'
    if _SPAM_RE.search(name):
        return False, 'spam-marker-in-name'
    if any(ord(c) < 32 for c in name):
        return False, 'control-chars-in-name'
    if len(email) > MAX_EMAIL_LEN or not _EMAIL_RE.match(email):
        return False, 'bad-email-format'
    if is_disposable_email(email):
        return False, 'disposable-email-domain'
    return True, None
