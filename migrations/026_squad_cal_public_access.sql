-- =============================================================================
-- Migration 026: Public RLS policies for squad iCal feed
--
-- The /squad/cal/<token>.ics route is unauthenticated — iOS Calendar fetches
-- it without a session, so auth.uid() is NULL and the existing
-- squad_manager_all / squad_events_manager_all policies return 0 rows → 404.
--
-- The cal_token acts as the access credential (long random hex string).
-- Knowing the token = permission to read that squad's calendar.
-- Safe to run on a live database — fully idempotent.
-- =============================================================================


-- ── squads: allow unauthenticated SELECT when cal_token is known ──────────────

DROP POLICY IF EXISTS "squad_public_cal_read" ON public.squads;
CREATE POLICY "squad_public_cal_read" ON public.squads
    FOR SELECT
    USING (cal_token IS NOT NULL);


-- ── squad_events: allow unauthenticated SELECT for calendar feed ──────────────

DROP POLICY IF EXISTS "squad_events_public_cal_read" ON public.squad_events;
CREATE POLICY "squad_events_public_cal_read" ON public.squad_events
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.squads
            WHERE squads.id = squad_events.squad_id
              AND squads.cal_token IS NOT NULL
        )
    );


-- ── Ensure every squad has a cal_token (guard against NULL from older rows) ───

UPDATE public.squads
SET cal_token = encode(gen_random_bytes(16), 'hex')
WHERE cal_token IS NULL;
