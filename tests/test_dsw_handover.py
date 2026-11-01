"""Post-sale "congrats on your sale ... Sales Handover Form" SMS: a non-lead TODO, never a lead (Rob, 1 Nov 2026).

Dry-run scenarios: Ajay (first name only), Adam Glyde, Damian Hadjinicolaou, a repeat SMS, a lookup failure,
plus the leak checks (post-sale must stay out of the lead pipeline / lead email paths).
"""
import os
import types
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault('PIPEREPLY_TOKEN', 'x')
import dsw_contact_dedup as dd
import dsw_handover as ho
from tests.fake_supabase import FakeDB, fake_task_manager

ROB = 'rob-uid'
NOW = datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc)          # 11:00 AEST
LINK = 'https://jetsubmit.com/f/MC0wRXpWxheGnF9rIAmNlUqC-p0U4X9dyhXKlLhXm2l'


def sms(name): return f'Hi Rob, congrats on your sale to {name}!\n\nTo finalise your sale, please complete the Sales Handover Form: {LINK}'


def task(tid, name, status='pending', days_ago=3, **kw):
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    row = {'id': tid, 'user_id': ROB, 'category': 'DSW Solar', 'status': status, 'lead_status': 'won', 'client_name': name,
           'client_phone': '+61400000001', 'title': f'Call {name}', 'created_at': ts,
           'completed_at': ts if status == 'completed' else None, 'description': 'x'}
    row.update(kw)
    return row


class H:
    def __init__(self, tasks=()):
        self.db = FakeDB(ROB)
        self.db.tables['tasks'] = list(tasks)
        self.tm = fake_task_manager(self.db)

    def run(self, text, **kw):
        return ho.apply_handover(text, sb=self.db, tm=self.tm, user_id=ROB, now=NOW, **kw)

    def todos(self): return [t for t in self.db.tasks() if t['category'] == ho.HANDOVER_CATEGORY]


# ── classification: handover wins, never a lead ──────────────────────────────
@pytest.mark.parametrize('name', ['Ajay', 'Adam Glyde', 'Damian Hadjinicolaou', 'Sivhoung (Siv) Lim'])
def test_handover_is_classified_before_signal_and_new_lead(name):
    import dsw_sms_poller as p
    kind, got = p.extract_name(sms(name))
    assert kind == 'handover' and got == ho.dd.strip_nick(name)


def test_unrelated_sms_not_handover():
    assert ho.classify_handover('Hi Rob, Kyung Lee has just been assigned to you.') is None
    assert ho.classify_handover('New YES reply from Kyung Lee.') is None


def test_main_routes_handover_without_process_or_contact_lookup(monkeypatch):
    import dsw_sms_poller as p
    called = []
    boom = types.SimpleNamespace(process=lambda *a, **k: called.append('process'))
    monkeypatch.setattr(p, 'get_new_sms', lambda: [(1, sms('Adam Glyde'), 'd'), (2, sms('Ajay'), 'd')])
    monkeypatch.setattr(p, 'load_done', lambda: set())
    monkeypatch.setattr(p, 'save_done', lambda d: called.append(('saved', len(d))))
    monkeypatch.setattr(p.importlib.util, 'spec_from_file_location', lambda *a, **k: types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda m: None)))
    monkeypatch.setattr(p.importlib.util, 'module_from_spec', lambda spec: boom)
    monkeypatch.setattr(p, 'find_pipereply_contact', lambda n: called.append('pipereply') or None)
    monkeypatch.setattr(p, '_raw_fallback_task', lambda *a, **k: called.append('raw'))
    monkeypatch.setattr(p, '_handle_signal', lambda *a, **k: called.append('signal'))
    monkeypatch.setattr(p, '_handle_handover', lambda raw: called.append('handover'))
    p.main()
    assert called == ['handover', 'handover', ('saved', 2)]          # no process / PipeReply / raw fallback / signal


# ── the dry-run scenarios ────────────────────────────────────────────────────
def test_ajay_first_name_only_links_the_one_ajay():
    h = H([task('t-ajay', 'Ajay Sharma', 'completed', 2), task('t-other', 'Bob Smith')])
    r = h.run(sms('Ajay'))
    assert r['action'] == 'create' and r['linked_task_id'] == 't-ajay' and not r['ambiguous']
    (todo,) = h.todos()
    assert todo['title'] == 'Post-sale: complete Sales Handover Form – Ajay Sharma'
    assert todo['lead_status'] == 'post_sale' and todo['category'] != 'DSW Solar' and LINK in todo['description']
    assert todo['due_date'] == '2026-11-01' and todo['due_time'] == '12:00:00' and todo['status'] == 'pending'
    notes = [n for n in h.db.notes() if n['task_id'] == 't-ajay']
    assert len(notes) == 1 and notes[0]['content'] == f'Sale closed, handover form: {LINK}'
    lead = next(t for t in h.db.tasks() if t['id'] == 't-ajay')
    assert lead['status'] == 'completed' and lead['lead_status'] == 'won'      # lead task itself untouched


def test_adam_glyde_matches_worked_task_not_cancelled_duplicates():
    h = H([task('adam-won', 'Adam Glyde', 'completed', 4), task('adam-old', 'Adam Glyde', 'cancelled', 9)])
    r = h.run(sms('Adam Glyde'))
    assert r['linked_task_id'] == 'adam-won' and len(h.todos()) == 1
    assert [t['status'] for t in h.db.tasks() if t['id'].startswith('adam')] == ['completed', 'cancelled']


def test_damian_with_spelling_difference_and_old_task_outside_window():
    h = H([task('dam-old', 'Damian Hadjinicolaou', 'completed', 200), task('dam', 'Damian Hadjinicolau', 'completed', 3)])
    r = h.run(sms('Damian Hadjinicolaou'))
    assert r['linked_task_id'] == 'dam' and len(h.todos()) == 1


def test_repeat_sms_adds_note_never_second_todo():
    h = H([task('adam-won', 'Adam Glyde', 'completed', 4)])
    first = h.run(sms('Adam Glyde'))
    again = h.run(sms('Adam Glyde'))
    assert again['action'] == 'update' and again['task_id'] == first['task_id']
    assert len(h.todos()) == 1 and any('Repeat handover SMS' in n['content'] for n in h.db.notes())


def test_unknown_customer_still_creates_todo_unlinked():
    h = H([])
    r = h.run(sms('Priya Nair'))
    assert r['action'] == 'create' and r['linked_task_id'] is None and len(h.todos()) == 1
    assert 'No matching lead task' in h.todos()[0]['description'] and h.db.notes() == []


def test_ambiguous_first_name_is_not_guessed():
    h = H([task('a', 'Ajay Sharma', 'completed'), task('b', 'Ajay Verma', 'completed')])
    r = h.run(sms('Ajay'))
    assert r['linked_task_id'] is None and r['ambiguous'] and len(h.todos()) == 1 and h.db.notes() == []


def test_lookup_failure_fails_closed_nothing_written():
    h = H([task('adam-won', 'Adam Glyde', 'completed')])
    h.db.fail = True
    with pytest.raises(dd.DedupLookupError):
        h.run(sms('Adam Glyde'))
    h.db.fail = False
    assert h.todos() == [] and h.db.notes() == []


def test_dry_run_writes_nothing():
    h = H([task('adam-won', 'Adam Glyde', 'completed')])
    assert h.run(sms('Adam Glyde'), dry_run=True)['action'] == 'create'
    assert h.todos() == [] and h.db.notes() == []


def test_late_evening_sms_is_due_next_morning():
    h = H([])
    h.run(sms('Priya Nair'))                                   # baseline 11:00 AEST -> due 12:00 today
    late = datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)   # 21:00 AEST
    assert ho._due(late) == ('2026-11-02', '08:00:00')


# ── LEAK CHECKS: post-sale must stay out of the lead pipeline and the lead email ──────────────
def test_todo_is_invisible_to_every_lead_query():
    """Every DSW lead path selects category == 'DSW Solar' (scheduler 24h/3d lead reminders, railway poll dedup keys,
    appt-poll linking, signal dedup, lead-poller migrate guard). The TODO has its own category, so none can see it."""
    h = H([task('adam-won', 'Adam Glyde', 'completed')])
    h.run(sms('Adam Glyde'))
    lead_pool = h.db.table('tasks').eq('category', 'DSW Solar').execute().data
    assert all(t['category'] == 'DSW Solar' and t['lead_status'] != 'post_sale' for t in lead_pool)
    assert dd.find_pending_dsw_task(h.db, ROB, '', '', ['Adam Glyde']) is None          # signal dedup can't match it


def test_find_existing_task_by_client_skips_the_todo():
    """It matches by name across ALL categories and returns the newest - the TODO must not hide the real lead
    (or be noted / superseded as if it were the lead)."""
    import task_manager as tmod
    rows = [{'id': 'todo', 'category': ho.HANDOVER_CATEGORY}, {'id': 'lead', 'category': 'DSW Solar'}]
    assert tmod._first_lead(rows)['id'] == 'lead'
    assert tmod._first_lead([rows[0]]) is None


def test_todo_gets_generic_reminder_not_the_lead_email():
    import saas_scheduler as sch
    t = {'id': 'x', 'title': 'Post-sale: complete Sales Handover Form – Adam Glyde', 'client_name': 'Adam Glyde',
         'priority': 'high', 'category': ho.HANDOVER_CATEGORY, 'lead_status': 'post_sale', 'description': LINK}
    html = sch.generate_reminder_email_html(t, '12:00 PM', 'Rob')
    assert 'NEW LEAD' not in html and 'Lead Status' not in html
    import inspect
    src = inspect.getsource(sch)
    assert "task.get('category') == 'DSW Solar':" in src          # lead template only for DSW Solar
    assert ".eq('category', 'DSW Solar')" in src                  # 24h/3d lead reminders only for DSW Solar


def test_post_sale_has_its_own_badge_not_new_lead():
    import dsw_lead_poller as lp
    assert lp.STATUS_LABELS['post_sale'] != lp.STATUS_LABELS['new_lead']
    assert "'post_sale'" in open('dashboard.py').read()
