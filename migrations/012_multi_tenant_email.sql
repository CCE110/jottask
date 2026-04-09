-- Migration 012: Multi-tenant email processing support
-- Run this in Supabase SQL Editor
-- All ADD COLUMN IF NOT EXISTS — safe to re-run

-- =============================================
-- 1. processed_emails: add connection_id and user_id for per-connection dedup
-- =============================================
ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS uid TEXT;
ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS connection_id UUID REFERENCES email_connections(id) ON DELETE SET NULL;
ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_processed_emails_connection ON processed_emails(connection_id);
CREATE INDEX IF NOT EXISTS idx_processed_emails_user ON processed_emails(user_id);

-- =============================================
-- 2. email_connections: add imap_server and use_env_credentials
-- =============================================
ALTER TABLE email_connections ADD COLUMN IF NOT EXISTS imap_server TEXT DEFAULT 'imap.gmail.com';
ALTER TABLE email_connections ADD COLUMN IF NOT EXISTS use_env_credentials BOOLEAN DEFAULT false;

-- =============================================
-- 3. users: add ai_context JSONB for per-user AI prompt customization
-- =============================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_context JSONB;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tasks_this_month INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tasks_month_reset TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_summary_enabled BOOLEAN DEFAULT true;

-- Done!
SELECT 'Migration 012: Multi-tenant email columns added' AS status;
