-- =============================================================================
-- Migration 024: Squad — youth soccer team management tables
-- Safe to run on a live database. Fully idempotent — will not error if tables,
-- columns, constraints, policies, or indexes already exist.
-- =============================================================================


-- ── 1. squads ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.squads (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manager_user_id  UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    season           TEXT,
    cal_token        TEXT UNIQUE DEFAULT encode(gen_random_bytes(16), 'hex'),
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Add any missing columns to existing table
ALTER TABLE public.squads ADD COLUMN IF NOT EXISTS season    TEXT;
ALTER TABLE public.squads ADD COLUMN IF NOT EXISTS cal_token TEXT;
ALTER TABLE public.squads ADD COLUMN IF NOT EXISTS notes     TEXT;

-- Ensure cal_token is populated for any rows that have none
UPDATE public.squads SET cal_token = encode(gen_random_bytes(16), 'hex') WHERE cal_token IS NULL;

ALTER TABLE public.squads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "squad_manager_all" ON public.squads;
CREATE POLICY "squad_manager_all" ON public.squads
    FOR ALL USING (manager_user_id = auth.uid());


-- ── 2. squad_players ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.squad_players (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    squad_id      UUID NOT NULL REFERENCES public.squads(id) ON DELETE CASCADE,
    player_name   TEXT NOT NULL,
    shirt_number  INTEGER,
    position      TEXT,
    date_of_birth DATE,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.squad_players ADD COLUMN IF NOT EXISTS shirt_number  INTEGER;
ALTER TABLE public.squad_players ADD COLUMN IF NOT EXISTS position      TEXT;
ALTER TABLE public.squad_players ADD COLUMN IF NOT EXISTS date_of_birth DATE;
ALTER TABLE public.squad_players ADD COLUMN IF NOT EXISTS notes         TEXT;

ALTER TABLE public.squad_players ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "squad_players_manager_all" ON public.squad_players;
CREATE POLICY "squad_players_manager_all" ON public.squad_players
    FOR ALL USING (
        squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid())
    );


-- ── 3. squad_parent_links ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.squad_parent_links (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    squad_id      UUID NOT NULL REFERENCES public.squads(id) ON DELETE CASCADE,
    player_id     UUID REFERENCES public.squad_players(id) ON DELETE SET NULL,
    parent_name   TEXT,
    parent_email  TEXT,
    magic_token   TEXT NOT NULL DEFAULT encode(gen_random_bytes(24), 'hex'),
    is_active     BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.squad_parent_links ADD COLUMN IF NOT EXISTS player_id    UUID;
ALTER TABLE public.squad_parent_links ADD COLUMN IF NOT EXISTS parent_name  TEXT;
ALTER TABLE public.squad_parent_links ADD COLUMN IF NOT EXISTS parent_email TEXT;
ALTER TABLE public.squad_parent_links ADD COLUMN IF NOT EXISTS is_active    BOOLEAN NOT NULL DEFAULT true;

-- Add unique index on magic_token if not already there
CREATE UNIQUE INDEX IF NOT EXISTS idx_squad_parent_links_token_unique
    ON public.squad_parent_links(magic_token);

ALTER TABLE public.squad_parent_links ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "squad_parent_links_manager_all" ON public.squad_parent_links;
CREATE POLICY "squad_parent_links_manager_all" ON public.squad_parent_links
    FOR ALL USING (
        squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid())
    );


-- ── 4. squad_email_inbox ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.squad_email_inbox (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    squad_id         UUID,
    email_from       TEXT,
    email_subject    TEXT,
    email_body       TEXT,
    email_date       TEXT,
    email_hash       TEXT NOT NULL,
    email_type       TEXT NOT NULL DEFAULT 'club_update',
    parsed_data      JSONB,
    status           TEXT NOT NULL DEFAULT 'pending',
    approved_at      TIMESTAMPTZ,
    dismissed_at     TIMESTAMPTZ,
    actions_executed JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS squad_id         UUID;
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS email_from       TEXT;
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS email_subject    TEXT;
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS email_body       TEXT;
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS email_date       TEXT;
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS email_hash       TEXT;
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS email_type       TEXT NOT NULL DEFAULT 'club_update';
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS parsed_data      JSONB;
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS approved_at      TIMESTAMPTZ;
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS dismissed_at     TIMESTAMPTZ;
ALTER TABLE public.squad_email_inbox ADD COLUMN IF NOT EXISTS actions_executed JSONB;

-- Add unique index on email_hash for dedup (only if column exists and is populated)
CREATE UNIQUE INDEX IF NOT EXISTS idx_squad_email_inbox_hash_unique
    ON public.squad_email_inbox(email_hash) WHERE email_hash IS NOT NULL;

ALTER TABLE public.squad_email_inbox ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "squad_inbox_manager_all" ON public.squad_email_inbox;
CREATE POLICY "squad_inbox_manager_all" ON public.squad_email_inbox
    FOR ALL USING (
        squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid())
        OR squad_id IS NULL
    );


-- ── 5. squad_events ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.squad_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    squad_id        UUID NOT NULL REFERENCES public.squads(id) ON DELETE CASCADE,
    event_date      DATE NOT NULL,
    event_time      TIME,
    opponent        TEXT,
    venue           TEXT,
    is_home         BOOLEAN,
    event_type      TEXT NOT NULL DEFAULT 'game',
    round           TEXT,
    notes           TEXT,
    is_cancelled    BOOLEAN NOT NULL DEFAULT false,
    source_inbox_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.squad_events ADD COLUMN IF NOT EXISTS event_time      TIME;
ALTER TABLE public.squad_events ADD COLUMN IF NOT EXISTS opponent        TEXT;
ALTER TABLE public.squad_events ADD COLUMN IF NOT EXISTS venue           TEXT;
ALTER TABLE public.squad_events ADD COLUMN IF NOT EXISTS is_home         BOOLEAN;
ALTER TABLE public.squad_events ADD COLUMN IF NOT EXISTS event_type      TEXT NOT NULL DEFAULT 'game';
ALTER TABLE public.squad_events ADD COLUMN IF NOT EXISTS round           TEXT;
ALTER TABLE public.squad_events ADD COLUMN IF NOT EXISTS notes           TEXT;
ALTER TABLE public.squad_events ADD COLUMN IF NOT EXISTS is_cancelled    BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.squad_events ADD COLUMN IF NOT EXISTS source_inbox_id UUID;

-- Add FK from squad_events.source_inbox_id → squad_email_inbox only if not present
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_squad_events_source_inbox'
          AND table_name = 'squad_events'
    ) THEN
        ALTER TABLE public.squad_events
            ADD CONSTRAINT fk_squad_events_source_inbox
            FOREIGN KEY (source_inbox_id)
            REFERENCES public.squad_email_inbox(id) ON DELETE SET NULL;
    END IF;
END $$;

ALTER TABLE public.squad_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "squad_events_manager_all" ON public.squad_events;
CREATE POLICY "squad_events_manager_all" ON public.squad_events
    FOR ALL USING (
        squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid())
    );


-- ── 6. squad_rsvps ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.squad_rsvps (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_link_id  UUID NOT NULL REFERENCES public.squad_parent_links(id) ON DELETE CASCADE,
    event_id        UUID NOT NULL REFERENCES public.squad_events(id) ON DELETE CASCADE,
    status          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ
);

ALTER TABLE public.squad_rsvps ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- Unique constraint: one RSVP per parent per event
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'squad_rsvps_parent_link_id_event_id_key'
          AND table_name = 'squad_rsvps'
    ) THEN
        ALTER TABLE public.squad_rsvps
            ADD CONSTRAINT squad_rsvps_parent_link_id_event_id_key
            UNIQUE (parent_link_id, event_id);
    END IF;
END $$;

ALTER TABLE public.squad_rsvps ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "squad_rsvps_manager_all" ON public.squad_rsvps;
CREATE POLICY "squad_rsvps_manager_all" ON public.squad_rsvps
    FOR ALL USING (
        parent_link_id IN (
            SELECT pl.id FROM public.squad_parent_links pl
            JOIN public.squads s ON s.id = pl.squad_id
            WHERE s.manager_user_id = auth.uid()
        )
    );


-- ── 7. Indexes ────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_squad_email_inbox_status ON public.squad_email_inbox(status);
CREATE INDEX IF NOT EXISTS idx_squad_email_inbox_squad  ON public.squad_email_inbox(squad_id);
CREATE INDEX IF NOT EXISTS idx_squad_events_squad_date  ON public.squad_events(squad_id, event_date);
CREATE INDEX IF NOT EXISTS idx_squad_rsvps_event        ON public.squad_rsvps(event_id);
CREATE INDEX IF NOT EXISTS idx_squad_players_squad      ON public.squad_players(squad_id);
