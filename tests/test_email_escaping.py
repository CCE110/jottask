"""Outbound-email HTML injection regressions. Hostile task/lead/profile text must come out inert."""
import pytest

SCRIPT = '<script>alert(1)</script>'
IMG = '<img src=x onerror=alert(1)>'
ATTR = '"><a href="http://evil.example/">pay now</a>'


def _inert(html):
    assert '<script>alert(1)</script>' not in html
    assert '<img src=x onerror' not in html
    assert '<a href="http://evil.example/">' not in html


# ── saas_scheduler ──────────────────────────────────────────────────────────

def test_reminder_email_escapes_title_and_client():
    from saas_scheduler import generate_reminder_email_html
    task = {'id': 'abc-123', 'title': f'Call {SCRIPT}', 'client_name': IMG + ATTR, 'priority': 'high'}
    html = generate_reminder_email_html(task, '9:00am', 'Bob', is_overdue=False)
    _inert(html)
    assert '&lt;script&gt;' in html
    assert 'Call ' in html  # legit text still there


def test_summary_email_escapes_titles_clients_names_projects():
    from saas_scheduler import generate_summary_email_html
    t = {'title': SCRIPT, 'client_name': IMG, 'due_date': '2026-11-17', 'due_time': '09:00:00',
         'category': ATTR, 'priority': 'high', 'id': 'x'}
    tasks = {'overdue': [t], 'due_today': [t], 'upcoming': [t], 'total_pending': 3}
    projects = {'projects': [{'name': SCRIPT, 'color': ATTR, 'total': 2, 'completed': 1,
                              'remaining': 1, 'progress': 50}], 'active_count': 1}
    html = generate_summary_email_html(f'Bob {SCRIPT}', 'Australia/Brisbane', tasks, projects)
    _inert(html)


def test_summary_email_normal_text_unchanged():
    from saas_scheduler import generate_summary_email_html
    t = {'title': 'Quote for Smith & Sons', 'client_name': 'Smith', 'due_date': '2026-11-17',
         'due_time': None, 'category': 'DSW Solar', 'id': 'x'}
    html = generate_summary_email_html('Rob', 'Australia/Brisbane',
                                       {'overdue': [], 'due_today': [t], 'upcoming': [], 'total_pending': 1},
                                       {'projects': [], 'active_count': 0})
    assert 'Quote for Smith &amp; Sons' in html and 'DSW SOLAR' in html


# ── dsw_lead_poller.send_email (every DSW lead / reminder / appointment email) ──

@pytest.fixture
def captured_lead_email(monkeypatch):
    import dsw_lead_poller as dsw
    import email_utils
    sent = {}
    monkeypatch.setattr(dsw, '_rate_limit_check', lambda *a, **k: (True, None))

    def fake_send(to, subject, html, **k):
        sent.update(to=to, subject=subject, html=html)
        return True, None
    monkeypatch.setattr(email_utils, 'send_email', fake_send)
    return dsw, sent


def test_dsw_lead_email_escapes_all_lead_fields(captured_lead_email):
    dsw, sent = captured_lead_email
    ok, _ = dsw.send_email(
        name=f'Jane {SCRIPT}', phone='0400 000 000' + ATTR, addr='1 St ' + IMG,
        src=IMG, summary=f'wants solar\n{SCRIPT}\nline 3', crm_url='https://x/contacts/detail/abc' + ATTR,
        os_url='https://os/x' + ATTR, email=f'a@b.c{ATTR}', source_badge_text=SCRIPT,
        appointment={'when': SCRIPT, 'type': IMG, 'phone': '0400' + ATTR})
    assert ok
    _inert(sent['html'])
    assert 'wants solar' in sent['html'] and 'line 3' in sent['html']


def test_dsw_lead_email_subject_is_single_line(captured_lead_email):
    dsw, sent = captured_lead_email
    dsw.send_email(name='Jane\r\nBcc: evil@example.com', phone='1', addr='a', src='s', summary='x',
                   crm_url='https://x/contacts/detail/abc', os_url='')
    assert '\n' not in sent['subject'] and '\r' not in sent['subject']


def test_dsw_lead_email_plain_lead_still_renders(captured_lead_email):
    dsw, sent = captured_lead_email
    dsw.send_email('Jane Citizen', '0400000000', '1 Test St, Brisbane', 'SolarQuotes',
                   'Wants 10kW + battery\nBill $400/q', 'https://x/contacts/detail/abc', '')
    html = sent['html']
    assert 'Jane Citizen' in html and '1 Test St, Brisbane' in html and 'Wants 10kW + battery' in html
    assert 'tel:0400000000' in html
