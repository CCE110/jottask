-- Migration 013: Seed Rob's email connection and AI context
-- Run this in Supabase SQL Editor AFTER migration 012
-- This activates the multi-tenant code path for Rob's inbox.
-- SAFETY: If anything breaks, DELETE this row to revert to env-var fallback instantly:
--   DELETE FROM email_connections WHERE email_address = 'jottask@flowquote.ai';

-- =============================================
-- 1. Insert Rob's IMAP connection (uses env vars for credentials)
-- =============================================
INSERT INTO email_connections (
    user_id,
    provider,
    email_address,
    imap_server,
    use_env_credentials,
    is_active,
    sync_frequency_minutes
)
VALUES (
    'e515407e-dbd6-4331-a815-1878815c89bc',  -- Rob's user_id
    'imap',
    'jottask@flowquote.ai',
    'mail.privateemail.com',
    true,    -- Use JOTTASK_EMAIL / JOTTASK_EMAIL_PASSWORD env vars
    true,
    15       -- Poll every 15 minutes
)
ON CONFLICT (user_id, email_address) DO UPDATE SET
    imap_server = EXCLUDED.imap_server,
    use_env_credentials = EXCLUDED.use_env_credentials,
    is_active = EXCLUDED.is_active,
    sync_frequency_minutes = EXCLUDED.sync_frequency_minutes;

-- =============================================
-- 2. Set Rob's AI context (per-user prompt customization)
-- =============================================
UPDATE users
SET ai_context = '{
    "company_name": "Direct Solar Wholesalers (DSW)",
    "role_description": "a solar & battery sales engineer at Direct Solar Wholesalers (DSW), QLD Australia. He sells residential solar panel + battery systems (GoodWe, SolaX brands)",
    "crm_name": "PipeReply",
    "workflow": "Lead → Scoping Call → Quote (OpenSolar) → Price (DSW Tool) → Send Proposal → Follow Up → Close",
    "default_business": "Cloud Clean Energy",
    "businesses": {
        "Cloud Clean Energy": "feb14276-5c3d-4fcf-af06-9a8f54cf7159",
        "AI Project Pro": "ec5d7aab-8d74-4ef2-9d92-01b143c68c82"
    },
    "categories": ["Remember to Callback", "Quote Follow Up", "CRM Update", "Site Visit", "New Lead", "Research", "General"],
    "extra_context": "- Quoting: OpenSolar (app.opensolar.com) + DSW Quoting Tool (dswenergygroup.com.au)\n- Common email types: New lead notifications from DSW, customer replies to quotes, supplier/installer comms, SolarQuotes lead assignments\n- Personal CRM: Jottask (jottask.app)"
}'::jsonb
WHERE id = 'e515407e-dbd6-4331-a815-1878815c89bc';

-- Done!
SELECT 'Migration 013: Rob''s connection seeded and AI context set' AS status;
