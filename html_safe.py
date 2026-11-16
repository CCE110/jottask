"""
Shared HTML-escaping helpers for server-built HTML (admin pages and emails).

Rule: any value that did not originate in our own code (user profile fields,
chat text, task titles, client names, lead/SMS/email-derived text) MUST go
through esc() before being interpolated into an f-string that becomes HTML.
"""

import html as _html


def esc(value):
    """HTML-escape any value for safe use in element text AND quoted attributes.
    None -> ''. Non-strings are str()'d first."""
    if value is None:
        return ''
    return _html.escape(str(value), quote=True)


def esc_br(value):
    """Escape, then turn newlines into <br> (for multi-line message bodies)."""
    return esc(value).replace('\r\n', '\n').replace('\n', '<br>')


def plain_subject(value, limit=120):
    """Make text safe for an email Subject: single line, capped. (Not HTML;
    mail clients render Subject as plain text.)"""
    s = '' if value is None else str(value)
    return ' '.join(s.split())[:limit]
