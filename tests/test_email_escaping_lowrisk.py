"""Second batch: approval / confirmation / install-order / squad / SMS-fallback / admin-reply emails."""
from unittest.mock import MagicMock

import pytest

SCRIPT = '<script>alert(1)</script>'
IMG = '<img src=x onerror=alert(1)>'
LINK = '<a href="http://evil.example/">approve here</a>'


def _inert(html):
    assert '<script>alert(1)</script>' not in html
    assert '<img src=x onerror' not in html
    assert '<a href="http://evil.example/">' not in html


@pytest.fixture
def captured(monkeypatch):
    import email_utils
    sent = []
    monkeypatch.setattr(email_utils, 'send_email',
                        lambda to, subject, html, **k: (sent.append((to, subject, html)) or (True, None)))
    return sent


def _processor():
    from saas_email_processor import AIEmailProcessor
    p = AIEmailProcessor.__new__(AIEmailProcessor)
    p.tm = MagicMock()
    return p


def test_approval_email_escapes_inbound_email_fields(captured):
    from saas_email_processor import UserContext
    p = _processor()
    actions = [{'action_type': 'update_crm', 'title': f'Update {SCRIPT}', 'customer_name': IMG,
                'crm_notes': LINK, 'description': SCRIPT}]
    p.send_approval_email(f'Re: quote {SCRIPT}\r\nBcc: x@evil.example', f'Eve {IMG} <eve@evil.example>',
                          actions, f'AI says {LINK}',
                          user_context=UserContext(user_id='u1', email_address='user@example.com',
                                                   company_name=SCRIPT, full_name=IMG))
    assert len(captured) == 1
    to, subject, html = captured[0]
    _inert(html)
    assert '\n' not in subject and '\r' not in subject
    assert '<em>' in html           # our own markup is preserved
    assert 'Approve</a>' in html    # real action buttons still render


def test_approval_description_keeps_em_but_escapes_values():
    p = _processor()
    out = p._format_action_description({'action_type': 'update_crm', 'crm_notes': SCRIPT})
    assert out.startswith('Add to CRM notes: <em>') and '<script>' not in out


def test_worker_task_confirmation_escapes_title(captured):
    p = _processor()
    p._send_task_confirmation('user@example.com', f'Bob {SCRIPT}',
                              {'id': 't1', 'title': f'Ring {IMG}', 'due_date': '2026-11-17', 'due_time': '09:00:00'})
    assert len(captured) == 1
    _inert(captured[0][2])
    assert 'Ring' in captured[0][2]


def test_dashboard_task_confirmation_escapes_title(monkeypatch):
    import dashboard
    sent = []
    # dashboard imported send_email by name, so patch ITS binding
    monkeypatch.setattr(dashboard, 'send_email',
                        lambda to, subject, html, **k: (sent.append((to, subject, html)) or (True, None)))
    dashboard.send_task_confirmation_email('user@example.com', f'Ring {SCRIPT}\nBcc: x@evil.example',
                                           '2026-11-17', '09:00:00', 't1', user_name=f'Bob {IMG}')
    assert len(sent) == 1
    to, subject, html = sent[0]
    _inert(html)
    assert 'Ring' in html and '\n' not in subject


def test_install_order_email_escapes_fields():
    from install_order import build_install_order_email
    n = MagicMock(address=f'1 St {SCRIPT}', project_id=f'8{IMG}', project_link='https://os/x"><b>')
    ctx = MagicMock(customer_name=f'Jane {SCRIPT}')
    html = build_install_order_email(n, 'draft <b>x</b>', ctx, user_name=IMG)
    _inert(html)
    assert '"><b>' not in html


def test_install_order_subject_single_line(captured):
    from install_order import send_install_order_email
    n = MagicMock(address='1 St\r\nBcc: x@evil.example', project_id='1', project_link='https://os/x')
    send_install_order_email('to@example.com', n, 'draft', MagicMock(customer_name='Jane'), 'Bob')
    assert captured and '\n' not in captured[0][1]
