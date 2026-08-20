-- =============================================================================
-- Migration 033: Add 'ongoing' to tasks_status_check
--
-- Context: the task-edit UI has an "Ongoing" status button
-- (templates/task_edit.html:80, setStatus('ongoing') in
-- templates/task_edit.html:320-325), and the server route
-- (dashboard.py:3898-3917) writes whatever status the client sends.
-- Multiple render + query paths in dashboard.py (2244-2248, 2455, 3913)
-- and templates/dashboard.html (109, 115, 142, 148, 175, 181) also
-- treat 'ongoing' as a valid state.
--
-- Pre-migration constraint (empirically confirmed 2026-08-20 by probing
-- every candidate value against the live constraint):
--   CHECK (status IN ('pending', 'completed', 'cancelled', 'in_progress'))
-- Post-migration constraint (this file adds 'ongoing', preserves
-- 'in_progress' as a belt-and-braces backwards-compat guard for any
-- future admin scripts / external integrations that might use the
-- more standard 'in_progress' name):
--   CHECK (status IN ('pending', 'ongoing', 'completed', 'cancelled', 'in_progress'))
--
-- Every UPDATE from the UI writes 'ongoing', which bounced with 23514
-- and rolled the row back. Zero rows had ever landed with status='ongoing'
-- (2,432 total: 2,311 completed / 71 pending / 50 cancelled / 0 in_progress
--  / 0 ongoing at migration time).
--
-- Failing case that surfaced this: task 2dfaa827 (Peter Griffin) —
-- Rob clicked the Ongoing button, the write bounced off the check,
-- the row stayed at status='completed'.
--
-- Fix: widen the CHECK to include 'ongoing'. Pre-existing rows all
-- satisfy the new constraint, so ADD CONSTRAINT will not fail on
-- existing data. Zero-downtime, no data migration needed.
-- =============================================================================

BEGIN;

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;

ALTER TABLE tasks ADD CONSTRAINT tasks_status_check
    CHECK (status IN ('pending', 'ongoing', 'completed', 'cancelled', 'in_progress'));

-- Verify no existing rows violate (belt-and-braces — should be zero):
DO $$
DECLARE
    bad_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO bad_count FROM tasks
     WHERE status NOT IN ('pending', 'ongoing', 'completed', 'cancelled', 'in_progress');
    IF bad_count > 0 THEN
        RAISE EXCEPTION 'Migration 033 aborted: % task row(s) hold a status outside the new CHECK set', bad_count;
    END IF;
END $$;

COMMIT;

-- To roll back to the pre-033 CHECK:
--   ALTER TABLE tasks DROP CONSTRAINT tasks_status_check;
--   ALTER TABLE tasks ADD CONSTRAINT tasks_status_check
--       CHECK (status IN ('pending', 'completed', 'cancelled', 'in_progress'));
-- (Requires no row currently holds status='ongoing'. Post-033, the UI can
--  write 'ongoing', so check `SELECT COUNT(*) FROM tasks WHERE status='ongoing'`
--  before rolling back.)
