"""Stored-XSS regression: user-controlled text must never reach the admin's browser unescaped."""
import types
import pytest

SCRIPT = '<script>alert(document.cookie)</script>'
IMG = '<img src=x onerror=alert(1)>'
ATTR = "x' onmouseover='alert(1)"


class _Chain:
    """Chainable stand-in for a supabase query; .execute() returns canned rows."""
    def __init__(self, tables, name):
        self.tables, self.name, self._single = tables, name, False

    def __getattr__(self, _):
        def f(*a, **k):
            if _ == 'single':
                self._single = True
            return self
        return f

    def execute(self):
        rows = self.tables.get(self.name, [])
        data = (rows[0] if rows else None) if self._single else rows
        return types.SimpleNamespace(data=data, count=len(rows))


class _StubSupabase:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Chain(self.tables, name)


@pytest.fixture
def admin_client(client, monkeypatch):
    import dashboard
    import auth
    tables = {
        'support_conversations': [{'id': f'abc{ATTR}', 'status': IMG, 'escalated_at': '2026-11-16T00:00:00',
                                   'users': {'full_name': IMG, 'email': f'{SCRIPT}@x.com'}}],
        'support_messages': [{'sender_type': f'user{ATTR}', 'message': f'hi {SCRIPT}\nline2',
                              'created_at': '2026-11-16T00:00:00'}],
        'users': [{'id': 'u1', 'full_name': SCRIPT, 'email': f'{IMG}@x.com',
                   'subscription_status': ATTR, 'created_at': '2026-11-16T00:00:00'}],
    }
    stub = _StubSupabase(tables)
    monkeypatch.setattr(dashboard, 'supabase', stub)
    monkeypatch.setattr(auth, 'supabase', stub)
    with client.session_transaction() as s:
        s['user_id'] = 'admin-1'
        s['user_role'] = 'global_admin'
    return client


def _assert_safe(body):
    assert '<script>alert(document.cookie)</script>' not in body
    assert '<img src=x onerror' not in body
    assert "' onmouseover='" not in body
    assert '&lt;script&gt;' in body or '&lt;img' in body


def test_admin_chat_view_escapes_everything(admin_client):
    r = admin_client.get('/admin/chats/abc')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    _assert_safe(body)
    assert 'line2' in body  # multi-line message still rendered


def test_admin_dashboard_escapes_signups_and_escalated_chats(admin_client):
    r = admin_client.get('/admin')
    assert r.status_code == 200
    _assert_safe(r.get_data(as_text=True))
