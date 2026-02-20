-- Migration 014: CRM Connections table
-- Run in Supabase SQL Editor
-- Phase 3: CRM Connector Framework

-- Create crm_connections table
CREATE TABLE IF NOT EXISTS crm_connections (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('pipereply', 'hubspot', 'zoho', 'salesforce', 'none')),
    display_name TEXT,

    -- Auth credentials (varies by provider)
    api_key TEXT,
    api_base_url TEXT,
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at TIMESTAMPTZ,

    -- Connection state
    is_active BOOLEAN DEFAULT false,
    connection_status TEXT DEFAULT 'pending' CHECK (connection_status IN ('pending', 'connected', 'error', 'disconnected')),

    -- Config
    field_mapping JSONB DEFAULT '{}'::jsonb,
    settings JSONB DEFAULT '{}'::jsonb,

    -- Tracking
    last_sync_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    -- One connection per CRM per user
    UNIQUE(user_id, provider)
);

-- RLS
ALTER TABLE crm_connections ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own CRM connections"
    ON crm_connections FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own CRM connections"
    ON crm_connections FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own CRM connections"
    ON crm_connections FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own CRM connections"
    ON crm_connections FOR DELETE
    USING (auth.uid() = user_id);

-- Service role bypass (for email processor / scheduler)
CREATE POLICY "Service role full access on crm_connections"
    ON crm_connections FOR ALL
    USING (auth.role() = 'service_role');

-- Indexes
CREATE INDEX idx_crm_connections_user_id ON crm_connections(user_id);
CREATE INDEX idx_crm_connections_provider ON crm_connections(provider);
CREATE INDEX idx_crm_connections_active ON crm_connections(user_id, is_active) WHERE is_active = true;

-- Seed Rob's PipeReply connection (inactive until he enters API key)
INSERT INTO crm_connections (user_id, provider, display_name, connection_status, is_active)
VALUES (
    'e515407e-dbd6-4331-a815-1878815c89bc',
    'pipereply',
    'PipeReply CRM',
    'pending',
    false
)
ON CONFLICT (user_id, provider) DO UPDATE SET
    display_name = EXCLUDED.display_name;
