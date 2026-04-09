# JOTTASK CODEBASE — COMPREHENSIVE KNOWLEDGE BASE

> Last updated: 2026-03-13. Reflects migrations 001–023 and all code as of current main branch.

---

## Executive Summary

Jottask is an **AI-powered task management and sales automation platform** for the Australian solar industry. Currently a single-tenant app serving Direct Solar Wholesalers (DSW), it's being rebuilt as a **multi-tenant SaaS**. The platform connects to email, CRM systems (PipeReply, HubSpot, Zoho), and OpenSolar for full workflow automation from lead arrival to installation handoff.

**Tech Stack:** Python 3 / Flask, PostgreSQL (Supabase), Railway hosting, Anthropic Claude API, Resend/SMTP email, Stripe billing

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3 / Flask |
| Database | Supabase (PostgreSQL + Auth + RLS) |
| Hosting | Railway (3 services: web, worker, scheduler) |
| Email Inbound | IMAP polling via privateemail.com |
| Email Outbound | Resend API + SMTP (privateemail.com) |
| AI | Anthropic Claude API |
| Payments | Stripe |
| Frontend | Server-rendered HTML/Jinja2 (inline render_template_string) |

---

## Repository Structure

```
jottask/
├── dashboard.py              # Main Flask app (~4,000 lines): all routes + inline templates
├── saas_email_processor.py   # Worker: IMAP polling → AI parsing → task creation (~920 lines)
├── saas_scheduler.py         # Scheduler: reminders + daily summaries (~575 lines)
├── task_manager.py           # Supabase CRUD helper class (~300 lines)
├── auth.py                   # Authentication + decorators (~150 lines)
├── billing.py                # Stripe subscription blueprint /billing/* (~210 lines)
├── onboarding.py             # New user setup wizard /onboarding/* (~300 lines)
├── email_setup.py            # Email connection UI /email/* (~200 lines)
├── email_utils.py            # Shared email sender via Resend (~70 lines)
├── approval_routes.py        # Email approval token handler
├── monitoring.py             # System event logging + healthchecks (~600 lines)
├── crm_setup.py              # CRM connection UI /crm/* (~400 lines)
├── crm_manager.py            # CRM database + dispatcher layer (~250 lines)
├── chat.py                   # AI chat interface routes (~600 lines)
├── chat_tools.py             # Claude tool-use handlers (~400 lines)
├── opensolar_connector.py    # OpenSolar headless browser integration (~400 lines)
├── crm_connectors/
│   ├── base.py               # Abstract connector interface + dataclasses
│   ├── registry.py           # Factory: get_connector(provider, ...)
│   ├── pipereply.py          # PipeReply / GoHighLevel CRM
│   ├── hubspot.py            # HubSpot CRM
│   └── zoho.py               # Zoho CRM
├── templates/                # HTML template files
├── static/                   # CSS, JS, images
├── migrations/               # SQL migration files (001–023)
├── requirements.txt          # Python dependencies
├── Procfile                  # Railway process definitions
└── railway.toml              # Railway build config
```

---

## Deployment

**Procfile (Railway):**
```
web: gunicorn dashboard:app --bind 0.0.0.0:$PORT
worker: python3 saas_email_processor.py
```

**railway.toml:**
```toml
[build]
builder = "nixpacks"

[build.nixpacks]
providers = ["python"]
```

**Key Python packages (requirements.txt):**
- anthropic==0.34.2
- supabase==2.7.4
- flask==3.0.3
- gunicorn==21.2.0
- pytz==2025.2
- schedule==1.2.2
- stripe==7.0.0
- resend==0.7.2
- requests
- pytest==8.3.4

---

## Environment Variables

```bash
# Supabase
SUPABASE_URL=https://bcrovytubvhrmypefzpe.supabase.co
SUPABASE_KEY=<service_role_key>

# Email (IMAP inbound)
JOTTASK_EMAIL=jottask@flowquote.ai
JOTTASK_EMAIL_PASSWORD=<password>
IMAP_SERVER=mail.privateemail.com

# Email (outbound)
RESEND_API_KEY=<resend_api_key>
FROM_EMAIL=admin@flowquote.ai

# AI
ANTHROPIC_API_KEY=sk-ant-...

# Stripe
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_PRO_MONTHLY=price_...
STRIPE_PRICE_PRO_YEARLY=price_...
STRIPE_PRICE_BUSINESS_MONTHLY=price_...
STRIPE_PRICE_BUSINESS_YEARLY=price_...

# Flask
FLASK_SECRET_KEY=<secret>
WEB_SERVICE_URL=https://www.jottask.app
TASK_ACTION_URL=https://www.jottask.app/action
APP_URL=https://www.jottask.app
INTERNAL_API_KEY=<see Railway env vars — CLAUDE.md value is stale>
```

---

## Database Schema

### users
Extends Supabase Auth. RLS-enabled.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK, references auth.users |
| email | TEXT | UNIQUE |
| full_name | TEXT | Display name |
| company_name | TEXT | |
| phone | TEXT | |
| timezone | TEXT | Default: 'Australia/Brisbane' |
| avatar_url | TEXT | Profile picture |
| subscription_status | TEXT | 'trial' \| 'active' \| 'cancelled' \| 'expired' |
| subscription_tier | TEXT | 'starter' \| 'pro' \| 'business' |
| trial_ends_at | TIMESTAMPTZ | 14 days from signup |
| stripe_customer_id | TEXT | |
| stripe_subscription_id | TEXT | |
| email_notifications | BOOLEAN | Default true |
| daily_summary_time | TIME | Default 08:00:00 |
| daily_summary_enabled | BOOLEAN | Default true |
| reminder_minutes_before | INTEGER | Default 30 |
| last_summary_sent_at | TIMESTAMPTZ | Dedup for daily summaries |
| onboarding_completed | BOOLEAN | |
| tasks_this_month | INTEGER | Usage metering |
| tasks_month_reset | DATE | Monthly reset marker |
| referral_code | TEXT | Unique 8-char auto-generated |
| referred_by | UUID | FK to users |
| referral_credits | DECIMAL | AUD credit earned |
| ai_context | JSONB | Per-user AI prompt customization |
| organization_id | UUID | FK to organizations |
| role | TEXT | 'user' \| 'company_admin' \| 'global_admin' |
| last_login_at | TIMESTAMPTZ | |
| last_system_alert_at | TIMESTAMPTZ | Alert throttle |
| system_alert_count_today | INTEGER | Alert throttle counter |
| created_at / updated_at | TIMESTAMPTZ | |

**Triggers:** Auto-update `updated_at`, generate referral_code on insert.

---

### tasks
Core task records. RLS-enabled.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK to users (required) |
| business_id | UUID | Legacy field (phased out) |
| title | TEXT | Task description |
| description | TEXT | Details |
| due_date | DATE | YYYY-MM-DD |
| due_time | TIME | HH:MM:SS (nullable) |
| priority | TEXT | 'urgent' \| 'high' \| 'medium' \| 'low' |
| status | TEXT | 'pending' \| 'completed' \| 'cancelled' |
| category | TEXT | e.g. 'follow-up', 'quote', 'install' |
| is_meeting | BOOLEAN | |
| client_name | TEXT | |
| client_email | TEXT | Lowercased |
| client_phone | TEXT | |
| project_name | TEXT | |
| project_status_id | UUID | FK to project_statuses |
| source | TEXT | 'email' \| 'manual' \| 'api' |
| reminder_sent_at | TIMESTAMPTZ | Last reminder (prevents duplicates) |
| completed_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ | |

**Indexes:** (user_id, status), (user_id, due_date, due_time), reminder_sent_at

---

### task_notes

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| task_id | UUID | FK to tasks |
| content | TEXT | Note text |
| source | TEXT | 'email' \| 'manual' \| 'system' \| 'ai' |
| source_email_subject | TEXT | |
| source_email_from | TEXT | |
| source_email_date | TIMESTAMPTZ | |
| created_by | TEXT | Usually 'system' |
| created_at | TIMESTAMPTZ | |

---

### task_checklist_items

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| task_id | UUID | FK to tasks |
| title | TEXT | |
| is_completed | BOOLEAN | |
| completed_at | TIMESTAMPTZ | |
| position | INTEGER | Sort order |
| created_at | TIMESTAMPTZ | |

---

### email_connections

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK to users |
| provider | TEXT | 'gmail' \| 'outlook' \| 'imap' |
| email_address | TEXT | |
| access_token / refresh_token | TEXT | OAuth |
| imap_password | TEXT | App-specific password |
| imap_server | TEXT | Default: 'imap.gmail.com' |
| is_active | BOOLEAN | Whether to poll |
| use_env_credentials | BOOLEAN | Fallback to env vars |
| last_sync_at | TIMESTAMPTZ | |
| sync_frequency_minutes | INTEGER | Default 15 |
| created_at / updated_at | TIMESTAMPTZ | |

**Unique:** (user_id, email_address)

---

### crm_connections

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK to users |
| provider | TEXT | 'pipereply' \| 'hubspot' \| 'zoho' \| 'salesforce' \| 'opensolar' \| 'none' |
| display_name | TEXT | User-friendly name |
| api_key | TEXT | API key or password |
| api_base_url | TEXT | Custom endpoint |
| access_token / refresh_token | TEXT | OAuth |
| token_expires_at | TIMESTAMPTZ | |
| is_active | BOOLEAN | |
| connection_status | TEXT | 'pending' \| 'connected' \| 'error' \| 'disconnected' |
| field_mapping | JSONB | CRM field → Jottask field mappings |
| settings | JSONB | Provider-specific config |
| last_sync_at | TIMESTAMPTZ | |
| last_error | TEXT | |
| created_at / updated_at | TIMESTAMPTZ | |

**Unique:** (user_id, provider)

---

### pending_actions
Tier 2 approval queue for AI-suggested actions.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK to users |
| token | TEXT | UNIQUE secure token |
| action_type | TEXT | 'update_crm' \| 'send_email' \| 'create_calendar_event' |
| action_data | JSONB | Full action details |
| status | TEXT | 'pending' \| 'approved' \| 'rejected' |
| crm_synced | BOOLEAN | Already synced after approval |
| crm_synced_at | TIMESTAMPTZ | |
| processed_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ | |

---

### email_action_tokens
Passwordless task actions from email buttons.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| task_id | UUID | FK to tasks |
| user_id | UUID | FK to users |
| token | TEXT | UNIQUE |
| action | TEXT | 'complete' \| 'delay_1h' \| 'delay_1d' \| 'reschedule' |
| expires_at | TIMESTAMPTZ | Usually 72h from creation |
| used_at | TIMESTAMPTZ | NULL = unused |
| created_at | TIMESTAMPTZ | |

---

### processed_emails
Deduplication log for email processor.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| email_id | TEXT | UNIQUE Message-ID from IMAP |
| uid | TEXT | IMAP UID |
| user_id | UUID | Which inbox |
| connection_id | UUID | FK to email_connections |
| processed_at | TIMESTAMPTZ | |
| outcome | TEXT | 'success' \| 'skipped' \| 'error' |

---

### subscription_plans
Read-only reference data. Public read RLS.

| Column | Type | Notes |
|--------|------|-------|
| id | TEXT | PK: 'starter' \| 'pro' \| 'business' |
| name | TEXT | Display name |
| price_monthly / price_yearly | DECIMAL | AUD |
| max_tasks | INTEGER | -1 = unlimited |
| max_email_connections | INTEGER | |
| max_team_members | INTEGER | |
| ai_summaries, custom_statuses, api_access, priority_support | BOOLEAN | Feature flags |
| stripe_price_id_monthly / yearly | TEXT | Stripe price IDs |

---

### project_statuses
Custom workflow stages per user.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK to users |
| name | TEXT | Stage name |
| emoji | TEXT | |
| display_order | INTEGER | Sort order |
| created_at | TIMESTAMPTZ | |

---

### saas_projects

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK to users |
| name | TEXT | |
| description | TEXT | |
| color | TEXT | Hex, default '#6366F1' |
| status | TEXT | 'active' \| 'completed' \| 'archived' |
| created_at / updated_at / completed_at | TIMESTAMPTZ | |

---

### saas_project_items

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| project_id | UUID | FK to saas_projects |
| item_text | TEXT | |
| is_completed | BOOLEAN | |
| completed_at | TIMESTAMPTZ | |
| display_order | INTEGER | |
| source | TEXT | 'email' \| 'manual' \| 'api' |
| source_email_subject | TEXT | |
| created_at | TIMESTAMPTZ | |

---

### system_events
Audit log for monitoring.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| event_type | TEXT | 'email_sent' \| 'email_failed' \| 'heartbeat' \| 'error' \| 'alert_sent' \| 'canary' \| 'health_digest' |
| category | TEXT | 'reminder' \| 'summary' \| 'canary' \| 'system' \| 'audit' |
| status | TEXT | 'info' \| 'success' \| 'warning' \| 'error' |
| message | TEXT | Human description |
| error_detail | TEXT | Full traceback |
| metadata | JSONB | Context (to_email, task_id, tick, etc.) |
| user_id | UUID | Nullable |
| created_at | TIMESTAMPTZ | |

**Indexes:** (event_type, created_at DESC), (status, created_at DESC)

---

### chat_conversations

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK to users |
| title | VARCHAR(255) | Default 'New Chat' |
| created_at / updated_at | TIMESTAMPTZ | |

---

### chat_messages

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| conversation_id | UUID | FK to chat_conversations |
| role | VARCHAR(20) | 'user' \| 'assistant' \| 'tool_result' |
| content | TEXT | |
| tool_calls | JSONB | Claude tool_use blocks |
| tool_results | JSONB | Executed tool results |
| created_at | TIMESTAMPTZ | |

---

### referrals

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| referrer_id | UUID | FK to users |
| referred_id | UUID | FK to users (UNIQUE) |
| referral_code | TEXT | Code used |
| status | TEXT | 'pending' \| 'trial' \| 'converted' \| 'expired' |
| reward_given | BOOLEAN | |
| created_at / converted_at | TIMESTAMPTZ | |

---

### contacts

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK to users |
| name | TEXT | |
| email | TEXT | |
| phone | TEXT | |
| company | TEXT | |
| crm_id | TEXT | External CRM ID |
| raw_data | JSONB | Full CRM contact object |
| created_at / updated_at | TIMESTAMPTZ | |

---

### support_conversations / support_messages

- **support_conversations:** id, user_id, subject, status, escalated_at, resolved_at, timestamps
- **support_messages:** id, conversation_id, sender_type ('user'|'bot'|'admin'), message, created_at

---

### duplicate_dismissed

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| task_ids | TEXT | |
| user_id | UUID | FK to users |
| created_at | TIMESTAMPTZ | |

---

### organizations

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name | TEXT | Company name |
| slug | TEXT | URL-friendly identifier |
| owner_id | UUID | FK to users |
| created_at | TIMESTAMPTZ | |

---

## Module Reference

### dashboard.py — All Routes

| Route | Method | Auth | Purpose |
|-------|--------|------|---------|
| `/` | GET | None | Homepage / pricing if logged out |
| `/version` | GET | None | App version |
| `/pricing` | GET | None | Pricing page |
| `/login` | GET, POST | None | Login |
| `/signup` | GET, POST | None | Signup |
| `/r/<referral_code>` | GET, POST | None | Signup with referral |
| `/forgot-password` | GET, POST | None | Password reset |
| `/logout` | GET | login_required | Sign out |
| `/dashboard` | GET | login_required | Main task interface |
| `/daily-report` | GET | login_required | Tasks: overdue/today/upcoming |
| `/tasks/create` | POST | login_required | Create task from form |
| `/tasks/<task_id>` | GET | login_required | Task detail + notes |
| `/tasks/<task_id>/edit` | GET, POST | login_required | Edit task |
| `/tasks/<task_id>/delete` | POST | login_required | Delete task |
| `/tasks/<task_id>/complete` | POST | login_required | Mark done |
| `/tasks/<task_id>/reopen` | POST | login_required | Mark pending |
| `/tasks/<task_id>/checklist` | POST | login_required | Update checklist |
| `/tasks/<task_id>/checklist/add` | POST | login_required | Add checklist item |
| `/tasks/<task_id>/notes/add` | POST | login_required | Add note |
| `/tasks/delay` | POST | login_required | Delay task |
| `/api/tasks/<task_id>/checklist/<item_id>/toggle` | POST | login_required | Toggle checklist via AJAX |
| `/api/tasks/<task_id>/delay` | POST | login_required | Delay via AJAX |
| `/api/tasks/<task_id>/title` | POST | login_required | Update title via AJAX |
| `/api/tasks/<task_id>/status` | POST | login_required | Update project status |
| `/api/tasks/cleanup-duplicates` | POST | login_required or internal key | Deduplicate tasks |
| `/api/tasks/reminder-debug` | GET | login_required | Debug upcoming reminders |
| `/shopping-list` | GET | login_required | User's shopping list |
| `/sl/<token>` | GET | Token | Public shared shopping list |
| `/sl/<token>/add` | POST | Token | Add item to shared list |
| `/sl/<token>/toggle/<item_id>` | POST | Token | Check off item |
| `/sl/<token>/clear` | POST | Token | Clear completed |
| `/api/shopping-list/clear` | POST | login_required | Clear completed (API) |
| `/action` | GET | None | Email action handler (complete/delay/reschedule) |
| `/action/<token>` | GET | Token | Passwordless task action |
| `/action/approve` | GET | Token | Approve pending Tier 2 action |
| `/action/reject` | GET | Token | Reject pending action |
| `/action/reschedule_submit` | POST | Token | Submit custom reschedule |
| `/action/task_delete` | POST | Token | Delete from email |
| `/settings` | GET | login_required | Preferences + subscription |
| `/settings/profile` | POST | login_required | Update name/company/timezone |
| `/settings/summary` | POST | login_required | Update summary time |
| `/settings/invite` | POST | login_required | Send referral invite |
| `/projects` | GET | login_required | List projects |
| `/projects/create` | GET, POST | login_required | New project |
| `/projects/<project_id>` | GET | login_required | Project detail |
| `/projects/<project_id>/items/add` | POST | login_required | Add item |
| `/projects/<project_id>/items/<item_id>/toggle` | POST | login_required | Toggle item |
| `/projects/<project_id>/complete` | POST | login_required | Mark done |
| `/projects/<project_id>/reopen` | POST | login_required | Reopen |
| `/projects/<project_id>/delete` | POST | login_required | Delete |
| `/api/internal/generate-token` | POST | Internal API key | Generate action token (for worker) |
| `/api/internal/send-email` | POST | Internal API key | Send email (for worker) |
| `/api/chat/start` | POST | login_required | Create chat conversation |
| `/api/chat/message` | POST | login_required | Send chat message + Claude tools |
| `/health` | GET | None | Health endpoint for uptime monitors |
| `/api/system/health` | GET | None | JSON healthcheck + metrics |
| `/admin` | GET | admin_required | Admin dashboard |
| `/admin/chats/<conversation_id>` | GET | admin | View support chat |
| `/admin/chats/<conversation_id>/reply` | POST | admin | Reply to support |
| `/admin/resend-reminders` | POST | admin | Force-resend today's reminders |
| `/admin/reset-reminders` | POST | admin | Clear reminder_sent_at |
| `/debug-tasks` | GET | admin | View all tasks |
| `/debug-db` | GET | admin | Database health |

---

### auth.py — Authentication

**Decorators:**
- `login_required` — Session check. Returns 401 JSON for `/api/*` paths or `Content-Type: application/json`; 302 redirect to /login otherwise.
- `admin_required` — Requires role='global_admin'
- `company_admin_required` — Requires role='global_admin' or 'company_admin'

**Key Functions:**
- `signup_user(email, password, full_name, timezone)` → (success, user_or_error)
- `login_user(email, password)` → (success, user) — populates session
- `logout_user()` — clears session
- `get_current_user()` → full user dict
- `update_user_profile(user_id, **kwargs)` → (success, error)

---

### task_manager.py — TaskManager Class

**Project Status:** load_project_statuses, get_status_by_name, get_default_status_id, get_next_status, get_previous_status, update_task_status, move_task_to_next_status, move_task_to_previous_status

**Task Notes:** add_note, get_task_notes, get_all_task_notes

**Client Matching:** find_existing_task_by_client(client_email, client_name, project_name, user_id) — priority: email > project_name > client_name. Requires user_id for multi-tenant safety. Excludes cancelled tasks.

**Task CRUD:** create_task, get_task, get_task_with_notes, get_pending_tasks_due_today, complete_task, delay_task (resets reminder_sent_at), update_task_client_info

**Checklist:** get_checklist_items, add_checklist_item (dedup by text), complete_checklist_item, bulk_update_checklist

**Projects:** find_project_by_name, create_project, get_or_create_project, add_project_item (dedup), get_project_items, get_project_with_items, get_active_projects, complete_project_item, bulk_update_project_items

---

### saas_email_processor.py — Email Worker

**Classes:**
- `UserContext` — Dataclass: user_id, email, company, timezone, ai_context, connection_id
- `AIEmailProcessor` — Main processor

**Key Methods:**
- `_get_active_connections()` — Query email_connections with last_sync_at + sync_frequency filtering
- `process_all_connections()` — Multi-tenant loop over all active inboxes
- `fetch_emails_imap(user_context, connection)` — Connect, fetch new messages, decode
- `parse_email_with_claude(email_from, subject, body, user_context)` → JSON: `{ actions: [...], notes: string }`
- `execute_action(action, user_context, task, batch_created)` — Route to Tier 1 (auto) or Tier 2 (approval)
- `_handle_opensolar_accepted()` — Detect OpenSolar "Customer Accepted", format install order
- `_detect_plaud_transcript()` — Extract multiple actions from Plaud voice transcription emails

**Tiering:**
- **Tier 1 (auto):** create_task, set_reminder, categorise
- **Tier 2 (approval email):** update_crm, send_email, create_calendar_event

**Within-batch dedup:** `batch_created` dict — second action for same client adds note to first task instead of creating duplicate.

---

### saas_scheduler.py — Scheduler

**Main Loop:** 60-second tick. Sends reminders and daily summaries.

**Key Functions:**
- `get_reminders_to_send()` — Find pending tasks with due_time within next 60 min, reminder_sent_at IS NULL
- `send_task_reminder(user_id, task, user_timezone)` — Build email + action buttons. **Optimistic locking:** set reminder_sent_at BEFORE send, rollback if Resend fails.
- `get_users_needing_summary()` — Users with daily_summary_enabled=true at their configured time
- `send_daily_summary_email(user_id, user, tasks_summary, projects_summary)` — HTML summary with progress bars

---

### monitoring.py — System Monitoring

**Functions:**
- `log_event(event_type, message, status, ...)` — Insert system_events (fire-and-forget)
- `log_email_send(success, to_email, subject, ...)` — Log email attempt
- `log_heartbeat(tick, ...)` — Worker heartbeat
- `log_error(context, exception, ...)` — Exception with traceback
- `get_system_health()` → dict: worker_status, heartbeat_age_minutes, emails_sent_24h, emails_failed_24h, errors_24h
- `send_self_alert(subject, detail)` — Alert global admins. Throttle: max 3/day, min 30 min apart.
- `check_reminder_health()` — Detect silent reminder failures; sends self_alert if tasks missed
- `check_and_send_canary()` — Send canary email at 7 AM + 5 PM AEST; log result
- `get_last_canary_status()` → dict: status ('ok'|'failed'|'missing'), last_canary
- `check_email_processing_health()` — Audit processed_emails outcomes; alert if 3+ unactioned + 0 tasks created
- `check_imap_health()` → dict: status, results per connection
- `send_daily_health_digest()` — Comprehensive 8 AM AEST health report to global admins
- `cleanup_old_events(days=30)` — Purge old system_events

---

### email_utils.py — Email Sender

- `send_email(to_email, subject, html_body, category, user_id, task_id)` → (success, error)
- Resend API with 2x retry backoff (1s, 2s delays)
- Logs every send attempt to system_events via monitoring.log_email_send()

---

### billing.py — Stripe Integration

**Routes:** /billing/checkout/<plan>, /billing/portal, /billing/success, /billing/cancelled, /billing/webhook

**Plans:** starter ($0), pro ($19/mo or $190/yr), business ($49/mo or $490/yr)

**Webhook events handled:** checkout.session.completed, customer.subscription.updated, customer.subscription.deleted, invoice.payment_failed

**Referral reward:** When referred user converts to paid → both referrer and referred get $5.00 AUD credit.

---

### crm_connectors/ — Plugin Architecture

**Base classes (base.py):**
- `CRMContact` — id, name, email, phone, company, raw_data
- `CRMDeal` — id, title, stage, value, contact_id, raw_data
- `CRMResult` — success, message, data, contact, deal, contacts[]
- `BaseCRMConnector` (ABC) — Abstract: test_connection, find_contact, add_note, get_contact_details. Optional: update_deal_stage, create_contact, refresh_access_token

**Connectors:**
- **pipereply.py** — GHL API (https://rest.gohighlevel.com/v1), Bearer token auth
- **hubspot.py** — HubSpot API (https://api.hubapi.com), OAuth 2.0
- **zoho.py** — Zoho CRM, OAuth 2.0, multi-region (AU: .zohoapis.com.au)

**Factory:** `registry.get_connector(provider, api_key, api_base_url, access_token, settings)` → connector instance

---

### chat.py + chat_tools.py — AI Chat Interface

**Streaming routes:** POST /api/chat/start, POST /api/chat/message (SSE streaming)

**Tools available to Claude:**
- create_task, list_tasks (filter: today/tomorrow/this_week/overdue/all), complete_task, delay_task
- get_overdue_tasks, get_todays_tasks, search_tasks, get_task_details, add_note_to_task

**All tool handlers filter by user_id** — multi-tenant safe.

**System prompt injection:** build_system_prompt(user_id) adds user's pending task count, overdue count, today's date, timezone.

---

### opensolar_connector.py — OpenSolar Integration

- Headless browser (requests + session cookies)
- Methods: authenticate, test_connection, create_project, get_project, list_projects, get_project_details, scrape_proposal, format_install_order

---

## Key Workflows

### 1. Email → Task Pipeline
1. Worker polls all active email_connections every 15 minutes
2. New emails sent to Claude API with solar sales system prompt
3. Claude returns JSON: `{ actions: [{action_type, title, customer_name, ...}], notes }`
4. Tier 1 actions auto-execute (create_task, set_reminder)
5. Tier 2 actions create pending_actions record + send approval email
6. Within-batch dedup: second action for same client adds note, not new task
7. processed_emails record created for dedup (email_id + connection_id + user_id)

### 2. Reminder System
1. Scheduler loops every 60 seconds
2. Finds pending tasks with due_time within next 60 min, reminder_sent_at IS NULL
3. Sets reminder_sent_at BEFORE sending (optimistic locking — prevents duplicate sends)
4. Sends email with Complete / +1hr / +1day / Reschedule buttons
5. Rolls back reminder_sent_at if Resend fails

### 3. Daily Summary
1. Checks all users with daily_summary_enabled=true
2. If current AEST time matches user's daily_summary_time (within 5-minute window)
3. Sends HTML email: overdue tasks, due today, upcoming, project progress bars
4. Updates last_summary_sent_at for dedup

### 4. Chat Tool-Use
1. User sends message → POST /api/chat/message
2. build_system_prompt() injects user context (task stats, date, timezone)
3. Claude returns text + optional tool_use blocks
4. execute_tool() dispatches to chat_tools (queries/modifies tasks in Supabase)
5. Tool results folded back into conversation history
6. Streamed via Flask SSE Response

### 5. Tier 2 Action Approval
1. Email processor identifies high-risk action (e.g. update_crm)
2. pending_actions record created (status='pending', token=uuid)
3. Approval email sent to user with approve/reject links (token in URL)
4. User clicks approve → /action/approve?token=... → execute action, mark approved
5. CRM connector called if crm_synced=false

### 6. Referral Program
1. New user signs up via /r/<referral_code> — referrals record created (status='pending')
2. User starts trial → status='trial'
3. User upgrades to paid → Stripe webhook → handle_checkout_completed()
4. Both referrer + referred get $5.00 referral_credits; status='converted'

---

## Migration History

| # | File | Purpose |
|---|------|---------|
| 001 | 001_saas_schema.sql | Initial schema: users, tasks, email_connections, subscription_plans, RLS |
| 002 | 002_projects_schema.sql | Legacy business_id-based projects |
| 003 | 003_saas_projects.sql | User-based saas_projects + saas_project_items |
| 004 | 004_migrate_to_saas.sql | Migrate old projects, attach user_id |
| 005 | 005_support_chat.sql | support_conversations + support_messages |
| 006 | 006_alternate_emails.sql | Minor column additions |
| 007 | 007_fix_business_id.sql | Minor fix |
| 008 | 008_processed_emails.sql | processed_emails dedup table |
| 009 | 009_reminder_tracking.sql | tasks.reminder_sent_at column |
| 010 | 010_email_action_tokens.sql | email_action_tokens (passwordless email buttons) |
| 011 | 011_subscriptions_referrals.sql | Subscription fields, referrals table, referral code generation |
| 012 | 012_multi_tenant_email.sql | processed_emails: add connection_id, user_id, uid; users.ai_context; email_connections.imap_server |
| 013 | 013_seed_rob_connection.sql | Seed Rob's email connection |
| 014 | 014_crm_connections.sql | crm_connections table |
| 015 | 015_opensolar_provider.sql | Add opensolar to crm_connections.provider |
| 015b | 015_user_ai_context.sql | users.ai_context per-user AI customization |
| 016 | 016_contacts.sql | contacts table (CRM-agnostic directory) |
| 017 | 017_email_history.sql | Email history tracking |
| 018 | 018_system_monitoring.sql | system_events audit log |
| 019 | 019_organizations_roles.sql | organizations + role-based access |
| 020 | 020_processed_emails_outcome.sql | processed_emails.outcome column |
| 021 | 021_chat_tables.sql | chat_conversations + chat_messages |
| 022 | 022_referral_invites.sql | Referral enhancements |
| 023 | 023_fix_rls_security.sql | Enable RLS on all public tables, fix security_invoker views (35 Supabase advisor errors) |

---

## Known Issues & Technical Debt

1. **Two email providers** — Scheduler uses SMTP (privateemail.com); dashboard uses Resend. Should consolidate to Resend everywhere.
2. **Inline HTML templates** — Most of dashboard.py is render_template_string() blobs. Should move to proper template files.
3. **login_required duplicated** — Defined separately in dashboard.py, billing.py, onboarding.py, email_setup.py, auth.py. Use auth.py's version everywhere.
4. **No test suite** — Zero pytest coverage.
5. **No CI/CD pipeline** — Direct push to main triggers Railway auto-deploy.
6. **No error monitoring** — Railway logs + system_events only. No Sentry.
7. **No rate limiting** — API endpoints unprotected.
8. **`/action` route auth** — Token-based actions lack formal validation; audit logging added as interim.
9. **Browser automation shortcuts** — lead-prep, sync-opensolar, install-order, sync-crm are Claude Cowork-only (not server-side).
10. **OpenSolar scraping** — Headless browser is fragile; needs API if available.
11. **business_id legacy field** — tasks.business_id being phased out in favour of user_id.
12. **INTERNAL_API_KEY stale** — Value in CLAUDE.md (`jottask-internal-2026`) may not match Railway env var.
13. **Gmail/Outlook OAuth missing** — Currently IMAP-only.

---

## Local Development

```bash
pip install -r requirements.txt

# Web app (port 5000)
python dashboard.py

# Email worker (polls every 15 min)
python saas_email_processor.py

# Scheduler (reminders every 60 sec)
python saas_scheduler.py

# Tests
pytest

# Database migrations: run SQL files in Supabase SQL Editor
```

---

## Production URLs

- **App:** https://www.jottask.app
- **Health:** https://www.jottask.app/health
- **Supabase Project:** bcrovytubvhrmypefzpe
- **Inbound email:** jottask@flowquote.ai
- **Outbound email:** admin@flowquote.ai
