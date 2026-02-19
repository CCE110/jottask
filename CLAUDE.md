# CLAUDE.md — Jottask SaaS Platform

## Product Vision

Jottask is an AI-powered task management and sales automation platform for the **Australian solar industry**. Currently a single-tenant app used by Rob Lowe at Direct Solar Wholesalers (DSW), the goal is to rebuild it as a **multi-tenant SaaS** that any solar company can sign up, connect their tools, and automate their sales workflow — from lead arrival through to installation handoff.

**Target users:** Solar sales teams (1–20 people per company) across Australia. Each company has their own CRM (PipeReply, HubSpot, Zoho, etc.), but most use **OpenSolar** as their quoting platform.

**Core value prop:** "Forward your emails to Jottask and it does the rest — creates tasks, tracks follow-ups, sends reminders, syncs your CRM, and formats install orders."

---

## Current Architecture (What Exists)

### Tech Stack
- **Backend:** Python 3 / Flask (dashboard + API)
- **Database:** Supabase (PostgreSQL + Auth + RLS)
- **Hosting:** Railway (3 services: web, worker, scheduler)
- **Email inbound:** IMAP polling (privateemail.com — jottask@flowquote.ai)
- **Email outbound:** Resend API (admin@flowquote.ai) + SMTP (jottask@flowquote.ai for scheduler)
- **AI:** Anthropic Claude API (email parsing, task extraction)
- **Payments:** Stripe (billing blueprint exists, not fully wired)
- **Frontend:** Server-rendered HTML/Jinja2 templates (no SPA framework yet)

### Railway Services (Procfile)
```
web: gunicorn dashboard:app --bind 0.0.0.0:$PORT
worker: python3 saas_email_processor.py
scheduler: python3 saas_scheduler.py
```

### Repository Structure
```
jottask/
├── dashboard.py              # Main Flask app (web routes, API, templates)
├── saas_email_processor.py   # Worker: IMAP polling → AI parsing → task creation
├── saas_scheduler.py         # Scheduler: reminders (1-min loop) + daily summaries
├── task_manager.py           # Supabase CRUD for tasks, notes, checklists, statuses
├── auth.py                   # Supabase Auth helpers (signup, login, session)
├── billing.py                # Stripe subscription blueprint (/billing/*)
├── onboarding.py             # New user onboarding flow blueprint (/onboarding/*)
├── email_setup.py            # Email connection setup blueprint (/email/*)
├── approval_routes.py        # Email action token handling (complete/delay from email)
├── scheduler.py              # Legacy scheduler (replaced by saas_scheduler.py)
├── templates/                # HTML templates
├── static/                   # CSS, JS, images
├── migrations/               # SQL migration files (001–011)
├── requirements.txt          # Python dependencies
├── Procfile                  # Railway process definitions
└── railway.toml              # Railway build config (nixpacks/python)
```

### Database Schema (Supabase PostgreSQL)

**Core Tables:**
- `users` — extends Supabase Auth; fields: email, full_name, company_name, phone, timezone, avatar_url, subscription_status (trial/active/cancelled/expired), subscription_tier (starter/pro/business), trial_ends_at, stripe_customer_id, stripe_subscription_id, email_notifications, daily_summary_time, daily_summary_enabled, reminder_minutes_before, onboarding_completed, referral_code, referred_by, referral_credits, tasks_this_month, tasks_month_reset, last_summary_sent_at, last_login_at
- `tasks` — user_id (FK→users), title, description, due_date, due_time, priority (urgent/high/medium/low), status (pending/completed/cancelled), category, client_name, business_id, is_meeting, project_status_id, reminder_sent_at, source, created_at, completed_at
- `task_notes` — task_id (FK→tasks), content, author, created_at
- `task_checklist_items` — task_id (FK→tasks), title, is_completed, position
- `email_connections` — user_id, provider (gmail/outlook/imap), email_address, access_token, refresh_token, imap_password, is_active, last_sync_at, sync_frequency_minutes
- `processed_emails` — tracking which emails have been processed (dedup)
- `pending_actions` — token-based approval flow for Tier 2 actions (AI suggests → user approves via email link); fields: token, action_type, action_data (JSONB), status, user_id, crm_synced, crm_synced_at
- `email_action_tokens` — one-click email actions (complete task, delay, reschedule)
- `subscription_plans` — starter ($0), pro ($19/mo), business ($49/mo) with feature limits
- `referrals` — referrer_id, referred_id, referral_code, status, reward_given
- `project_statuses` — custom workflow statuses per user (display_order based)
- `saas_projects` — project tracking with items
- `saas_project_items` — project checklist items

**Row Level Security:** Enabled on all tables. Users can only access their own data.

### Key Workflows (Currently Working for DSW)

#### 1. Email-to-Task (saas_email_processor.py)
- Polls IMAP inbox every 15 minutes
- Uses Claude API to parse emails into structured tasks
- **Tiered action system:**
  - Tier 1 (auto-execute): Create tasks, set reminders, update status
  - Tier 2 (approval required): CRM updates, external actions → sends approval email with token links
- Detects Plaud voice transcriptions and extracts multiple actions
- Solar sales pipeline awareness (lead stages, quoting, installation)
- Tracks processed emails to avoid duplicates

#### 2. Task Reminders (saas_scheduler.py)
- Runs every 60 seconds
- Checks tasks with due_time set for today
- Sends reminder email 5–20 minutes before due time
- Uses SMTP (privateemail.com) for sending
- Tracks reminder_sent_at to avoid duplicates

#### 3. Daily Summary (saas_scheduler.py)
- Checks at each user's configured summary time (default 8 AM)
- Sends HTML email with overdue, due today, and upcoming tasks
- Includes project progress bars
- Tracks last_summary_sent_at

#### 4. Email Action Buttons
- Every task email includes clickable buttons: Complete, +1 Hour, +1 Day, Reschedule
- Routes through /action endpoint with task_id parameter
- No auth required (token-based)

### DSW-Specific Automations (Cowork Mode Shortcuts — NOT in codebase)

These are currently implemented as Claude Cowork mode shortcuts (browser automation), not server-side code. They need to be **productized** into the SaaS platform:

1. **Lead Prep** (`/lead-prep`) — Takes SMS lead notification, finds contact in PipeReply CRM, scrapes details, creates Mac Contact, summarises CRM notes for scoping call
2. **Sync OpenSolar** (`/sync-opensolar`) — Creates OpenSolar projects for new leads via PipeReply pipeline change, captures project URL
3. **Install Order** (`/install-order`) — Formats WhatsApp message for install team from OpenSolar "Customer Accepted" notification, scrapes equipment + custom line items
4. **Sync CRM** (`/sync-crm`) — Pushes approved CRM updates from Jottask pending_actions to PipeReply

---

## SaaS Rebuild Requirements

### Multi-Tenancy

**Currently:** Single hardcoded user (Rob), single IMAP inbox, single set of business IDs.

**Target:** Each tenant (solar company) signs up, connects their own email, configures their CRM, and gets isolated data.

Key changes needed:
- The `saas_email_processor.py` worker currently connects to ONE IMAP inbox. For multi-tenant, it needs to iterate over all active `email_connections` and poll each user's inbox.
- Business IDs are hardcoded in the processor. Move to per-user configuration.
- Claude AI prompt context needs to be per-user (each company has different categories, workflows, clients).

### Authentication & Onboarding
- **Already exists:** Supabase Auth, signup/login flows, onboarding blueprint
- **Needs:** OAuth for Gmail/Outlook (currently IMAP-only), proper password reset flow, team member invites (business tier)

### Subscription & Billing
- **Already exists:** Stripe integration (billing.py), subscription_plans table, tier enforcement
- **Needs:** Actual Stripe price IDs configured, webhook handling for subscription changes, usage metering (tasks_this_month), plan limit enforcement in email processor

### CRM Integrations (Connector System)
**This is the big new feature.** Each solar company uses a different CRM:
- PipeReply (DSW's current CRM)
- HubSpot
- Zoho
- Salesforce
- Monday.com
- Custom/none

**Architecture needed:**
- Abstract CRM connector interface: `find_contact(name)`, `get_contact_details(id)`, `add_note(id, text)`, `update_pipeline(id, stage)`, `get_opportunity(id)`
- Per-user CRM configuration (API keys, OAuth tokens, field mappings)
- CRM connector registry (pluggable adapters)
- Settings page for connecting/configuring CRM

### OpenSolar Integration
- Most solar companies use OpenSolar for quoting
- Currently browser-automated; needs API integration or headless browser service
- **Key features:** Project creation, proposal scraping (equipment lists, custom line items), customer data sync
- OpenSolar API documentation: https://developer.opensolar.com (if available) or continue with web scraping

### Chat Interface
Rob wants a **smart, intuitive chat interface** where users can:
- Type natural language commands: "Create a task to call John Smith tomorrow at 2pm"
- Ask questions: "What's overdue?" "Show me this week's tasks"
- Trigger automations: "Prep lead for Brad Mills" "Format install order for project 8955463"
- Get AI suggestions: "You have 3 follow-ups overdue, want me to reschedule them?"

**Architecture:**
- WebSocket or SSE for real-time chat
- Claude API as the brain (with function calling / tool use)
- Chat history stored in Supabase
- Context-aware: knows user's tasks, CRM data, calendar
- Tool definitions for task CRUD, CRM lookups, OpenSolar queries

### Dashboard Redesign
Currently server-rendered HTML. Consider:
- Keep Flask backend as API
- Add React/Next.js frontend (or keep progressive enhancement with HTMX)
- Mobile-responsive (solar sales reps work from phones/tablets on-site)
- Real-time updates (task status changes, new emails processed)

---

## Environment Variables

```
# Supabase
SUPABASE_URL=https://bcrovytubvhrmypefzpe.supabase.co
SUPABASE_KEY=<service_role_key>

# Email (inbound IMAP)
JOTTASK_EMAIL=jottask@flowquote.ai
JOTTASK_EMAIL_PASSWORD=<password>
IMAP_SERVER=mail.privateemail.com

# Email (outbound)
RESEND_API_KEY=<resend_key>
FROM_EMAIL=admin@flowquote.ai

# AI
ANTHROPIC_API_KEY=<anthropic_key>

# Stripe
STRIPE_SECRET_KEY=<stripe_secret>
STRIPE_PUBLISHABLE_KEY=<stripe_pub>
STRIPE_WEBHOOK_SECRET=<stripe_webhook>
STRIPE_PRICE_PRO_MONTHLY=<price_id>
STRIPE_PRICE_PRO_YEARLY=<price_id>
STRIPE_PRICE_BUSINESS_MONTHLY=<price_id>
STRIPE_PRICE_BUSINESS_YEARLY=<price_id>

# App
FLASK_SECRET_KEY=<secret>
WEB_SERVICE_URL=https://www.jottask.app
TASK_ACTION_URL=https://www.jottask.app/action
APP_URL=https://www.jottask.app
INTERNAL_API_KEY=jottask-internal-2026

# DSW-specific (move to per-user config in SaaS)
BUSINESS_ID_CCE=feb14276-5c3d-4fcf-af06-9a8f54cf7159
BUSINESS_ID_AIPP=ec5d7aab-8d74-4ef2-9d92-01b143c68c82
```

---

## Known Issues & Technical Debt

1. **Reminder system uses SMTP** (saas_scheduler.py) while **dashboard uses Resend API** — should consolidate to one email provider
2. **Email processor is single-tenant** — polls one IMAP inbox with hardcoded business IDs
3. **No proper error monitoring** — Railway logs only, no Sentry/alerting
4. **Templates are inline HTML strings** — most are render_template_string(), should be proper template files
5. **login_required decorator duplicated** — defined separately in dashboard.py, billing.py, onboarding.py, email_setup.py, auth.py
6. **No test suite** — zero automated tests
7. **No CI/CD pipeline** — direct push to main triggers Railway auto-deploy
8. **Browser automation shortcuts** (lead-prep, install-order, sync-crm, sync-opensolar) are Cowork-only — need server-side equivalents for SaaS
9. **OpenSolar integration is web scraping** — fragile, needs API or more robust approach
10. **No rate limiting** on API endpoints
11. **No webhook receiver** for real-time CRM/email events (currently relies on polling)

---

## Recommended Build Sequence

### Phase 1: Foundation (Week 1–2)
- [ ] Set up proper project structure (separate frontend/backend or monorepo)
- [ ] Add test framework (pytest) with initial tests for task_manager.py
- [ ] Consolidate email sending to Resend API everywhere
- [ ] Extract inline HTML templates to proper template files
- [ ] Fix duplicate login_required decorators (use auth.py's version everywhere)
- [ ] Add basic CI (GitHub Actions: lint + test)

### Phase 2: Multi-Tenant Email Processing (Week 3–4)
- [ ] Refactor saas_email_processor.py to iterate over email_connections table
- [ ] Per-user AI context (custom categories, business names, client lists)
- [ ] Per-user email connection management UI (email_setup.py enhancement)
- [ ] Gmail OAuth integration (most solar companies use Google Workspace)
- [ ] Outlook OAuth integration

### Phase 3: CRM Connector Framework (Week 5–7)
- [ ] Design abstract CRM connector interface
- [ ] Implement PipeReply connector (server-side API, not browser automation)
- [ ] Implement HubSpot connector
- [ ] CRM configuration UI (connect, map fields, test connection)
- [ ] Migrate sync-crm and lead-prep logic to server-side connectors

### Phase 4: Chat Interface (Week 8–10)
- [ ] Design chat data model (conversations, messages, tool calls)
- [ ] Implement Claude tool-use backend (task CRUD, CRM lookups, scheduling)
- [ ] Build chat UI (WebSocket/SSE, message rendering, typing indicators)
- [ ] Add context injection (user's tasks, recent emails, CRM data)
- [ ] Natural language task creation and management

### Phase 5: OpenSolar Integration (Week 11–12)
- [ ] Research OpenSolar API availability
- [ ] Build proposal scraper service (headless browser or API)
- [ ] Install order formatter (server-side, replaces /install-order shortcut)
- [ ] Project creation automation (replaces /sync-opensolar shortcut)

### Phase 6: Billing & Launch (Week 13–14)
- [ ] Wire up Stripe subscription flow end-to-end
- [ ] Implement plan limits enforcement
- [ ] Referral system activation
- [ ] Landing page / marketing site
- [ ] Production monitoring (Sentry, uptime checks)
- [ ] Beta launch with 3–5 solar companies

---

## File-by-File Reference

| File | Lines | Purpose | SaaS Status |
|------|-------|---------|-------------|
| `dashboard.py` | ~4000 | Main web app, all routes, templates | Needs refactoring into smaller modules |
| `saas_email_processor.py` | ~920 | IMAP polling + AI task creation | Single-tenant, needs multi-tenant refactor |
| `saas_scheduler.py` | ~575 | Reminders + daily summaries | Already multi-user aware |
| `task_manager.py` | ~300 | Supabase CRUD helper class | Good, needs minor updates |
| `auth.py` | ~100 | Authentication helpers | Good, needs OAuth additions |
| `billing.py` | ~200 | Stripe subscription management | Scaffolded, needs completion |
| `onboarding.py` | ~300 | New user setup wizard | Good, needs CRM step added |
| `email_setup.py` | ~200 | Email connection UI | Good, needs OAuth flow |
| `approval_routes.py` | ~150 | Email action token handling | Good |

---

## Development Commands

```bash
# Local development
pip install -r requirements.txt
python dashboard.py          # Run web app locally (port 5000)
python saas_email_processor.py  # Run email worker
python saas_scheduler.py     # Run scheduler

# Deploy (auto via Railway on git push to main)
git add . && git commit -m "message" && git push origin main

# Database migrations (run in Supabase SQL Editor)
# Files in migrations/ directory, numbered 001–011
```

---

## Important Context for Claude Code

- **Rob is the sole developer** — he builds by describing what he wants and having AI write the code
- **The app is LIVE in production** on Railway — be careful with breaking changes
- **Supabase RLS is enabled** — all table queries need proper auth context or service_role_key
- **Australian solar industry specifics:** AEST timezone, AUD pricing, Australian phone formats (+61), state-based solar rebates (STCs), NEM/grid connection requirements
- **OpenSolar is the dominant quoting tool** in Australian solar — almost every installer uses it
- **PipeReply is a niche CRM** — the SaaS version should support popular CRMs first (HubSpot, Zoho)
- **The chat interface is a key differentiator** — Rob envisions users "talking" to Jottask like a smart assistant that knows their pipeline, tasks, and clients
