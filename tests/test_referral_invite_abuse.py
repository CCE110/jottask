"""Referral invite is an email relay to any address the user types: it must be capped, validated, and inert."""
import types
import pytest

SCRIPT = '<script>alert(1)</script>'


class _Chain:
    def __init__(self, db, name):
        self.db, self.name, self._single = db, name, False

    def __getattr__(self, attr):
        def f(*a, **k):
            if attr == 'single':
                self._single = True
            if attr == 'insert':
                self.db.inserted.append((self.name, a[0]))
            return self
        return f

    def execute(self):
        if self.db.fail_count and self.name == 'referral_invites' and not self._single and self.db.armed:
            raise RuntimeError('db down')
        rows = self.db.tables.get(self.name, [])
        data = (rows[0] if rows else None) if self._single else rows
        return types.SimpleNamespace(data=data, count=len(rows))


class _DB:
    def __init__(self, user_name='Bob Builder', recent_invites=0, fail_count=False):
        self.inserted = []
        self.fail_count = fail_count
        self.armed = True
        self.tables = {
            'users': [{'full_name': user_name, 'referral_code': 'ABC123'}],
            'referral_invites': [{'id': i} for i in range(recent_invites)],
        }

    def table(self, name):
        return _Chain(self, name)


@pytest.fixture
def invite(authenticated_client, monkeypatch):
    import dashboard
    sent = []
    monkeypatch.setattr(dashboard, 'send_email', lambda to, subject, html, **k: (sent.append((to, subject, html)) or (True, None)))

    def run(db, email):
        monkeypatch.setattr(dashboard, 'supabase', db)
        authenticated_client.post('/settings/invite', data={'invited_email': email})
        return sent
    return run


def test_normal_invite_sends(invite):
    db = _DB()
    sent = invite(db, 'friend@gmail.com')
    assert len(sent) == 1 and sent[0][0] == 'friend@gmail.com' and 'Bob Builder' in sent[0][1]


@pytest.mark.parametrize('addr', ['a@mailinator.com', 'not-an-email', 'x@', 'a@b@c.com', '<x>@y.com'])
def test_invalid_or_disposable_recipient_rejected(invite, addr):
    assert invite(_DB(), addr) == []


def test_daily_cap_blocks(invite):
    assert invite(_DB(recent_invites=10), 'friend@gmail.com') == []


def test_cap_check_failure_fails_closed(invite):
    assert invite(_DB(fail_count=True), 'friend@gmail.com') == []


def test_scam_referrer_name_not_sent_to_third_party(invite):
    sent = invite(_DB(user_name='* * * SCAM SERVICE 24/7: https://myxa.cc/ * * *'), 'friend@gmail.com')
    assert len(sent) == 1
    to, subject, html = sent[0]
    assert 'SCAM' not in subject and 'myxa.cc' not in html and 'A Jottask user' in subject


def test_script_in_referrer_name_inert(invite):
    sent = invite(_DB(user_name=f'Bob {SCRIPT}'), 'friend@gmail.com')
    assert len(sent) == 1 and '<script>' not in sent[0][2]
