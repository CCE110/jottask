-- Migration 017: Extend processed_emails for email history tracking
-- Adds sender info and contact linking for "show all emails from this customer"
-- Run in Supabase SQL Editor

ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS sender_email TEXT;
ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS sender_name TEXT;
ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS subject TEXT;
ALTER TABLE processed_emails ADD COLUMN IF NOT EXISTS contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_processed_emails_sender ON processed_emails(lower(sender_email));
CREATE INDEX IF NOT EXISTS idx_processed_emails_contact ON processed_emails(contact_id);

SELECT 'Migration 017: Email history columns added to processed_emails' AS status;
