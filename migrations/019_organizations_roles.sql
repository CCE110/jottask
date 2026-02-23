-- Migration 019: Organizations and Role-Based Access
-- Adds multi-tenant org layer and 3-tier role system

-- 1. Organizations table
CREATE TABLE IF NOT EXISTS organizations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE,
    owner_id UUID REFERENCES users(id),
    ai_context JSONB DEFAULT '{}',
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Add role and organization_id to users
ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user';
ALTER TABLE users ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id);

-- 3. Indexes
CREATE INDEX IF NOT EXISTS idx_users_organization ON users(organization_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_organizations_owner ON organizations(owner_id);
CREATE INDEX IF NOT EXISTS idx_organizations_slug ON organizations(slug);

-- 4. Seed: Create DSW organization from Rob's existing ai_context
INSERT INTO organizations (name, slug, owner_id, ai_context)
SELECT
    'Direct Solar Wholesalers',
    'direct-solar-wholesalers',
    id,
    COALESCE(ai_context, '{}'::jsonb)
FROM users
WHERE id = 'e515407e-dbd6-4331-a815-1878815c89bc'
ON CONFLICT (slug) DO NOTHING;

-- 5. Set Rob as global_admin and link to DSW org
UPDATE users SET
    role = 'global_admin',
    organization_id = (SELECT id FROM organizations WHERE slug = 'direct-solar-wholesalers')
WHERE id = 'e515407e-dbd6-4331-a815-1878815c89bc';

-- 6. RLS for organizations
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access on organizations"
    ON organizations FOR ALL
    USING (true)
    WITH CHECK (true);
