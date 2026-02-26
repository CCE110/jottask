-- Migration 020: Add outcome tracking to processed_emails
-- Tracks what happened when each email was processed (task created, no action, error, etc.)
-- Run in Supabase SQL Editor

ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS outcome TEXT;
ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS outcome_detail TEXT;

-- outcome values: 'task_created', 'note_added', 'approval_queued', 'no_action', 'opensolar', 'error'
-- outcome_detail: AI summary or error message (first 500 chars)

CREATE INDEX IF NOT EXISTS idx_processed_emails_outcome ON processed_emails(outcome);

SELECT 'Migration 020: outcome tracking columns added to processed_emails' AS status;
