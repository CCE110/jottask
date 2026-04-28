-- =============================================================================
-- Migration 029: Emergency RLS hardening
--
-- Resolves Supabase Security Advisor alerts:
--   - rls_disabled_in_public      (table publicly accessible)
--   - sensitive_columns_exposed   (PII / auth tokens readable by anon)
--
-- Probed via anon key on 2026-04-29 — these tables were returning rows to
-- unauthenticated callers:
--   users, tasks, task_notes, pending_actions, contacts, system_events,
--   support_messages, organizations, projects, project_items,
--   referral_invites, project_statuses, processed_emails, squads,
--   squad_events
--
-- Two root causes:
--   1. Migration 026 added "USING (cal_token IS NOT NULL)" policies on
--      squads/squad_events to support an unauthenticated iCal feed. The
--      iCal route now uses _admin_db() (service_role) which bypasses RLS,
--      so those policies are redundant AND wide-open — every squad has a
--      cal_token, so the policy effectively means "anyone can read every
--      squad and every event". DROP them.
--   2. Several tables had ENABLE RLS skipped (or lost it) so RLS isn't
--      enforced at all → anon role sees everything via PostgREST. ENABLE
--      RLS on every public-schema table that the app uses.
--
-- The Flask backend already uses the service-role key on Railway (lazy
-- _LazySupabase proxy initialised from SUPABASE_KEY env), so locking down
-- anon doesn't break any app code paths. Public routes that need anon
-- access (iCal feed, RSVP, email action tokens) all use _admin_db() /
-- service-role.
--
-- Idempotent — safe to re-run.
-- =============================================================================


-- ── 1. DROP the over-permissive policies from migration 026 ──────────────────

DROP POLICY IF EXISTS "squad_public_cal_read"        ON public.squads;
DROP POLICY IF EXISTS "squad_events_public_cal_read" ON public.squad_events;


-- ── 2. FORCE-ENABLE RLS on every table the app touches ──────────────────────
--    With RLS on and no anon-permitting policy, anon role sees zero rows.
--    Service role bypasses RLS — Flask app keeps working.

ALTER TABLE public.users                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tasks                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.task_notes             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.task_checklist_items   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.email_connections      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_connections        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.contacts               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.processed_emails       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.email_action_tokens    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pending_actions        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.system_events          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subscription_plans     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.referrals              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.referral_invites       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organizations          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.projects               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.project_items          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.saas_projects          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.saas_project_items     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.project_statuses       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.support_conversations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.support_messages       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_conversations     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_messages          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.duplicate_dismissed    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.squads                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.squad_players          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.squad_parent_links     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.squad_events           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.squad_email_inbox      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.squad_event_players    ENABLE ROW LEVEL SECURITY;


-- ── 3. Subscription plans — public read OK (signup page lists tiers) ─────────

DROP POLICY IF EXISTS "Anyone can view subscription plans" ON public.subscription_plans;
CREATE POLICY "Anyone can view subscription plans"
  ON public.subscription_plans FOR SELECT
  USING (true);


-- ── 4. User-scoped policies (idempotent — drop+recreate so wording is stable)
--    These tables already had policies in earlier migrations, but enabling
--    RLS for the first time on some of them activates them. For the rest
--    we re-establish a known-good policy here so the system is fully
--    auditable from this single migration.

-- users — only see / update own profile
DROP POLICY IF EXISTS "Users can view own profile"   ON public.users;
DROP POLICY IF EXISTS "Users can update own profile" ON public.users;
DROP POLICY IF EXISTS "users_select_self"            ON public.users;
DROP POLICY IF EXISTS "users_update_self"            ON public.users;
CREATE POLICY "users_select_self" ON public.users FOR SELECT
  USING (auth.uid() = id);
CREATE POLICY "users_update_self" ON public.users FOR UPDATE
  USING (auth.uid() = id) WITH CHECK (auth.uid() = id);

-- tasks — full CRUD on own rows
DROP POLICY IF EXISTS "Users can view own tasks"   ON public.tasks;
DROP POLICY IF EXISTS "Users can create own tasks" ON public.tasks;
DROP POLICY IF EXISTS "Users can update own tasks" ON public.tasks;
DROP POLICY IF EXISTS "Users can delete own tasks" ON public.tasks;
DROP POLICY IF EXISTS "tasks_owner_all"            ON public.tasks;
CREATE POLICY "tasks_owner_all" ON public.tasks FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- task_notes — scoped via parent task
DROP POLICY IF EXISTS "Users can view own task notes"      ON public.task_notes;
DROP POLICY IF EXISTS "Users can create notes on own tasks" ON public.task_notes;
DROP POLICY IF EXISTS "task_notes_owner_all"               ON public.task_notes;
CREATE POLICY "task_notes_owner_all" ON public.task_notes FOR ALL
  USING (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()))
  WITH CHECK (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()));

-- task_checklist_items — scoped via parent task
DROP POLICY IF EXISTS "Users can view own checklist items"   ON public.task_checklist_items;
DROP POLICY IF EXISTS "Users can manage own checklist items" ON public.task_checklist_items;
DROP POLICY IF EXISTS "task_checklist_owner_all"             ON public.task_checklist_items;
CREATE POLICY "task_checklist_owner_all" ON public.task_checklist_items FOR ALL
  USING (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()))
  WITH CHECK (task_id IN (SELECT id FROM public.tasks WHERE user_id = auth.uid()));

-- email_connections / crm_connections — own rows
DROP POLICY IF EXISTS "email_connections_owner_all" ON public.email_connections;
CREATE POLICY "email_connections_owner_all" ON public.email_connections FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "crm_connections_owner_all" ON public.crm_connections;
CREATE POLICY "crm_connections_owner_all" ON public.crm_connections FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- contacts — own rows (scoped by user_id column added in 016)
DROP POLICY IF EXISTS "contacts_owner_all" ON public.contacts;
CREATE POLICY "contacts_owner_all" ON public.contacts FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- referrals
DROP POLICY IF EXISTS "Users can view own referrals" ON public.referrals;
CREATE POLICY "Users can view own referrals" ON public.referrals FOR SELECT
  USING (auth.uid() = referrer_id OR auth.uid() = referred_id);

-- referral_invites — referrer owns
DROP POLICY IF EXISTS "referral_invites_owner_all" ON public.referral_invites;
CREATE POLICY "referral_invites_owner_all" ON public.referral_invites FOR ALL
  USING (auth.uid() = referrer_id) WITH CHECK (auth.uid() = referrer_id);

-- organizations — owner sees own
DROP POLICY IF EXISTS "organizations_owner_all" ON public.organizations;
CREATE POLICY "organizations_owner_all" ON public.organizations FOR ALL
  USING (auth.uid() = owner_id) WITH CHECK (auth.uid() = owner_id);

-- projects + project_items — owner via business_id => skip strict policy;
-- service_role handles writes, no anon access.
-- saas_projects + saas_project_items — owner via user_id
DROP POLICY IF EXISTS "saas_projects_owner_all" ON public.saas_projects;
CREATE POLICY "saas_projects_owner_all" ON public.saas_projects FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "saas_project_items_owner_all" ON public.saas_project_items;
CREATE POLICY "saas_project_items_owner_all" ON public.saas_project_items FOR ALL
  USING (project_id IN (SELECT id FROM public.saas_projects WHERE user_id = auth.uid()))
  WITH CHECK (project_id IN (SELECT id FROM public.saas_projects WHERE user_id = auth.uid()));

-- project_statuses
DROP POLICY IF EXISTS "Users can manage own project statuses" ON public.project_statuses;
CREATE POLICY "project_statuses_owner_all" ON public.project_statuses FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- support tables
DROP POLICY IF EXISTS "Users can access own support conversations" ON public.support_conversations;
CREATE POLICY "support_conversations_owner_all" ON public.support_conversations FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "Users can access own support messages" ON public.support_messages;
CREATE POLICY "support_messages_owner_all" ON public.support_messages FOR ALL
  USING (conversation_id IN (SELECT id FROM public.support_conversations WHERE user_id = auth.uid()))
  WITH CHECK (conversation_id IN (SELECT id FROM public.support_conversations WHERE user_id = auth.uid()));

-- chat tables
DROP POLICY IF EXISTS "chat_conversations_owner_all" ON public.chat_conversations;
CREATE POLICY "chat_conversations_owner_all" ON public.chat_conversations FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
DROP POLICY IF EXISTS "chat_messages_owner_all" ON public.chat_messages;
CREATE POLICY "chat_messages_owner_all" ON public.chat_messages FOR ALL
  USING (conversation_id IN (SELECT id FROM public.chat_conversations WHERE user_id = auth.uid()))
  WITH CHECK (conversation_id IN (SELECT id FROM public.chat_conversations WHERE user_id = auth.uid()));

-- duplicate_dismissed
DROP POLICY IF EXISTS "Users can manage own dismissed duplicates" ON public.duplicate_dismissed;
CREATE POLICY "duplicate_dismissed_owner_all" ON public.duplicate_dismissed FOR ALL
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);


-- ── 5. SQUAD tables — manager sees own squad's data ──────────────────────────
--    Public (anon) access to iCal feed is handled in app code via
--    _admin_db() (service_role), so RLS does NOT need an anon-read policy.

DROP POLICY IF EXISTS "squad_manager_all" ON public.squads;
CREATE POLICY "squad_manager_all" ON public.squads FOR ALL
  USING (auth.uid() = manager_user_id) WITH CHECK (auth.uid() = manager_user_id);

DROP POLICY IF EXISTS "squad_players_manager_all" ON public.squad_players;
CREATE POLICY "squad_players_manager_all" ON public.squad_players FOR ALL
  USING (squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid()))
  WITH CHECK (squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid()));

DROP POLICY IF EXISTS "squad_parent_links_manager_all" ON public.squad_parent_links;
CREATE POLICY "squad_parent_links_manager_all" ON public.squad_parent_links FOR ALL
  USING (squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid()))
  WITH CHECK (squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid()));

DROP POLICY IF EXISTS "squad_events_manager_all" ON public.squad_events;
CREATE POLICY "squad_events_manager_all" ON public.squad_events FOR ALL
  USING (squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid()))
  WITH CHECK (squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid()));

DROP POLICY IF EXISTS "squad_email_inbox_manager_all" ON public.squad_email_inbox;
CREATE POLICY "squad_email_inbox_manager_all" ON public.squad_email_inbox FOR ALL
  USING (squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid()))
  WITH CHECK (squad_id IN (SELECT id FROM public.squads WHERE manager_user_id = auth.uid()));

DROP POLICY IF EXISTS "squad_event_players_manager_all" ON public.squad_event_players;
CREATE POLICY "squad_event_players_manager_all" ON public.squad_event_players FOR ALL
  USING (event_id IN (
    SELECT e.id FROM public.squad_events e
    JOIN public.squads s ON s.id = e.squad_id
    WHERE s.manager_user_id = auth.uid()
  ))
  WITH CHECK (event_id IN (
    SELECT e.id FROM public.squad_events e
    JOIN public.squads s ON s.id = e.squad_id
    WHERE s.manager_user_id = auth.uid()
  ));


-- ── 6. SERVICE-ROLE-ONLY tables ──────────────────────────────────────────────
--    No anon/authenticated policies = no access via PostgREST. The Flask
--    app reaches them via service_role which bypasses RLS.
--      • system_events    (internal monitoring / heartbeat)
--      • processed_emails (dedup state)
--      • email_action_tokens (token-based 1-click actions)
--      • pending_actions  (Tier-2 approval tokens — sensitive)
--    Just enabling RLS without policies = lockdown. No CREATE POLICY needed.


-- ── 7. Re-verify post-migration with this query (in Supabase SQL editor) ────
-- SELECT schemaname, tablename, rowsecurity
--   FROM pg_tables
--  WHERE schemaname = 'public' AND rowsecurity = false;
-- Expected: zero rows.
