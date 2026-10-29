"""OpenSolar healer: creates a project only for real, recent, Rob-assigned pending tasks showing 'OpenSolar: pending'."""
import types
from datetime import datetime, timedelta, timezone

import pytest

import dsw_os_healer as h
import dsw_contact_dedup as dd
from tests.fake_supabase import FakeDB

ROB = 'rob-uid'
NOW = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)


def task(i, age_min=60, **over):
    row = {'id': i, 'user_id': ROB, 'category': 'DSW Solar', 'status': 'pending', 'client_name': f'Name {i}',
           'title': f'Call Name {i}', 'metadata': {},
           'description': f'Phone: x\nCRM: {dd.CRM_CONTACT_URL}cid{i}\nOpenSolar: pending\n\nbody',
           'created_at': (NOW - timedelta(minutes=age_min)).isoformat()}
    row.update(over)
    return row


class FakeDsw:
    def __init__(self, contacts=None, url='https://app.opensolar.com/#/projects/555/info', fail=False):
        self.contacts = contacts or {}
        self.created, self.notes, self.url, self.fail = [], [], url, fail

    def get_full(self, cid):
        return self.contacts.get(cid, {'assignedTo': h.ROB_UID, 'address1': '1 Test St', 'city': 'Bris', 'state': 'QLD',
                                       'postalCode': '4000', 'firstName': 'A', 'lastName': 'B', 'phone': '+61400000000'})

    def make_opensolar(self, *a, **k):
        self.created.append((a, k))
        if self.fail:
            return None, None
        pid = 555 + (len(self.created) - 1)                      # a fresh project per create
        return pid, self.url.replace('555', str(pid))

    def get_os_url_from_crm(self, cid): return None
    def save_to_crm(self, cid, url, summary, overwrite=True): self.notes.append((cid, url))


@pytest.fixture
def db():
    d = FakeDB(ROB)
    return d


def run(db, dsw, **kw):
    return h.heal(db, ROB, dsw, now=NOW, log=lambda *a: None, **kw)


def test_creates_and_links_eligible_task(db):
    db.tables['tasks'] = [task('t1')]
    dsw = FakeDsw()
    r = run(db, dsw, dry_run=False)
    assert r[0]['action'] == 'created'
    assert 'OpenSolar: https://app.opensolar.com/#/projects/555/info' in db.tasks()[0]['description']
    assert 'OpenSolar: pending' not in db.tasks()[0]['description'] and db.tasks()[0]['description'].endswith('body')
    assert dsw.notes == [('cidt1', 'https://app.opensolar.com/#/projects/555/info')]


def test_dry_run_writes_and_creates_nothing(db):
    db.tables['tasks'] = [task('t1')]
    dsw = FakeDsw()
    assert run(db, dsw, dry_run=True)[0]['action'] == 'would_create'
    assert dsw.created == [] and 'OpenSolar: pending' in db.tasks()[0]['description']


@pytest.mark.parametrize('over,reason', [
    (dict(title='Review DSW SMS - x'), 'stub'),
    (dict(description=f'x\n{dd.STUB_MARKER}\nOpenSolar: pending'), 'stub'),
    (dict(description='no link\nOpenSolar: pending'), 'no PipeReply'),
    (dict(age_min=5), 'too new'),
    (dict(age_min=60 * 24 * 20), 'stale'),
    (dict(metadata={'os_heal_attempts': 3}), 'gave up'),
])
def test_ineligible_tasks_skipped(db, over, reason):
    age = over.pop('age_min', 60)
    db.tables['tasks'] = [task('t1', age_min=age, **over)]
    dsw = FakeDsw()
    r = run(db, dsw, dry_run=False)
    assert r[0]['action'] == 'skip' and reason in r[0]['reason'] and dsw.created == []


def test_already_linked_and_non_pending_ignored(db):
    db.tables['tasks'] = [task('t1', description='OpenSolar: https://app.opensolar.com/#/projects/1/info'),
                          task('t2', status='completed')]
    assert run(db, FakeDsw(), dry_run=False) == []


def test_contact_not_robs_or_no_address_skipped(db):
    db.tables['tasks'] = [task('t1'), task('t2')]
    dsw = FakeDsw(contacts={'cidt1': {'assignedTo': 'someone-else', 'address1': 'x'},
                            'cidt2': {'assignedTo': h.ROB_UID, 'address1': ''}})
    r = run(db, dsw, dry_run=False)
    assert [x['action'] for x in r] == ['skip', 'skip'] and dsw.created == []


def test_failure_counts_attempts_and_gives_up(db):
    db.tables['tasks'] = [task('t1')]
    dsw = FakeDsw(fail=True)
    for n in (1, 2, 3):
        assert run(db, dsw, dry_run=False)[0]['action'] == 'failed'
        assert db.tasks()[0]['metadata']['os_heal_attempts'] == n
    assert run(db, dsw, dry_run=False)[0]['action'] == 'skip'            # 4th run: gave up
    assert len(dsw.created) == 3


def test_existing_project_used_by_another_task_is_not_linked(db):
    db.tables['tasks'] = [task('t1'), task('other', description='OpenSolar: https://app.opensolar.com/#/projects/555/info')]
    r = run(db, FakeDsw(), dry_run=False)
    assert r[0]['action'] == 'skip' and 'already used' in r[0]['reason']
    assert 'OpenSolar: pending' in db.tasks()[0]['description']


def test_per_run_limit(db):
    db.tables['tasks'] = [task(f't{i}') for i in range(5)]
    dsw = FakeDsw()
    r = run(db, dsw, dry_run=False)
    assert [x['action'] for x in r].count('created') == h.LIMIT and [x['action'] for x in r].count('deferred') == 2
