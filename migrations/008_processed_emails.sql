-- Track processed emails to prevent duplicate processing
-- Run this in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS processed_emails (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email_id TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_processed_emails_email_id ON processed_emails(email_id);

-- RLS policy (allow service role full access)
ALTER TABLE processed_emails ENABLE ROW LEVEL SECURITY;

-- Allow inserts and selects for authenticated users (service role bypasses RLS)
CREATE POLICY "Service can manage processed_emails" ON processed_emails
    FOR ALL USING (true) WITH CHECK (true);
