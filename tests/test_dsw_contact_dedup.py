"""One PipeReply contact = one pending DSW task across the poll and both DSW email handlers.

Uses an in-memory fake Supabase + fake PipeReply. Contact is PipeReply 'Kyi Aung'; the email spells
the name differently ('Kyi Aun') to prove matching does not rely on the name alone.
"""
import os
import types
import importlib.util
from datetime import datetime, timezone

import pytest

os.environ.setdefault('PIPEREPLY_TOKEN', 'x')
import dsw_contact_dedup as dd

ROB = 'rob-uid'
CID = 'abc123CID'
CONTACT = {'id': CID, 'contactName': 'Kyi Aung', 'phone': '+61412345678', 'email': 'kyi@example.com'}


class FakeQuery:
    def __init__(self, db, name):
        self.db, self.name, self.rows = db, name, None
        self.filters, self._limit, self._op, self._payload = [], None, 'select', None

    def select(self, *_a, **_k): return self
    def eq(self, c, v): self.filters.append(lambda r: r.get(c) == v); return self
    def gte(self, c, v): self.filters.append(lambda r: (r.get(c) or '') >= v); return self
    def order(self, *_a, **_k): return self
    def limit(self, n): self._limit = n; return self

    def ilike(self, c, pat):
        p = pat.lower().strip('%')
        wild = pat.startswith('%')
        self.filters.append(lambda r: (p in (r.get(c) or '').lower()) if wild else (p == (r.get(c) or '').lower()))
        return self

    def or_(self, expr):
        conds = []
        for part in expr.split(','):
            col, op, val = part.split('.', 2)
            conds.append((col, op, val))
        def m(r):
            for col, op, val in conds:
                v = r.get(col) or ''
                if (op == 'eq' and v == val) or (op == 'ilike' and v.lower() == val.lower()):
                    return True
            return False
        self.filters.append(m)
        return self

    def insert(self, payload): self._op, self._payload = 'insert', payload; return self
    def update(self, payload): self._op, self._payload = 'update', payload; return self

    def execute(self):
        if self.db.fail and self.name == 'tasks':
            raise RuntimeError('supabase down')
        table = self.db.tables.setdefault(self.name, [])
        if self._op == 'insert':
            row = dict(self._payload)
            row.setdefault('id', f'{self.name}-{len(table) + 1}')
            row.setdefault('created_at', datetime.now(timezone.utc).isoformat())
            table.append(row)
            return types.SimpleNamespace(data=[row])
        hits = [r for r in table if all(f(r) for f in self.filters)]
        if self._op == 'update':
            for r in hits:
                r.update(self._payload)
            return types.SimpleNamespace(data=hits)
        hits = list(reversed(hits))
        return types.SimpleNamespace(data=hits[:self._limit] if self._limit else hits)


class FakeDB:
    def __init__(self):
        self.tables = {'users': [{'id': ROB, 'email': 'rob@cloudcleanenergy.com.au'}]}
        self.fail = False

    def table(self, name): return FakeQuery(self, name)

    def tasks(self): return self.tables.get('tasks', [])


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    import task_manager
    monkeypatch.setattr(task_manager, 'TaskManager', lambda *a, **k: types.SimpleNamespace(supabase=fake))
    # PipeReply: phone / name search returns the contact; email handler imports dsw_lead_poller -> stub it
    monkeypatch.setattr(dd, '_pr_get', lambda path, params: {'contacts': [CONTACT]})
    monkeypatch.setattr(importlib.util, 'spec_from_file_location',
                        lambda *a, **k: types.SimpleNamespace(loader=types.SimpleNamespace(
                            exec_module=lambda m: setattr(m, 'send_email', lambda *a, **k: None))))
    monkeypatch.setattr(importlib.util, 'module_from_spec', lambda spec: types.SimpleNamespace())
    monkeypatch.setenv('DSW_TASK_ASSIGNED_ENABLED', '1')
    return fake


APPT_BODY = ("Contact Name: Kyi Aun\nAppointment Title: Intro Call\nDate and Time: 30 Oct 2026 10:00 AM\n"
             "We will call you on 0412 345 678")
ASSIGNED_BODY = ("Contact Name: Kyi Aun\nTask: Call now: replied YES to DSW battery sale\n"
                 "Due: 30 Oct 2026 10:00 AM\nPhone: 0412345678")


def poll_style_task(**over):
    """Shape make_task writes for a poll-created task."""
    row = {'id': 'poll-1', 'user_id': ROB, 'category': 'DSW Solar', 'status': 'pending',
           'client_name': 'Kyi Aung', 'client_phone': '+61412345678',
           'description': f'Phone: +61412345678\nCRM: {dd.CRM_CONTACT_URL}{CID}\nOpenSolar: pending\n\nlead',
           'created_at': datetime.now(timezone.utc).isoformat()}
    row.update(over)
    return row


def poll_would_skip(db):
    """The poll's own in-process() guard: pending DSW task for this name OR phone in the last 24h."""
    import dsw_lead_poller as poll
    task, ok = poll._find_recent_pending_dsw_task('Kyi Aung', phone='+61412345678')
    assert ok
    return task is not None


@pytest.mark.parametrize('handler,body', [('appt', APPT_BODY), ('assigned', ASSIGNED_BODY)])
def test_poll_first_then_email_makes_no_second_task(db, handler, body):
    import saas_email_processor as sp
    db.tables['tasks'] = [poll_style_task()]
    if handler == 'appt':
        sp.handle_dsw_appointment('Appointment Confirmation', body, 'reply@replies.dswenergy.com.au')
    else:
        r = sp.handle_dsw_task_assigned('New Task Assigned to You', body, 'reply@replies.dswenergy.com.au')
        assert r['action'] == 'update'
    assert len(db.tasks()) == 1


@pytest.mark.parametrize('handler,body', [('appt', APPT_BODY), ('assigned', ASSIGNED_BODY)])
def test_email_first_then_poll_skips(db, handler, body, monkeypatch):
    import saas_email_processor as sp
    if handler == 'appt':
        sp.handle_dsw_appointment('Appointment Confirmation', body, 'reply@replies.dswenergy.com.au')
    else:
        sp.handle_dsw_task_assigned('New Task Assigned to You', body, 'reply@replies.dswenergy.com.au')
    assert len(db.tasks()) == 1
    t = db.tasks()[0]
    assert t['client_phone'] == '+61412345678'
    assert f'/contacts/detail/{CID}' in t['description']
    # name differs ('Kyi Aun' vs PipeReply 'Kyi Aung'): PipeReply name is used, and the poll matches by phone anyway
    assert poll_would_skip(db)


def test_both_email_handlers_same_contact_one_task(db):
    import saas_email_processor as sp
    sp.handle_dsw_task_assigned('New Task Assigned to You', ASSIGNED_BODY, 'reply@replies.dswenergy.com.au')
    sp.handle_dsw_appointment('Appointment Confirmation', APPT_BODY, 'reply@replies.dswenergy.com.au')
    sp.handle_dsw_task_assigned('New Task Assigned to You', ASSIGNED_BODY, 'reply@replies.dswenergy.com.au')
    assert len(db.tasks()) == 1


def test_matches_by_contact_id_when_name_and_phone_differ(db):
    db.tables['tasks'] = [poll_style_task(client_name='Totally Different', client_phone=None,
                                          description=f'CRM: {dd.CRM_CONTACT_URL}{CID}')]
    t, _ = dd.find_existing_for_contact(db, ROB, name='Kyi Aun', phone='')
    assert t and t['id'] == 'poll-1'


def test_completed_task_does_not_block_fresh_yes(db):
    import saas_email_processor as sp
    db.tables['tasks'] = [poll_style_task(status='completed'), poll_style_task(id='x', status='cancelled')]
    r = sp.handle_dsw_task_assigned('New Task Assigned to You', ASSIGNED_BODY, 'reply@replies.dswenergy.com.au')
    assert r['action'] == 'create'
    assert len([t for t in db.tasks() if t['status'] == 'pending']) == 1


def test_task_lookup_error_fails_closed(db):
    import saas_email_processor as sp
    db.tables['tasks'] = []
    db.fail = True
    with pytest.raises(Exception):
        sp.handle_dsw_task_assigned('New Task Assigned to You', ASSIGNED_BODY, 'reply@replies.dswenergy.com.au')
    with pytest.raises(Exception):
        sp.handle_dsw_appointment('Appointment Confirmation', APPT_BODY, 'reply@replies.dswenergy.com.au')
    db.fail = False
    assert db.tasks() == []


def test_pipereply_error_fails_closed(db, monkeypatch):
    import saas_email_processor as sp

    def boom(path, params):
        raise dd.DedupLookupError('HTTP 401')
    monkeypatch.setattr(dd, '_pr_get', boom)
    with pytest.raises(dd.DedupLookupError):
        sp.handle_dsw_appointment('Appointment Confirmation', APPT_BODY, 'reply@replies.dswenergy.com.au')
    with pytest.raises(dd.DedupLookupError):
        sp.handle_dsw_task_assigned('New Task Assigned to You', ASSIGNED_BODY, 'reply@replies.dswenergy.com.au')
    assert db.tasks() == []


def test_assigned_handler_off_by_default(db, monkeypatch):
    import saas_email_processor as sp
    monkeypatch.delenv('DSW_TASK_ASSIGNED_ENABLED')
    assert sp.handle_dsw_task_assigned('New Task Assigned to You', ASSIGNED_BODY, 'reply@replies.dswenergy.com.au') is False
    assert db.tasks() == []
