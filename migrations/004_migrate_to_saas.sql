-- Migration: Move data from Rob CRM (old) to Jottask SaaS (new)
-- Run this in Supabase SQL Editor
-- =====================================================

-- STEP 1: First, get your user_id from the users table
-- Run this query first to find your user_id:
-- SELECT id, email, full_name FROM users;
-- Then replace 'YOUR_USER_ID_HERE' below with your actual user_id

-- Set your user_id here (get it from the query above)
DO $$
DECLARE
    target_user_id UUID := 'YOUR_USER_ID_HERE';  -- REPLACE THIS!
    old_business_id UUID := 'feb14276-5c3d-4fcf-af06-9a8f54cf7159';  -- Cloud Clean Energy
    project_record RECORD;
    new_project_id UUID;
BEGIN
    -- Verify user exists
    IF NOT EXISTS (SELECT 1 FROM users WHERE id = target_user_id) THEN
        RAISE EXCEPTION 'User not found. Please check the user_id.';
    END IF;

    RAISE NOTICE 'Starting migration for user: %', target_user_id;

    -- =====================================================
    -- MIGRATE PROJECTS
    -- =====================================================
    FOR project_record IN
        SELECT * FROM projects
        WHERE business_id = old_business_id
        AND id NOT IN (SELECT id FROM saas_projects)  -- Skip if already migrated
    LOOP
        -- Insert into saas_projects with same ID to preserve links
        INSERT INTO saas_projects (
            id, user_id, name, description, color, status,
            created_at, updated_at, completed_at
        ) VALUES (
            project_record.id,
            target_user_id,
            project_record.name,
            project_record.description,
            COALESCE(project_record.color, '#6366F1'),
            COALESCE(project_record.status, 'active'),
            project_record.created_at,
            project_record.updated_at,
            project_record.completed_at
        ) ON CONFLICT (id) DO NOTHING;

        RAISE NOTICE 'Migrated project: %', project_record.name;
    END LOOP;

    -- =====================================================
    -- MIGRATE PROJECT ITEMS
    -- =====================================================
    INSERT INTO saas_project_items (
        id, project_id, item_text, is_completed, completed_at,
        display_order, source, created_at
    )
    SELECT
        pi.id,
        pi.project_id,
        pi.item_text,
        pi.is_completed,
        pi.completed_at,
        pi.display_order,
        COALESCE(pi.source, 'email'),
        pi.created_at
    FROM project_items pi
    INNER JOIN saas_projects sp ON sp.id = pi.project_id
    WHERE pi.id NOT IN (SELECT id FROM saas_project_items)  -- Skip duplicates
    ON CONFLICT (id) DO NOTHING;

    RAISE NOTICE 'Migrated project items';

    -- =====================================================
    -- UPDATE TASKS with user_id (if not already set)
    -- =====================================================
    UPDATE tasks
    SET user_id = target_user_id
    WHERE business_id = old_business_id
    AND (user_id IS NULL OR user_id != target_user_id);

    RAISE NOTICE 'Updated tasks with user_id';

    RAISE NOTICE 'Migration complete!';
END $$;

-- =====================================================
-- VERIFICATION QUERIES (run after migration)
-- =====================================================

-- Check migrated projects
-- SELECT id, name, status, created_at FROM saas_projects ORDER BY created_at DESC;

-- Check migrated project items
-- SELECT sp.name as project, spi.item_text, spi.is_completed
-- FROM saas_project_items spi
-- JOIN saas_projects sp ON sp.id = spi.project_id;

-- Check tasks
-- SELECT id, title, status, user_id FROM tasks WHERE user_id IS NOT NULL LIMIT 10;
