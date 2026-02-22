-- Migration 018: System Monitoring
-- Creates system_events table for tracking emails, heartbeats, errors
-- Adds alert throttling columns to users table

-- ============================================
-- system_events table
-- ============================================
CREATE TABLE IF NOT EXISTS system_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    event_type TEXT NOT NULL,           -- 'email_sent', 'email_failed', 'heartbeat', 'error', 'alert_sent'
    category TEXT,                       -- 'reminder', 'summary', 'confirmation', 'approval', 'system'
    status TEXT DEFAULT 'info',          -- 'info', 'success', 'warning', 'error'
    message TEXT,                        -- Human-readable description
    error_detail TEXT,                   -- Full traceback or error message
    metadata JSONB DEFAULT '{}',         -- Flexible data: user_id, task_id, to_email, etc.
    user_id UUID REFERENCES users(id),   -- Optional: which user this relates to
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes for efficient querying
CREATE INDEX idx_system_events_type_created ON system_events (event_type, created_at DESC);
CREATE INDEX idx_system_events_status_created ON system_events (status, created_at DESC);
CREATE INDEX idx_system_events_created ON system_events (created_at DESC);

-- ============================================
-- Alert throttling columns on users table
-- ============================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_system_alert_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS system_alert_count_today INTEGER DEFAULT 0;

-- ============================================
-- RLS: Allow service_role full access (no user-level RLS needed for system table)
-- ============================================
ALTER TABLE system_events ENABLE ROW LEVEL SECURITY;

-- Service role can do everything
CREATE POLICY "Service role full access on system_events"
    ON system_events
    FOR ALL
    USING (true)
    WITH CHECK (true);
