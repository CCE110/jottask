-- Migration 016: Contacts table + link tasks to contacts
-- Run in Supabase SQL Editor

-- =============================================
-- 1. Create contacts table
-- =============================================
CREATE TABLE IF NOT EXISTS contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT,
    email TEXT,
    phone TEXT,
    company TEXT,
    source TEXT DEFAULT 'email',  -- email, manual, crm_sync
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Unique constraint: one contact per email per user
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_user_email
    ON contacts(user_id, lower(email))
    WHERE email IS NOT NULL AND email != '';

CREATE INDEX IF NOT EXISTS idx_contacts_user ON contacts(user_id);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(lower(email));
CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(user_id, lower(name));

-- RLS
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own contacts"
    ON contacts FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can manage own contacts"
    ON contacts FOR ALL USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- Service role bypass (for email processor)
CREATE POLICY "Service can manage all contacts"
    ON contacts FOR ALL USING (true) WITH CHECK (true);

-- =============================================
-- 2. Add contact_id FK to tasks table
-- =============================================
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_contact ON tasks(contact_id);

-- =============================================
-- 3. Ensure client_email column exists on tasks (may already exist)
-- =============================================
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS client_email TEXT;
CREATE INDEX IF NOT EXISTS idx_tasks_client_email ON tasks(lower(client_email));

-- =============================================
-- 4. Backfill contacts from existing task data
-- =============================================
INSERT INTO contacts (user_id, name, email, source)
SELECT DISTINCT ON (t.user_id, lower(t.client_email))
    t.user_id,
    t.client_name,
    lower(t.client_email),
    'backfill'
FROM tasks t
WHERE t.client_email IS NOT NULL
  AND t.client_email != ''
ON CONFLICT (user_id, lower(email)) DO NOTHING;

-- Link existing tasks to their contacts
UPDATE tasks t
SET contact_id = c.id
FROM contacts c
WHERE lower(t.client_email) = lower(c.email)
  AND t.user_id = c.user_id
  AND t.contact_id IS NULL
  AND t.client_email IS NOT NULL
  AND t.client_email != '';

SELECT 'Migration 016: Contacts table created and backfilled' AS status;
