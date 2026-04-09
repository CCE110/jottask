-- 027_squad_event_players.sql
-- Availability poll: one row per player per event.
-- All parents linked to a player share the same poll_token (first response wins).

CREATE TABLE IF NOT EXISTS squad_event_players (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        UUID NOT NULL REFERENCES squad_events(id) ON DELETE CASCADE,
    player_id       UUID NOT NULL REFERENCES squad_players(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'yes', 'no')),
    poll_token      TEXT UNIQUE NOT NULL,
    responded_by    TEXT,        -- informational: parent name who tapped first
    responded_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (event_id, player_id)
);

ALTER TABLE squad_event_players ENABLE ROW LEVEL SECURITY;

-- Managers can read/write rows belonging to their squads
CREATE POLICY "sep_manager_all" ON squad_event_players
    FOR ALL
    USING (
        event_id IN (
            SELECT e.id FROM squad_events e
            JOIN squads s ON s.id = e.squad_id
            WHERE s.manager_user_id = auth.uid()
        )
    );

-- Public RSVP routes use the service-role client (_admin_db) so no public policy needed.

CREATE INDEX IF NOT EXISTS idx_sep_event_id  ON squad_event_players(event_id);
CREATE INDEX IF NOT EXISTS idx_sep_poll_token ON squad_event_players(poll_token);
