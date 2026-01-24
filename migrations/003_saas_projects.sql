-- Jottask SaaS Projects Feature Migration
-- Run this in your Supabase SQL Editor
-- Date: January 2026

-- =============================================
-- 1. SAAS_PROJECTS TABLE (User-based, not business-based)
-- =============================================
CREATE TABLE IF NOT EXISTS saas_projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- Project info
    name TEXT NOT NULL,
    description TEXT,
    color TEXT DEFAULT '#6366F1',  -- Default indigo

    -- Status: active, completed, archived
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'archived')),

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_saas_projects_user_id ON saas_projects(user_id);
CREATE INDEX IF NOT EXISTS idx_saas_projects_status ON saas_projects(status);
CREATE INDEX IF NOT EXISTS idx_saas_projects_name ON saas_projects(name);

-- =============================================
-- 2. SAAS_PROJECT_ITEMS (Checklist items)
-- =============================================
CREATE TABLE IF NOT EXISTS saas_project_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES saas_projects(id) ON DELETE CASCADE,

    -- Item content
    item_text TEXT NOT NULL,

    -- Completion status
    is_completed BOOLEAN DEFAULT false,
    completed_at TIMESTAMPTZ,

    -- Ordering
    display_order INTEGER DEFAULT 0,

    -- Source tracking
    source TEXT DEFAULT 'manual',  -- 'email', 'manual', 'api'
    source_email_subject TEXT,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_saas_project_items_project_id ON saas_project_items(project_id);
CREATE INDEX IF NOT EXISTS idx_saas_project_items_completed ON saas_project_items(is_completed);

-- =============================================
-- 3. ADD DAILY SUMMARY COLUMNS TO USERS TABLE
-- =============================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_summary_enabled BOOLEAN DEFAULT true;
ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_summary_time TIME DEFAULT '08:00:00';
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_summary_sent_at TIMESTAMPTZ;

-- =============================================
-- 4. ROW LEVEL SECURITY
-- =============================================

-- Enable RLS
ALTER TABLE saas_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas_project_items ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (for re-running migration)
DROP POLICY IF EXISTS "Users can view own projects" ON saas_projects;
DROP POLICY IF EXISTS "Users can insert own projects" ON saas_projects;
DROP POLICY IF EXISTS "Users can update own projects" ON saas_projects;
DROP POLICY IF EXISTS "Users can delete own projects" ON saas_projects;

DROP POLICY IF EXISTS "Users can view own project items" ON saas_project_items;
DROP POLICY IF EXISTS "Users can insert own project items" ON saas_project_items;
DROP POLICY IF EXISTS "Users can update own project items" ON saas_project_items;
DROP POLICY IF EXISTS "Users can delete own project items" ON saas_project_items;

-- Policies for saas_projects
CREATE POLICY "Users can view own projects" ON saas_projects
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can insert own projects" ON saas_projects
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update own projects" ON saas_projects
    FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY "Users can delete own projects" ON saas_projects
    FOR DELETE USING (user_id = auth.uid());

-- Policies for saas_project_items (through project ownership)
CREATE POLICY "Users can view own project items" ON saas_project_items
    FOR SELECT USING (
        project_id IN (SELECT id FROM saas_projects WHERE user_id = auth.uid())
    );

CREATE POLICY "Users can insert own project items" ON saas_project_items
    FOR INSERT WITH CHECK (
        project_id IN (SELECT id FROM saas_projects WHERE user_id = auth.uid())
    );

CREATE POLICY "Users can update own project items" ON saas_project_items
    FOR UPDATE USING (
        project_id IN (SELECT id FROM saas_projects WHERE user_id = auth.uid())
    );

CREATE POLICY "Users can delete own project items" ON saas_project_items
    FOR DELETE USING (
        project_id IN (SELECT id FROM saas_projects WHERE user_id = auth.uid())
    );

-- =============================================
-- 5. SERVICE ROLE BYPASS (for email processor)
-- =============================================
-- The service role key bypasses RLS automatically
-- No additional policies needed for backend services

-- =============================================
-- 6. HELPER FUNCTION: Get project progress
-- =============================================
CREATE OR REPLACE FUNCTION get_saas_project_progress(p_project_id UUID)
RETURNS TABLE(total_items INTEGER, completed_items INTEGER, progress_percent INTEGER) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::INTEGER as total_items,
        COUNT(*) FILTER (WHERE is_completed = true)::INTEGER as completed_items,
        CASE
            WHEN COUNT(*) = 0 THEN 0
            ELSE (COUNT(*) FILTER (WHERE is_completed = true) * 100 / COUNT(*))::INTEGER
        END as progress_percent
    FROM saas_project_items
    WHERE project_id = p_project_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
