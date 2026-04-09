-- Migration 023: Fix RLS and security definer view issues
-- Resolves 35 Supabase Security Advisor errors reported 2026-03-07

-- ============================================================
-- 1. ENABLE RLS ON TABLES WITH EXISTING POLICIES
--    These tables have policies but RLS was never activated
-- ============================================================

ALTER TABLE public.activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.email_commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.opportunities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pending_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- 2. ENABLE RLS ON TABLES WITH NO POLICIES + ADD POLICIES
-- ============================================================

-- processed_emails: internal system table, only service_role needs access
ALTER TABLE public.processed_emails ENABLE ROW LEVEL SECURITY;
-- No policy needed — service_role bypasses RLS; anon/authenticated get no access

-- email_action_tokens: token-based access, no user auth required
-- Service_role (used by app) bypasses RLS
ALTER TABLE public.email_action_tokens ENABLE ROW LEVEL SECURITY;

-- activities_archive: internal archive, no direct user access needed
ALTER TABLE public.activities_archive ENABLE ROW LEVEL SECURITY;

-- subscription_plans: public read-only (all users need to view plans)
ALTER TABLE public.subscription_plans ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Anyone can view subscription plans" ON public.subscription_plans;
CREATE POLICY "Anyone can view subscription plans"
  ON public.subscription_plans FOR SELECT
  USING (true);

-- project_statuses: user-scoped
ALTER TABLE public.project_statuses ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can manage own project statuses" ON public.project_statuses;
CREATE POLICY "Users can manage own project statuses"
  ON public.project_statuses FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- support_conversations: user-scoped
ALTER TABLE public.support_conversations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can access own support conversations" ON public.support_conversations;
CREATE POLICY "Users can access own support conversations"
  ON public.support_conversations FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- support_messages: scoped via conversation
ALTER TABLE public.support_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can access own support messages" ON public.support_messages;
CREATE POLICY "Users can access own support messages"
  ON public.support_messages FOR ALL
  USING (
    conversation_id IN (
      SELECT id FROM public.support_conversations WHERE user_id = auth.uid()
    )
  );

-- referrals: user can view own referrals (as referrer or referred)
ALTER TABLE public.referrals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own referrals" ON public.referrals;
CREATE POLICY "Users can view own referrals"
  ON public.referrals FOR SELECT
  USING (auth.uid() = referrer_id OR auth.uid() = referred_id);

-- duplicate_dismissed: user-scoped
ALTER TABLE public.duplicate_dismissed ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can manage own dismissed duplicates" ON public.duplicate_dismissed;
CREATE POLICY "Users can manage own dismissed duplicates"
  ON public.duplicate_dismissed FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- ============================================================
-- 3. FIX SECURITY DEFINER VIEWS
--    Switch to security_invoker so views respect the querying
--    user's RLS policies instead of the view creator's
-- ============================================================

ALTER VIEW public.tasks_with_details SET (security_invoker = on);
ALTER VIEW public.recent_task_notes SET (security_invoker = on);
ALTER VIEW public.tasks_view SET (security_invoker = on);
ALTER VIEW public.tasks_with_crm SET (security_invoker = on);
