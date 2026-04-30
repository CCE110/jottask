-- =============================================================================
-- Migration 031: Lead tagging system
--
-- Stores arbitrary tags against DSW Solar tasks (and any other tasks) so the
-- lead detail page, reminder emails, and the future broadcast tooling can
-- filter by hardware fit (V2G ready, 3-phase, single-phase, battery,
-- EV charger). Each tag is its own row so we can add new tag types without
-- a schema change later.
-- =============================================================================


CREATE TABLE IF NOT EXISTS public.lead_tags (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id     UUID        NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
    tag         TEXT        NOT NULL CHECK (tag IN (
                              'v2g',
                              'three_phase',
                              'single_phase',
                              'battery',
                              'ev_charger'
                            )),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_lead_tags_task ON public.lead_tags(task_id);
CREATE INDEX IF NOT EXISTS idx_lead_tags_tag  ON public.lead_tags(tag);


-- ── RLS — tag visibility scoped to parent task owner ───────────────────────

ALTER TABLE public.lead_tags ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "lead_tags_owner_all" ON public.lead_tags;
CREATE POLICY "lead_tags_owner_all" ON public.lead_tags FOR ALL
  USING (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()))
  WITH CHECK (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()));
