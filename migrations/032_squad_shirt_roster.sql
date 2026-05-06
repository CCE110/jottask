-- Shirt washing roster: one player per game takes the shirts home to wash.
-- Mirrors the fruit roster (migration 028). Stored on each game event so
-- swaps and history are first-class. Auto-assigned at game creation as
-- (fruit_player_index + 5) % player_count so shirt and fruit can't collide.
--
-- Note: SHIRT_OFFSET is configured in squad_routes.py — change there if the
-- offset needs to shift. The column itself is just a nullable FK and lets
-- managers override the default via the event edit sheet.

ALTER TABLE public.squad_events
  ADD COLUMN IF NOT EXISTS shirt_player_id UUID
  REFERENCES public.squad_players(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_squad_events_shirt_player
  ON public.squad_events(shirt_player_id)
  WHERE shirt_player_id IS NOT NULL;
