-- Fruit roster: one player per game brings fruit, rotates alphabetically.
-- Stored on each game event so swaps are first-class and history is preserved.

ALTER TABLE public.squad_events
  ADD COLUMN IF NOT EXISTS fruit_player_id UUID
  REFERENCES public.squad_players(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_squad_events_fruit_player
  ON public.squad_events(fruit_player_id)
  WHERE fruit_player_id IS NOT NULL;
