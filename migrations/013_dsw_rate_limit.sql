-- Migration 013 — DSW rate-limit / circuit-breaker
--
-- The DSW lead path has stormed three times in two days (2026-09-10 create-loop,
-- fail-open dedup, 2026-09-11 email echo), each with a different root cause but
-- the same shape: one PipeReply contact rapidly generating repeated tasks/emails.
-- This adds a hard backstop below every per-path guard: if the same contact
-- generates more than N actions inside M minutes, all DSW paths refuse to
-- process that contact and Rob gets ONE alert. Two thresholds:
--
--   Fast burst:  >3 actions in 10 min
--   Slow drip:   >10 actions in 60 min
--
-- Manual clear only — the tripped contact stays frozen until Rob explicitly
-- flips `manually_cleared=true`.
--
-- Referenced by dsw_lead_poller._rate_limit_check(), which gates
-- dsw.process(), dsw.make_task(), and dsw.send_email().

CREATE TABLE IF NOT EXISTS contact_actions (
  id            uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_id    text          NOT NULL,     -- PipeReply cid
  action_type   text          NOT NULL,     -- process_call | task_create | lead_email | crm_note_post
  triggered_by  text,                       -- handler / caller label
  task_id       uuid,                       -- FK-shape (not enforced) to tasks.id
  at            timestamptz   NOT NULL DEFAULT now()
);

-- Rolling-window lookup: WHERE contact_id=? AND at > now()-interval 'N min'
CREATE INDEX IF NOT EXISTS idx_contact_actions_cid_at
  ON contact_actions(contact_id, at DESC);


CREATE TABLE IF NOT EXISTS contact_circuit_breaker (
  contact_id       text          PRIMARY KEY,
  tripped_at       timestamptz   NOT NULL DEFAULT now(),
  trip_reason      text          NOT NULL,   -- e.g. "4 actions in 10min (fast burst)"
  trip_count       int           NOT NULL,   -- count at trip time
  trip_window      text          NOT NULL DEFAULT 'fast',  -- 'fast' | 'slow'
  alerted_at       timestamptz,              -- NULL until send_self_alert fires
  manually_cleared boolean       NOT NULL DEFAULT false,
  cleared_at       timestamptz,
  cleared_by       text                      -- 'rob' / 'auto' / etc
);


-- Retention hint: contact_actions grows ~one row per lead action. A cleanup
-- job (or a follow-up TTL policy) can DELETE rows older than 30 days. Not
-- shipping cleanup with this migration — the rolling-window query only
-- reads recent rows, so growth is a background cost, not a correctness issue.
