-- Migration: Add pending_actions table for Tier 2 approval flow
-- Run this in your Supabase SQL editor

CREATE TABLE IF NOT EXISTS pending_actions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    action_type TEXT NOT NULL,
    action_data JSONB NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'failed', 'expired')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    user_id UUID REFERENCES auth.users(id)
);

-- Index for fast token lookups
CREATE INDEX idx_pending_actions_token ON pending_actions(token);
CREATE INDEX idx_pending_actions_status ON pending_actions(status);

-- RLS policy
ALTER TABLE pending_actions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own pending actions"
    ON pending_actions FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "System can insert pending actions"
    ON pending_actions FOR INSERT
    WITH CHECK (true);

CREATE POLICY "System can update pending actions"
    ON pending_actions FOR UPDATE
    USING (true);
