"""In-memory Supabase fake that ENFORCES the real table columns.

A column that does not exist in production raises, exactly like PostgREST (42703), so a handler that
writes e.g. task_notes.author can no longer pass tests (it did once: handle_dsw_task_assigned).
Column lists copied from the live schema on 2026-10-29 (select * on a real row).
"""
import types
from datetime import datetime, timezone

import task_manager
_RealTaskManager = task_manager.TaskManager  # captured before tests monkeypatch the attribute

SCHEMA = {
    'tasks': {'business_id', 'category', 'client_email', 'client_name', 'client_phone', 'company_id', 'completed_at',
              'contact_id', 'created_at', 'description', 'due_date', 'due_time', 'id', 'is_meeting', 'lead_status',
              'lost_reason', 'metadata', 'opportunity_id', 'priority', 'project_name', 'project_status_id',
              'reminder_sent_at', 'status', 'tags', 'title', 'updated_at', 'user_id'},
    'task_notes': {'content', 'created_at', 'created_by', 'id', 'source', 'source_email_date', 'source_email_from',
                   'source_email_subject', 'task_id'},
    'users': {'id', 'email', 'full_name', 'timezone'},
}


class UndefinedColumn(Exception):
    pass


class FakeQuery:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters, self._limit, self._op, self._payload = [], None, 'select', None
        self._cols = SCHEMA.get(name)

    def _check(self, cols):
        if self._cols is None:
            return
        bad = [c for c in cols if c not in self._cols]
        if bad:
            raise UndefinedColumn(f"column {self.name}.{bad[0]} does not exist (42703)")

    def select(self, cols='*', *_a, **_k):
        if cols != '*':
            self._check([c.strip() for c in cols.split(',')])
        return self

    def eq(self, c, v): self._check([c]); self.filters.append(lambda r: r.get(c) == v); return self
    def gte(self, c, v): self._check([c]); self.filters.append(lambda r: (r.get(c) or '') >= v); return self
    def order(self, c, *_a, **_k): self._check([c]); return self
    def limit(self, n): self._limit = n; return self

    def ilike(self, c, pat):
        self._check([c])
        p, wild = pat.lower().strip('%'), pat.startswith('%')
        self.filters.append(lambda r: (p in (r.get(c) or '').lower()) if wild else (p == (r.get(c) or '').lower()))
        return self

    def or_(self, expr):
        conds = [part.split('.', 2) for part in expr.split(',')]
        self._check([c for c, _, _ in conds])
        self.filters.append(lambda r: any(
            (op == 'eq' and (r.get(c) or '') == v) or (op == 'ilike' and (r.get(c) or '').lower() == v.lower())
            for c, op, v in conds))
        return self

    def insert(self, payload): self._check(payload.keys()); self._op, self._payload = 'insert', payload; return self
    def update(self, payload): self._check(payload.keys()); self._op, self._payload = 'update', payload; return self

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
    def __init__(self, rob='rob-uid'):
        self.tables = {'users': [{'id': rob, 'email': 'rob@cloudcleanenergy.com.au'}]}
        self.fail = False

    def table(self, name): return FakeQuery(self, name)

    def tasks(self): return self.tables.get('tasks', [])

    def notes(self): return self.tables.get('task_notes', [])


def fake_task_manager(db):
    """The REAL TaskManager (so add_note's column set is exercised) pointed at the fake DB."""
    class _TM(_RealTaskManager):
        def __init__(self):
            self._statuses_loaded, self.statuses = True, []

        @property
        def supabase(self):
            return db
    return _TM()
