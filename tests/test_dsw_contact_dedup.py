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
from tests.fake_supabase import FakeDB, fake_task_manager

ROB = 'rob-uid'
CID = 'abc123CID'
CONTACT = {'id': CID, 'contactName': 'Kyi Aung', 'phone': '+61412345678', 'email': 'kyi@example.com'}


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB(ROB)
    import task_manager
    monkeypatch.setattr(task_manager, 'TaskManager', lambda *a, **k: fake_task_manager(fake))
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
