-- Jottask Projects Feature Migration
-- Run this in your Supabase SQL Editor
-- Date: January 2026

-- =============================================
-- 1. PROJECTS TABLE
-- =============================================
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Project info
    name TEXT NOT NULL,
    description TEXT,

    -- Status: active, completed, archived
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'archived')),

    -- For multi-tenant (future SaaS)
    business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Index for business lookups
CREATE INDEX IF NOT EXISTS idx_projects_business_id ON projects(business_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name);

-- =============================================
-- 2. PROJECT ITEMS (To-Do Checkboxes)
-- =============================================
CREATE TABLE IF NOT EXISTS project_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    -- Item content
    item_text TEXT NOT NULL,

    -- Completion status
    is_completed BOOLEAN DEFAULT false,
    completed_at TIMESTAMPTZ,

    -- Ordering
    display_order INTEGER DEFAULT 0,

    -- Source tracking
    source TEXT DEFAULT 'email',  -- 'email', 'manual', 'api'
    source_email_subject TEXT,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for project items
CREATE INDEX IF NOT EXISTS idx_project_items_project_id ON project_items(project_id);
CREATE INDEX IF NOT EXISTS idx_project_items_completed ON project_items(is_completed);

-- =============================================
-- 3. HELPER FUNCTIONS
-- =============================================

-- Function to get project progress (completed / total items)
CREATE OR REPLACE FUNCTION get_project_progress(p_project_id UUID)
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
    FROM project_items
    WHERE project_id = p_project_id;
END;
$$ LANGUAGE plpgsql;

-- =============================================
-- 4. ROW LEVEL SECURITY (for future SaaS)
-- =============================================

-- Enable RLS on projects
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_items ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see their business's projects
-- (Disabled for now - enable when multi-tenant is ready)
-- CREATE POLICY "Users can view own business projects" ON projects
--     FOR SELECT USING (business_id IN (
--         SELECT business_id FROM users WHERE id = auth.uid()
--     ));

-- For now, allow all access (single-tenant mode)
CREATE POLICY "Allow all access to projects" ON projects FOR ALL USING (true);
CREATE POLICY "Allow all access to project_items" ON project_items FOR ALL USING (true);

-- =============================================
-- 5. SAMPLE DATA (Optional - for testing)
-- =============================================
-- Uncomment to create a test project:
-- INSERT INTO projects (name, description, business_id)
-- VALUES ('Test Project', 'A test project', 'feb14276-5c3d-4fcf-af06-9a8f54cf7159');
