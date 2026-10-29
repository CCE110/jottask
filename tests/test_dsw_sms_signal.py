"""DSW SMS signals ("New YES reply from X") update the lead's task; they never insert a duplicate.

Dry-run scenarios for the 29 Oct 2026 Kyung Lee duplicate (d1d0755e 'New YES' + e030e3b2).
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault('PIPEREPLY_TOKEN', 'x')
import dsw_contact_dedup as dd
import dsw_sms_signal as sig
from tests.fake_supabase import FakeDB, UndefinedColumn, fake_task_manager

ROB = 'rob-uid'
NOW = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)
KYUNG = {'id': 'ihYNewjr5wPHJ03m5lFe', 'contactName': 'Kyung Lee', 'phone': '+61425257133', 'email': 'huddart9@gmail.com'}
YES = 'New YES reply from Kyung Lee. You will be connected shortly.'


def lead(**over):
    row = {'id': 'lead-1', 'user_id': ROB, 'category': 'DSW Solar', 'status': 'pending', 'lead_status': 'new_lead',
           'client_name': 'Kyung Lee', 'client_phone': '+61425257133', 'title': 'Call Kyung Lee - New DSW Lead',
           'description': f'Phone: +61425257133\nCRM: {dd.CRM_CONTACT_URL}{KYUNG["id"]}\nOpenSolar: x',
           'created_at': NOW.isoformat(), 'reminder_sent_at': '2026-10-28T00:00:00+00:00'}
    row.update(over)
    return row


class Harness:
    def __init__(self, contacts=(KYUNG,)):
        self.db = FakeDB(ROB)
        self.tm = fake_task_manager(self.db)
        self.contacts = list(contacts)
        self.reminders, self.stubs = [], []

    def pr_get(self, path, params):
        q = (params.get('query') or '').lower()
        return {'contacts': [c for c in self.contacts if q in (c['contactName'] + c['phone']).lower()]}

    def create_stub(self, raw, display, contact):
        self.stubs.append((raw, display, contact))
        row = {'user_id': ROB, 'category': 'DSW Solar', 'status': 'pending', 'client_name': display,
               'title': f'Review DSW SMS — {raw[:40]}', 'description': f'{raw}\n{dd.STUB_MARKER}'}
        self.db.table('tasks').insert(row).execute()
        return 'stub'

    def run(self, name, raw, **kw):
        return sig.apply_signal(name, raw, sb=self.db, tm=self.tm, user_id=ROB, create_stub=self.create_stub,
                                send_reminder=lambda t, tag: (self.reminders.append((t['id'], tag)) or (True, None)),
                                pr_get=self.pr_get, now=NOW, **kw)


# ── classification ────────────────────────────────────────────────────────────
def test_yes_reply_is_a_named_signal_not_new_yes():
    assert sig.classify_signal(YES) == 'Kyung Lee'
    import dsw_sms_poller as p
    assert p.extract_name(YES) == ('signal', 'Kyung Lee')


@pytest.mark.parametrize('text,name', [
    ('+QFollow Up With Christine Jessop on Aug 22nd 2026 at 12:00 am for Christine Jessop', 'Christine Jessop'),
    ('Cancelled Meeting Follow Up with Elaine Egan on Oct 27th 2026 at 12:00 am for Elaine Egan', 'Elaine Egan'),
    ('Kyung Lee replied YES to battery promo', 'Kyung Lee'),
])
def test_other_signal_shapes(text, name):
    assert sig.classify_signal(text) == name


@pytest.mark.parametrize('text', ['New YES', 'Urgent Reminder', 'New YES. Call now', 'Customer confidence issue, existing X'])
def test_junk_names_never_become_leads(text):
    import dsw_sms_poller as p
    assert sig.classify_signal(text) is None
    assert p.extract_name(text) == (None, None)
    assert sig.is_junk_name('New YES') and sig.is_junk_name('Urgent') and sig.is_junk_name('Reminder')
    assert not sig.is_junk_name('Kyung Lee')


# ── the rule ──────────────────────────────────────────────────────────────────
def test_yes_for_existing_lead_updates_and_refires_no_second_task():
    h = Harness()
    h.db.tables['tasks'] = [lead()]
    r = h.run('Kyung Lee', YES)
    assert r == {'action': 'update', 'task_id': 'lead-1'}
    assert len(h.db.tasks()) == 1 and h.stubs == []
    assert 'YES reply from Kyung Lee' in h.db.notes()[0]['content'] and h.db.notes()[0]['task_id'] == 'lead-1'
    assert h.reminders == [('lead-1', 'signal')]
    assert h.db.tasks()[0]['reminder_sent_at'] == NOW.isoformat()      # stamped after the send, so no double-send


def test_name_spelling_difference_still_matches_the_lead():
    h = Harness()
    h.db.tables['tasks'] = [lead()]
    assert h.run('Kyung Lea', YES)['action'] == 'update'
    assert len(h.db.tasks()) == 1


def test_yes_for_recently_completed_lead_reopens_it():
    h = Harness()
    h.db.tables['tasks'] = [lead(status='completed', completed_at=(NOW - timedelta(days=1)).isoformat())]
    r = h.run('Kyung Lee', YES)
    t = h.db.tasks()[0]
    assert r['action'] == 'reopen' and len(h.db.tasks()) == 1
    assert t['status'] == 'pending' and t['completed_at'] is None
    assert 're-opened' in h.db.notes()[0]['content'] and h.reminders


def test_old_completed_lead_and_cancelled_dupes_are_not_reopened():
    h = Harness()
    h.db.tables['tasks'] = [lead(status='completed', completed_at=(NOW - timedelta(days=45)).isoformat()),
                            lead(id='dupe', status='cancelled')]
    assert h.run('Kyung Lee', YES)['action'] == 'create_stub'
    assert [t['status'] for t in h.db.tasks()].count('cancelled') == 1   # nothing resurrected


def test_yes_for_genuinely_new_name_creates_exactly_one_stub():
    h = Harness(contacts=())
    r = h.run('Priya Nair', 'New YES reply from Priya Nair. You will be connected shortly.')
    assert r['action'] == 'create_stub' and len(h.stubs) == 1 and len(h.db.tasks()) == 1
    assert h.stubs[0][1] == 'Priya Nair'
    assert h.run('Priya Nair', 'New YES reply from Priya Nair.')['action'] == 'update'   # 2nd SMS -> note, no 2nd task
    assert len(h.db.tasks()) == 1 and h.reminders == []                                  # stub: note only, no CRM email


def test_lookup_error_fails_closed_nothing_inserted():
    h = Harness()
    h.db.tables['tasks'] = []
    h.db.fail = True
    with pytest.raises(dd.DedupLookupError):
        h.run('Kyung Lee', YES)
    h.db.fail = False
    assert h.db.tasks() == [] and h.stubs == []


def test_dry_run_writes_nothing():
    h = Harness()
    h.db.tables['tasks'] = [lead()]
    assert h.run('Kyung Lee', YES, dry_run=True)['action'] == 'update'
    assert h.db.notes() == [] and h.reminders == []
    assert h.db.tasks()[0]['reminder_sent_at'] == '2026-10-28T00:00:00+00:00'


# ── signal-before-lead race: the poll must not treat a stub as "task exists" ──
def test_poll_dedup_keys_ignore_stubs_but_not_real_leads():
    import dsw_railway_poll as rp
    h = Harness()
    h.db.tables['tasks'] = [
        {'id': 's', 'category': 'DSW Solar', 'client_name': 'Priya Nair', 'client_phone': '+61411111111',
         'status': 'pending', 'created_at': NOW.isoformat(), 'description': f'x\n{dd.STUB_MARKER}'},
        {'id': 'r', 'category': 'DSW Solar', 'client_name': 'Kyung Lee', 'client_phone': '+61425257133',
         'status': 'pending', 'created_at': NOW.isoformat(), 'description': 'real'}]
    h.db.tables['tasks'][0]['created_at'] = datetime.now(timezone.utc).isoformat()
    h.db.tables['tasks'][1]['created_at'] = datetime.now(timezone.utc).isoformat()
    phones, names, _ = rp._existing_dsw_keys(h.tm, days=7)
    assert 'priya nair' not in names and '0411111111' not in phones
    assert 'kyung lee' in names and '0425257133' in phones


# ── the fake itself must reject columns that do not exist (the task_notes.author bug) ──
def test_fake_rejects_nonexistent_columns():
    db = FakeDB()
    with pytest.raises(UndefinedColumn):
        db.table('task_notes').insert({'task_id': 'x', 'content': 'c', 'author': 'DSW email'}).execute()
    with pytest.raises(UndefinedColumn):
        db.table('tasks').select('id, nope').execute()


# ── 29 Oct 2026: lowercase "appointment" SMS superseded Tracie Burton's worked task ──────────────
@pytest.mark.parametrize('text,name', [
    ('Reminder: Upcoming appointment with Tracie Burton at Thursday, 29 October 2026 1:00 PM (AEST).', 'Tracie Burton'),
    ('Reminder: Upcoming Appointment with Tracie Burton in 10 minutes.', 'Tracie Burton'),
    ('Reminder: Upcoming appointment with Sam D at Monday, 26 October 2026 1:30 PM (AEST).', 'Sam D'),
])
def test_appointment_reminders_are_never_new_leads(text, name):
    import dsw_sms_poller as p
    assert p.extract_name(text) == ('appt_reminder', name)


# ── 29 Oct 2026: bracketed nickname "Sivhoung (Siv) Lim" broke extraction -> unstructured stub dupe ──
@pytest.mark.parametrize('text,kind', [
    ('Hi Rob, Sivhoung (Siv) Lim has just been assigned to you. Please call ASAP.\n\n- D', 'new_lead'),
    ('Reminder: Upcoming appointment with Sivhoung (Siv) Lim at Friday, 30 October 2026 1:00 PM (AEST).', 'appt_reminder'),
    ('Reminder: Upcoming Appointment with Sivhoung (Siv) Lim in 10 minutes.', 'appt_reminder'),
    ('New YES reply from Sivhoung (Siv) Lim. You will be connected shortly.', 'signal'),
])
def test_bracketed_nickname_shapes_extract(text, kind):
    import dsw_sms_poller as p
    assert p.extract_name(text) == (kind, 'Sivhoung (Siv) Lim')


def test_bracketed_nickname_matches_task_and_creates_no_second_task():
    h = Harness(contacts=({'id': 'SIV', 'contactName': 'Sivhoung Lim', 'phone': '+61421595186', 'email': 'x@y.com'},))
    h.db.tables['tasks'] = [lead(client_name='Sivhoung (siv) Lim', client_phone='+61421595186',
                                 description=f'Phone: +61421595186\nCRM: {dd.CRM_CONTACT_URL}SIV')]
    for nm in ('Sivhoung (Siv) Lim', 'Sivhoung Lim'):
        r = h.run(nm, 'Hi Rob, Sivhoung (Siv) Lim has just been assigned to you.', allow_create=False)
        assert r['action'] == 'update'
    assert len(h.db.tasks()) == 1 and h.stubs == []
    # name-only (no phone/cid on the task): bracket must not defeat the fuzzy match
    h.db.tables['tasks'] = [lead(client_name='Sivhoung (siv) Lim', client_phone=None, description='no ids')]
    assert h.run('Sivhoung Lim', 'x', allow_create=False)['action'] == 'update'


def test_new_lead_sms_for_existing_lead_updates_never_supersedes():
    h = Harness()
    h.db.tables['tasks'] = [lead()]
    r = h.run('Kyung Lee', 'Hi Rob, Kyung Lee has just been assigned to you.', allow_create=False)
    assert r['action'] == 'update' and len(h.db.tasks()) == 1 and h.stubs == []


def test_new_lead_sms_for_unknown_person_returns_no_match_and_creates_nothing():
    h = Harness(contacts=())
    r = h.run('Priya Nair', 'Hi Rob, Priya Nair has just been assigned to you.', allow_create=False)
    assert r['action'] == 'no_match' and h.db.tasks() == [] and h.stubs == []
