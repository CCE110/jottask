-- Add reminder tracking to prevent duplicate reminder emails
-- Run this in Supabase SQL Editor

-- Add column to track when reminder was sent
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMPTZ;

-- Index for efficient queries
CREATE INDEX IF NOT EXISTS idx_tasks_reminder_sent ON tasks(reminder_sent_at);
