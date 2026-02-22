-- Migration 015: Add ai_context column to users table
-- The email processor joins email_connections → users and reads ai_context
-- for per-user AI prompt configuration (businesses, categories, etc.)

ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_context JSONB DEFAULT '{}';

COMMENT ON COLUMN users.ai_context IS 'Per-user AI configuration: businesses, categories, default_business, custom prompts';

-- Seed Rob's ai_context with DSW defaults
UPDATE users
SET ai_context = jsonb_build_object(
    'default_business', 'Cloud Clean Energy',
    'businesses', jsonb_build_object(
        'Cloud Clean Energy', 'feb14276-5c3d-4fcf-af06-9a8f54cf7159',
        'AIPP', 'ec5d7aab-8d74-4ef2-9d92-01b143c68c82'
    ),
    'user_name', full_name
)
WHERE ai_context IS NULL OR ai_context = '{}'::jsonb;
