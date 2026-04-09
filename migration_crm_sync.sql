-- Migration: Add CRM sync tracking to pending_actions table
-- Run this in your Supabase SQL editor at https://supabase.com/dashboard
-- This lets the sync-crm shortcut track which approved CRM updates
-- have been pushed to PipeReply CRM

ALTER TABLE pending_actions ADD COLUMN IF NOT EXISTS crm_synced BOOLEAN DEFAULT false;
ALTER TABLE pending_actions ADD COLUMN IF NOT EXISTS crm_synced_at TIMESTAMPTZ;

-- Index for fast lookup of un-synced approved CRM updates
CREATE INDEX IF NOT EXISTS idx_pending_actions_crm_sync
ON pending_actions(action_type, status, crm_synced)
WHERE action_type = 'update_crm' AND status = 'approved' AND (crm_synced IS NULL OR crm_synced = false);
