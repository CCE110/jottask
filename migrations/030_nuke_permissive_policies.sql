-- =============================================================================
-- Migration 030: Nuke residual permissive policies
--
-- After 029 was applied, an anon-key probe confirmed RLS is enabled on every
-- public table BUT 8 tables still return data to anon:
--
--   tasks, task_notes, contacts, system_events, organizations, projects,
--   project_items, referral_invites, project_statuses
--
-- Cause: PostgreSQL evaluates RLS policies as a logical OR — any single
-- permissive policy that returns true for the row makes the row visible.
-- Older migrations created policies on these tables (e.g. an early
-- "Anyone can read" or "FOR SELECT USING (true)" policy) that 029 didn't
-- DROP by name, so they OR'd with the new restrictive policy and let anon
-- through.
--
-- This migration drops EVERY policy on the leaking tables via a dynamic
-- DO block, then rebuilds only the restrictive one we want. Idempotent.
-- =============================================================================


-- ── Helper: drop all policies on a table ─────────────────────────────────────

DO $$
DECLARE
  r record;
  victims text[] := ARRAY[
    'tasks',
    'task_notes',
    'task_checklist_items',
    'contacts',
    'system_events',
    'organizations',
    'projects',
    'project_items',
    'referral_invites',
    'project_statuses',
    'pending_actions',
    'processed_emails',
    'email_action_tokens'
  ];
  tbl text;
BEGIN
  FOREACH tbl IN ARRAY victims LOOP
    FOR r IN
      SELECT policyname FROM pg_policies
       WHERE schemaname = 'public' AND tablename = tbl
    LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', r.policyname, tbl);
    END LOOP;
  END LOOP;
END $$;


-- ── Re-add the restrictive policies (no policy = service-role only) ─────────

-- tasks: owner-only full CRUD
CREATE POLICY "tasks_owner_all" ON public.tasks FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- task_notes: scoped via parent task
CREATE POLICY "task_notes_owner_all" ON public.task_notes FOR ALL
  USING (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()))
  WITH CHECK (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()));

-- task_checklist_items: scoped via parent task
CREATE POLICY "task_checklist_owner_all" ON public.task_checklist_items FOR ALL
  USING (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()))
  WITH CHECK (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()));

-- organizations: owner-only
CREATE POLICY "organizations_owner_all" ON public.organizations FOR ALL
  USING (auth.uid() = owner_id) WITH CHECK (auth.uid() = owner_id);

-- referral_invites: referrer-only
CREATE POLICY "referral_invites_owner_all" ON public.referral_invites FOR ALL
  USING (auth.uid() = referrer_id) WITH CHECK (auth.uid() = referrer_id);

-- contacts, system_events, organizations[done above], projects, project_items,
-- project_statuses, pending_actions, processed_emails, email_action_tokens
-- intentionally have NO policy → service-role-only access.
--   • contacts: CRM-sync table, created_by is a TEXT name not a UUID
--   • system_events: internal monitoring (admin-only)
--   • projects/project_items: business_id-keyed, no direct user column
--   • project_statuses: shared config table
--   • pending_actions / processed_emails / email_action_tokens: token-based
--     server-side flows, never user-readable via PostgREST


-- ── Verification (run separately after this completes) ──────────────────────
-- SELECT tablename, policyname, cmd, qual
--   FROM pg_policies
--  WHERE schemaname = 'public' AND tablename IN (
--    'tasks','task_notes','contacts','system_events','organizations',
--    'projects','project_items','referral_invites','project_statuses',
--    'pending_actions','processed_emails','email_action_tokens',
--    'task_checklist_items'
--  )
--  ORDER BY tablename, policyname;
-- Expected: only the 5 *_owner_all policies above (tasks, task_notes,
-- task_checklist_items, organizations, referral_invites). The other 8
-- tables should have ZERO policies → service-role-only access.
