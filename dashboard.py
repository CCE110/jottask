"""
Jottask Dashboard - Main Web Application
Full SaaS task management interface
"""

import os
import re
import json
from flask import Flask, render_template_string, render_template, request, redirect, url_for, session, jsonify, flash
from datetime import datetime, timedelta
from functools import wraps
import pytz
from supabase import create_client, Client

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Register blueprints
from auth import login_required, admin_required
from email_utils import send_email
from billing import billing_bp
from onboarding import onboarding_bp
from email_setup import email_setup_bp
from crm_setup import crm_setup_bp
from chat import chat_bp
from squad_routes import squad_bp
app.register_blueprint(billing_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(email_setup_bp)
app.register_blueprint(crm_setup_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(squad_bp)

# Supabase client — lazy-init proxy. Defers create_client() until the first
# .table()/.auth/.rpc() call so a missing env var at import time can't crash
# gunicorn before any request is served. Mirrors auth._LazySupabase. Uses
# the service-role key (via db_keys.get_admin_key) so RLS-locked-down tables
# remain writable from the Flask backend.
from db_keys import get_admin_key


class _LazySupabase:
    def __init__(self):
        self._client = None

    def _ensure(self):
        if self._client is None:
            url = os.getenv('SUPABASE_URL')
            key = get_admin_key()
            if not url or not key:
                raise RuntimeError(
                    "Supabase env vars missing — set SUPABASE_URL and "
                    "SUPABASE_SERVICE_KEY (or SUPABASE_KEY) on the running service."
                )
            self._client = create_client(url, key)
        return self._client

    def __getattr__(self, name):
        return getattr(self._ensure(), name)


SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = get_admin_key()  # service-role first; was anon-only, blocked writes after RLS
supabase = _LazySupabase()

# ── Lead-text junk filter ────────────────────────────────────────────────────
# Mirrors dsw_lead_poller.filter_junk_lines + _strip_html so the lead detail
# page cleans the same noise. Kept local to avoid importing the heavy
# dsw_lead_poller module (anthropic/resend/opensolar) at dashboard startup.
# Keep pattern list in sync with dsw_lead_poller._JUNK_PATTERNS.
_LEAD_JUNK_PATTERNS = [
    # SolarQuotes API dump
    r'verified phone',
    r'phone number verified',
    r'phone.+verified',
    r'consent(?:ed)?\b',
    r'lead submitted',
    r'\bsubmission\b',
    r'requested quotes',
    r'quote count',
    r'number of quotes',
    r'roof ownership',
    r'north[\s\-]?facing',
    r'\bsupplier\s*id\b',
    r'\bsupplierid\b',
    r'\bsuppliername\b',
    r'\bidleadsupplier\b',
    r'^\s*claimed\s*:',
    r'^\s*id\s*:\s*\S',
    # DSW Energy system-note noise
    r'link\.dswenergy\.com\.au',
    r'^\s*system added note',
    r'click here',
    r'\bB-\d+-WF-',
    r'sales meeting status',
    r'site inspection form',
    r'appt\s+to\s+quote',
    r'appt\s+confirmed',
]
_LEAD_JUNK_RE = re.compile('|'.join(_LEAD_JUNK_PATTERNS), re.IGNORECASE)

# Minimal HTML → plain text. Mirrors dsw_lead_poller._strip_html — any changes
# should be applied to both. Used for CRM notes + user-pasted MY NOTES.
_LEAD_HTML_BLOCK_RE = re.compile(r'<(script|style)[^>]*>.*?</\1>', re.IGNORECASE | re.DOTALL)
_LEAD_HTML_BR_RE    = re.compile(r'<\s*br\s*/?\s*>', re.IGNORECASE)
_LEAD_HTML_BLK_END  = re.compile(r'</\s*(p|div|li|h[1-6]|tr)\s*>', re.IGNORECASE)
_LEAD_HTML_TAG_RE   = re.compile(r'<[^>]+>')
_LEAD_HTML_ENTITIES = {
    '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>',
    '&quot;': '"', '&#39;': "'", '&#x27;': "'", '&apos;': "'",
}


def _strip_lead_html(text):
    """Convert HTML fragments to plain text. No-op if no '<' present."""
    if not text or '<' not in text:
        return text
    t = _LEAD_HTML_BLOCK_RE.sub('', text)
    t = _LEAD_HTML_BR_RE.sub('\n', t)
    t = _LEAD_HTML_BLK_END.sub('\n', t)
    t = _LEAD_HTML_TAG_RE.sub('', t)
    for k, v in _LEAD_HTML_ENTITIES.items():
        t = t.replace(k, v)
    lines = [re.sub(r'[ \t]+', ' ', ln).strip() for ln in t.splitlines()]
    return '\n'.join(lines)


def _filter_lead_junk(text):
    """Strip HTML, drop junk-pattern lines, collapse blank runs."""
    if not text:
        return text
    text = _strip_lead_html(text)
    kept = []
    prev_blank = False
    for raw in text.splitlines():
        if _LEAD_JUNK_RE.search(raw):
            continue
        if raw.strip() == '':
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        kept.append(raw)
    return '\n'.join(kept).strip()


# ── Lead tags ────────────────────────────────────────────────────────────────
# Display metadata for the 5 tag types defined in migration 031. (label, color,
# bg, border) drives the pill rendering in the email + lead detail page.
LEAD_TAG_META = {
    'v2g':          ('⚡ V2G Ready',    '#7c3aed', '#ede9fe', '#c4b5fd'),
    'three_phase':  ('🔌 3 Phase',      '#0369a1', '#dbeafe', '#93c5fd'),
    'single_phase': ('🔌 Single Phase', '#0891b2', '#cffafe', '#67e8f9'),
    'battery':      ('🔋 Battery',      '#10b981', '#d1fae5', '#6ee7b7'),
    'ev_charger':   ('🚗 EV Charger',   '#f59e0b', '#fef3c7', '#fcd34d'),
}
LEAD_TAG_KEYS = list(LEAD_TAG_META.keys())

# Patterns used by the retrospective scan (only v2g + three_phase + single_phase
# get auto-tagged from existing notes; battery + ev_charger are checkbox-only)
LEAD_TAG_SCAN_PATTERNS = {
    'v2g':          [r'\bv2g\b', r'vehicle[\s\-]?to[\s\-]?grid'],
    'three_phase':  [r'\bthree[\s\-]?phase\b', r'\b3[\s\-]?phase\b'],
    'single_phase': [r'\bsingle[\s\-]?phase\b', r'\b1[\s\-]?phase\b'],
}


def _fetch_task_tags(task_id):
    """Return a set of tag strings currently set on this task."""
    try:
        r = supabase.table('lead_tags').select('tag').eq('task_id', task_id).execute()
        return {row['tag'] for row in (r.data or [])}
    except Exception as e:
        print(f"[lead_tags] fetch failed for {task_id[:8]}: {e}")
        return set()


def _set_task_tag(task_id, tag, enabled):
    """Add or remove a single tag on a task. Idempotent."""
    if tag not in LEAD_TAG_META:
        raise ValueError(f"unknown tag: {tag!r}")
    if enabled:
        try:
            supabase.table('lead_tags').insert({'task_id': task_id, 'tag': tag}).execute()
        except Exception as e:
            # Unique violation — already tagged. Treat as success.
            if '23505' not in str(e) and 'duplicate' not in str(e).lower():
                raise
    else:
        supabase.table('lead_tags').delete()\
            .eq('task_id', task_id).eq('tag', tag).execute()


def _render_tag_pill_html(tag, *, size='sm'):
    """Render one tag as a coloured HTML pill. Used in the reminder email
    header and the lead detail tags section."""
    meta = LEAD_TAG_META.get(tag)
    if not meta:
        return ''
    label, fg, bg, border = meta
    pad = '3px 9px' if size == 'sm' else '5px 12px'
    fs = '11px' if size == 'sm' else '13px'
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'border:1px solid {border};padding:{pad};border-radius:14px;'
        f'font-size:{fs};font-weight:700;margin:2px 4px 2px 0;'
        f'white-space:nowrap;">{label}</span>'
    )


def _render_tag_pills_block(tags):
    """Render a row of pills for the given iterable of tag keys (header use)."""
    if not tags:
        return ''
    # Preserve canonical order regardless of input order
    ordered = [t for t in LEAD_TAG_KEYS if t in tags]
    if not ordered:
        return ''
    pills = ''.join(_render_tag_pill_html(t) for t in ordered)
    return f'<div style="margin:6px 0 0;line-height:1.9">{pills}</div>'


# Shopping list secret token (allows access without login)
SHOPPING_LIST_TOKEN = os.getenv('SHOPPING_LIST_TOKEN', '')
SHOPPING_LIST_USER_EMAIL = os.getenv('SHOPPING_LIST_USER_EMAIL', '')

@app.context_processor
def inject_shopping_list_url():
    """Make shopping list token URL available to all templates."""
    if SHOPPING_LIST_TOKEN:
        return {'shopping_list_url': f'/sl/{SHOPPING_LIST_TOKEN}'}
    return {'shopping_list_url': '/shopping-list'}

# Admin notification settings
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'admin@flowquote.ai')
RESEND_API_KEY = os.getenv('RESEND_API_KEY')
FROM_EMAIL = os.getenv('FROM_EMAIL', 'jottask@flowquote.ai')


def send_admin_notification(subject, body_html):
    """Send notification email to admin using Resend"""
    success, error = send_email(ADMIN_EMAIL, f'[Jottask Admin] {subject}', body_html)
    if not success:
        print(f"Failed to send admin notification: {error}")
    return success


def send_task_confirmation_email(user_email, task_title, due_date, due_time, task_id, user_name=None):
    """Send confirmation email when a task is created from dashboard"""
    WEB_SERVICE_URL = os.getenv('WEB_SERVICE_URL', 'https://www.jottask.app')

    print(f"📧 Attempting task confirmation email from dashboard:")
    print(f"    To: {user_email}")
    print(f"    Task: {task_title}")
    print(f"    Task ID: {task_id}")
    print(f"    Due: {due_date} {due_time}")

    # Build action URLs
    action_base = f"{WEB_SERVICE_URL}/action"
    complete_url = f"{action_base}?action=complete&task_id={task_id}"
    delay_1hour_url = f"{action_base}?action=delay_1hour&task_id={task_id}"
    delay_1day_url = f"{action_base}?action=delay_1day&task_id={task_id}"
    delay_next_day_8am_url = f"{action_base}?action=delay_next_day_8am&task_id={task_id}"
    delay_next_day_9am_url = f"{action_base}?action=delay_next_day_9am&task_id={task_id}"
    delay_next_monday_9am_url = f"{action_base}?action=delay_next_monday_9am&task_id={task_id}"
    reschedule_url = f"{action_base}?action=delay_custom&task_id={task_id}"

    greeting = f"Hi {user_name}," if user_name else "Hi,"
    due_display = f"{due_date} at {due_time[:5]}" if due_time else due_date

    html_content = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%); padding: 24px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 24px;">✅ Task Created</h1>
        </div>
        <div style="background: #f9fafb; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
            <p style="color: #374151;">{greeting}</p>
            <p style="color: #374151;">Your task has been created:</p>
            <div style="background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin: 16px 0;">
                <h3 style="margin: 0 0 8px 0; color: #111827;">{task_title}</h3>
                <p style="margin: 0; color: #6b7280; font-size: 14px;">Due: {due_display}</p>
            </div>
            <div style="margin-top: 16px; text-align: center;">
                <a href="{complete_url}" style="display: inline-block; background: #10B981; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">✅ Complete</a>
                <a href="{delay_1hour_url}" style="display: inline-block; background: #6b7280; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">⏰ +1 Hour</a>
                <a href="{delay_1day_url}" style="display: inline-block; background: #6b7280; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">📅 +1 Day</a>
                <a href="{delay_next_day_8am_url}" style="display: inline-block; background: #0EA5E9; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">🌅 Tmrw 8am</a>
                <a href="{delay_next_day_9am_url}" style="display: inline-block; background: #0EA5E9; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">☀️ Tmrw 9am</a>
                <a href="{delay_next_monday_9am_url}" style="display: inline-block; background: #F59E0B; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">📆 Mon 9am</a>
                <a href="{reschedule_url}" style="display: inline-block; background: #6366F1; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">🗓️ Change Time</a>
            </div>
            <p style="color: #6b7280; font-size: 13px; margin-top: 16px;">You'll receive a reminder 5-20 minutes before this task is due.</p>
        </div>
        <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 24px;">
            Jottask - AI-Powered Task Management
        </p>
    </body>
    </html>
    """

    success, error = send_email(user_email, f"Task Created: {task_title}", html_content,
                               category='confirmation', task_id=task_id)

    if success:
        print(f"✅ Task confirmation email SENT successfully to {user_email}")
    else:
        print(f"❌ Task confirmation email FAILED for {user_email}: {error}")

    return success


# ============================================
# AUTH HELPERS
# ============================================

def get_user_timezone():
    return pytz.timezone(session.get('timezone', 'Australia/Brisbane'))


# ============================================
# SUBSCRIPTION HELPERS
# ============================================

TIER_LIMITS = {
    'free_trial': {'tasks_per_month': 20, 'projects': True},
    'starter': {'tasks_per_month': 100, 'projects': True},
    'pro': {'tasks_per_month': float('inf'), 'projects': True},
    'business': {'tasks_per_month': float('inf'), 'projects': True},
}

def get_user_subscription(user_id):
    """Get user's subscription status and limits"""
    user = supabase.table('users').select(
        'subscription_tier, trial_ends_at, tasks_this_month, tasks_month_reset, referral_code, referral_credits'
    ).eq('id', user_id).single().execute()

    if not user.data:
        return {'tier': 'free_trial', 'can_create_task': False, 'reason': 'User not found'}

    data = user.data
    tier = data.get('subscription_tier', 'free_trial')
    trial_ends = data.get('trial_ends_at')
    tasks_this_month = data.get('tasks_this_month', 0)
    month_reset = data.get('tasks_month_reset')

    # Check if we need to reset monthly count
    today = datetime.now(pytz.timezone('Australia/Brisbane')).date()
    if month_reset:
        reset_date = datetime.fromisoformat(month_reset).date() if isinstance(month_reset, str) else month_reset
        if today.month != reset_date.month or today.year != reset_date.year:
            # Reset count for new month
            supabase.table('users').update({
                'tasks_this_month': 0,
                'tasks_month_reset': today.replace(day=1).isoformat()
            }).eq('id', user_id).execute()
            tasks_this_month = 0

    # Check trial expiry
    if tier == 'free_trial' and trial_ends:
        trial_end_date = datetime.fromisoformat(trial_ends.replace('Z', '+00:00')) if isinstance(trial_ends, str) else trial_ends
        if datetime.now(pytz.UTC) > trial_end_date:
            return {
                'tier': tier,
                'can_create_task': False,
                'reason': 'Trial expired',
                'tasks_used': tasks_this_month,
                'tasks_limit': TIER_LIMITS[tier]['tasks_per_month'],
                'trial_expired': True
            }

    # Check task limit
    limit = TIER_LIMITS.get(tier, TIER_LIMITS['free_trial'])['tasks_per_month']
    can_create = tasks_this_month < limit

    return {
        'tier': tier,
        'can_create_task': can_create,
        'reason': None if can_create else f'Monthly limit reached ({limit} tasks)',
        'tasks_used': tasks_this_month,
        'tasks_limit': limit,
        'trial_expired': False,
        'referral_code': data.get('referral_code'),
        'referral_credits': data.get('referral_credits', 0)
    }

def increment_task_count(user_id):
    """Increment user's monthly task count"""
    supabase.rpc('increment_task_count', {'user_id_param': user_id}).execute()
    # Fallback if RPC doesn't exist
    try:
        user = supabase.table('users').select('tasks_this_month').eq('id', user_id).single().execute()
        current = user.data.get('tasks_this_month', 0) if user.data else 0
        supabase.table('users').update({'tasks_this_month': current + 1}).eq('id', user_id).execute()
    except:
        pass

# ============================================
# BASE TEMPLATE
# ============================================

BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} - Jottask</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
    <link rel="manifest" href="/static/manifest.json">
    <meta name="theme-color" content="#6366F1">
    <style>
        :root {
            --primary: #6366F1;
            --primary-dark: #4F46E5;
            --success: #10B981;
            --warning: #F59E0B;
            --danger: #EF4444;
            --gray-50: #F9FAFB;
            --gray-100: #F3F4F6;
            --gray-200: #E5E7EB;
            --gray-300: #D1D5DB;
            --gray-500: #6B7280;
            --gray-700: #374151;
            --gray-900: #111827;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--gray-50);
            color: var(--gray-900);
            line-height: 1.5;
        }

        /* Navigation */
        .nav {
            background: white;
            border-bottom: 1px solid var(--gray-200);
            padding: 0 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            height: 64px;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .nav-brand {
            display: flex;
            align-items: center;
            gap: 12px;
            text-decoration: none;
            color: var(--primary);
            font-weight: 700;
            font-size: 20px;
        }

        .nav-brand svg {
            width: 32px;
            height: 32px;
        }

        .nav-links {
            display: flex;
            gap: 8px;
        }

        .nav-link {
            padding: 8px 16px;
            text-decoration: none;
            color: var(--gray-700);
            border-radius: 8px;
            font-weight: 500;
            transition: all 0.2s;
        }

        .nav-link:hover, .nav-link.active {
            background: var(--gray-100);
            color: var(--primary);
        }

        .nav-user {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .avatar {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: var(--primary);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
        }

        /* Main Layout */
        .main {
            max-width: 1200px;
            margin: 0 auto;
            padding: 24px;
        }

        /* Cards */
        .card {
            background: white;
            border-radius: 12px;
            border: 1px solid var(--gray-200);
            overflow: hidden;
        }

        .card-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--gray-200);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .card-title {
            font-size: 16px;
            font-weight: 600;
        }

        .card-body {
            padding: 20px;
        }

        /* Buttons */
        .btn {
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            border: none;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s;
        }

        .btn-primary {
            background: var(--primary);
            color: white;
        }

        .btn-primary:hover {
            background: var(--primary-dark);
        }

        .btn-secondary {
            background: var(--gray-100);
            color: var(--gray-700);
        }

        .btn-secondary:hover {
            background: var(--gray-200);
        }

        .btn-success {
            background: var(--success);
            color: white;
        }

        .btn-danger {
            background: var(--danger);
            color: white;
        }

        .btn-sm {
            padding: 6px 12px;
            font-size: 13px;
        }

        /* Forms */
        .form-group {
            margin-bottom: 16px;
        }

        .form-label {
            display: block;
            margin-bottom: 6px;
            font-weight: 500;
            font-size: 14px;
            color: var(--gray-700);
        }

        .form-input {
            width: 100%;
            padding: 10px 14px;
            border: 1px solid var(--gray-300);
            border-radius: 8px;
            font-size: 14px;
            transition: all 0.2s;
        }

        .form-input:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
        }

        /* Task List */
        .task-list {
            display: flex;
            flex-direction: column;
        }

        .task-item {
            display: flex;
            align-items: center;
            padding: 16px 20px;
            border-bottom: 1px solid var(--gray-100);
            gap: 16px;
            transition: background 0.2s;
        }

        .task-item:hover {
            background: var(--gray-50);
        }

        .task-item:last-child {
            border-bottom: none;
        }

        .task-checkbox {
            width: 22px;
            height: 22px;
            border-radius: 50%;
            border: 2px solid var(--gray-300);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            transition: all 0.2s;
        }

        .task-checkbox:hover {
            border-color: var(--success);
            background: rgba(16, 185, 129, 0.1);
        }

        .task-checkbox.completed {
            background: var(--success);
            border-color: var(--success);
        }

        .task-content {
            flex: 1;
            min-width: 0;
        }

        .task-title {
            font-weight: 500;
            color: var(--gray-900);
            margin-bottom: 4px;
        }

        .task-meta {
            display: flex;
            gap: 16px;
            font-size: 13px;
            color: var(--gray-500);
        }

        .task-actions {
            display: flex;
            gap: 8px;
            opacity: 0;
            transition: opacity 0.2s;
        }

        .task-item:hover .task-actions {
            opacity: 1;
        }

        /* Status Badge */
        .status-badge {
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }

        /* Priority */
        .priority-high { color: var(--danger); }
        .priority-medium { color: var(--warning); }
        .priority-low { color: var(--success); }

        /* Stats */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .stat-card {
            background: white;
            border-radius: 12px;
            border: 1px solid var(--gray-200);
            padding: 20px;
        }

        .stat-value {
            font-size: 32px;
            font-weight: 700;
            color: var(--gray-900);
        }

        .stat-label {
            font-size: 14px;
            color: var(--gray-500);
            margin-top: 4px;
        }

        /* Modal */
        .modal-backdrop {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            opacity: 0;
            visibility: hidden;
            transition: all 0.3s;
        }

        .modal-backdrop.active {
            opacity: 1;
            visibility: visible;
        }

        .modal {
            background: white;
            border-radius: 16px;
            width: 100%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
            transform: scale(0.9);
            transition: transform 0.3s;
        }

        .modal-backdrop.active .modal {
            transform: scale(1);
        }

        .modal-header {
            padding: 20px;
            border-bottom: 1px solid var(--gray-200);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .modal-title {
            font-size: 18px;
            font-weight: 600;
        }

        .modal-close {
            background: none;
            border: none;
            font-size: 24px;
            cursor: pointer;
            color: var(--gray-500);
        }

        .modal-body {
            padding: 20px;
        }

        .modal-footer {
            padding: 16px 20px;
            border-top: 1px solid var(--gray-200);
            display: flex;
            gap: 12px;
            justify-content: flex-end;
        }

        /* Delay Buttons */
        .delay-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 12px;
        }

        .delay-btn {
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            border: 1px solid var(--gray-200);
            background: white;
            cursor: pointer;
            transition: all 0.2s;
        }

        .delay-btn:hover {
            border-color: var(--primary);
            background: rgba(99, 102, 241, 0.05);
        }

        /* Empty State */
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--gray-500);
        }

        .empty-state-icon {
            font-size: 48px;
            margin-bottom: 16px;
        }

        /* Tabs */
        .tabs {
            display: flex;
            gap: 4px;
            border-bottom: 1px solid var(--gray-200);
            margin-bottom: 20px;
        }

        .tab {
            padding: 12px 20px;
            font-weight: 500;
            color: var(--gray-500);
            border-bottom: 2px solid transparent;
            cursor: pointer;
            transition: all 0.2s;
        }

        .tab:hover {
            color: var(--gray-700);
        }

        .tab.active {
            color: var(--primary);
            border-bottom-color: var(--primary);
        }

        /* Alert */
        .alert {
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 16px;
            font-size: 14px;
        }

        .alert-success {
            background: #D1FAE5;
            color: #065F46;
        }

        .alert-error {
            background: #FEE2E2;
            color: #991B1B;
        }

        /* Auth Pages */
        .auth-container {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
            padding: 20px;
        }

        .auth-card {
            background: white;
            border-radius: 16px;
            padding: 40px;
            width: 100%;
            max-width: 420px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.2);
        }

        .auth-logo {
            text-align: center;
            margin-bottom: 32px;
        }

        .auth-logo svg {
            width: 48px;
            height: 48px;
        }

        .auth-logo h1 {
            color: var(--primary);
            font-size: 24px;
            margin-top: 12px;
        }

        /* Responsive */
        @media (max-width: 768px) {
            .nav {
                padding: 0 16px;
            }

            .nav-links {
                display: none;
            }

            .main {
                padding: 16px;
            }

            .task-actions {
                opacity: 1;
            }
        }
    </style>
</head>
<body>
    {% block content %}{% endblock %}

    <script>
        // Modal handling
        function openModal(modalId) {
            document.getElementById(modalId).classList.add('active');
        }

        function closeModal(modalId) {
            document.getElementById(modalId).classList.remove('active');
        }

        // Task completion
        async function toggleTask(taskId, checkbox) {
            const isCompleted = checkbox.classList.contains('completed');
            const newStatus = isCompleted ? 'pending' : 'completed';

            try {
                const response = await fetch(`/api/tasks/${taskId}/status`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: newStatus })
                });

                if (response.ok) {
                    checkbox.classList.toggle('completed');
                    const taskItem = checkbox.closest('.task-item');
                    taskItem.setAttribute('data-status', newStatus);
                    if (newStatus === 'completed') {
                        checkbox.innerHTML = '✓';
                        taskItem.style.opacity = '0.6';
                    } else {
                        checkbox.innerHTML = '';
                        taskItem.style.opacity = '1';
                    }
                    // Re-apply current tab filter
                    const activeTab = document.querySelector('.tabs .tab.active');
                    if (activeTab) activeTab.click();
                }
            } catch (err) {
                console.error('Failed to update task:', err);
            }
        }

        // Quick delay
        async function delayTask(taskId, hours, days) {
            try {
                const response = await fetch(`/api/tasks/${taskId}/delay`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hours, days })
                });

                if (response.ok) {
                    location.reload();
                }
            } catch (err) {
                console.error('Failed to delay task:', err);
            }
        }

        async function delayTaskPreset(taskId, preset) {
            try {
                const response = await fetch(`/api/tasks/${taskId}/delay`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ preset })
                });

                if (response.ok) {
                    location.reload();
                }
            } catch (err) {
                console.error('Failed to delay task:', err);
            }
        }
    </script>
</body>
</html>
"""

# ============================================
# DASHBOARD TEMPLATE
# ============================================

DASHBOARD_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<nav class="nav">
    <a href="{{ url_for('dashboard') }}" class="nav-brand">
        <svg viewBox="0 0 512 512" width="32" height="32">
            <defs>
                <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" style="stop-color:#8B5CF6" />
                    <stop offset="100%" style="stop-color:#6366F1" />
                </linearGradient>
            </defs>
            <rect width="512" height="512" rx="96" fill="white"/>
            <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
            <circle cx="310" cy="350" r="70" fill="#10B981"/>
            <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Jottask
    </a>

    <div class="nav-links">
        <a href="{{ url_for('dashboard') }}" class="nav-link active">Tasks</a>
        <a href="{{ url_for('projects') }}" class="nav-link">Projects</a>
        <a href="{{ url_for('settings') }}" class="nav-link">Settings</a>
    </div>

    <div class="nav-user">
        <span style="color: var(--gray-500);">{{ session.user_name }}</span>
        <div class="avatar">{{ session.user_name[0].upper() }}</div>
        <a href="{{ url_for('logout') }}" class="btn btn-secondary btn-sm">Logout</a>
    </div>
</nav>

<main class="main">
    <!-- Stats -->
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-value">{{ stats.pending }}</div>
            <div class="stat-label">Pending Tasks</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.due_today }}</div>
            <div class="stat-label">Due Today</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.overdue }}</div>
            <div class="stat-label">Overdue</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.completed_this_week }}</div>
            <div class="stat-label">Completed This Week</div>
        </div>
    </div>

    <!-- Task Card -->
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Tasks</h2>
            <button class="btn btn-primary" onclick="openModal('new-task-modal')">
                + New Task
            </button>
        </div>

        <div class="tabs" style="padding: 0 20px;">
            <div class="tab active" data-filter="all">All</div>
            <div class="tab" data-filter="today">Today</div>
            <div class="tab" data-filter="overdue">Overdue</div>
            <div class="tab" data-filter="completed">Completed</div>
        </div>

        <div class="task-list">
            {% if tasks %}
                {% for task in tasks %}
                <div class="task-item" data-task-id="{{ task.id }}" data-status="{{ task.status }}" data-due-date="{{ task.due_date or '' }}">
                    <div class="task-checkbox {% if task.status == 'completed' %}completed{% endif %}"
                         onclick="toggleTask('{{ task.id }}', this)">
                        {% if task.status == 'completed' %}✓{% endif %}
                    </div>

                    <div class="task-content">
                        <div class="task-title">{{ task.title }}</div>
                        <div class="task-meta">
                            <span class="priority-{{ task.priority }}">{{ task.priority|capitalize }}</span>
                            <span>Due: {{ task.due_date }} {{ task.due_time[:5] if task.due_time else '' }}</span>
                            {% if task.client_name %}
                            <span>{{ task.client_name }}</span>
                            {% endif %}
                        </div>
                    </div>

                    <div class="task-actions">
                        <button class="btn btn-secondary btn-sm" onclick="openEditModal('{{ task.id }}')">Edit</button>
                        <button class="btn btn-secondary btn-sm" onclick="openDelayModal('{{ task.id }}')">Delay</button>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">
                    <div class="empty-state-icon">📋</div>
                    <h3>No tasks yet</h3>
                    <p>Create your first task to get started</p>
                </div>
            {% endif %}
        </div>
    </div>
</main>

<!-- New Task Modal -->
<div id="new-task-modal" class="modal-backdrop" onclick="if(event.target === this) closeModal('new-task-modal')">
    <div class="modal">
        <div class="modal-header">
            <h3 class="modal-title">New Task</h3>
            <button class="modal-close" onclick="closeModal('new-task-modal')">&times;</button>
        </div>
        <form method="POST" action="{{ url_for('create_task') }}">
            <div class="modal-body">
                <div class="form-group">
                    <label class="form-label">Title</label>
                    <input type="text" name="title" class="form-input" required>
                </div>

                <div class="form-group">
                    <label class="form-label">Description</label>
                    <textarea name="description" class="form-input" rows="3"></textarea>
                </div>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                    <div class="form-group">
                        <label class="form-label">Due Date</label>
                        <input type="date" name="due_date" class="form-input" value="{{ today }}">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Due Time</label>
                        <input type="time" name="due_time" class="form-input" value="09:00">
                    </div>
                </div>

                <div class="form-group">
                    <label class="form-label">Priority</label>
                    <select name="priority" class="form-input">
                        <option value="low">Low</option>
                        <option value="medium" selected>Medium</option>
                        <option value="high">High</option>
                        <option value="urgent">Urgent</option>
                    </select>
                </div>

                <div class="form-group">
                    <label class="form-label">Client Name (optional)</label>
                    <input type="text" name="client_name" class="form-input">
                </div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-secondary" onclick="closeModal('new-task-modal')">Cancel</button>
                <button type="submit" class="btn btn-primary">Create Task</button>
            </div>
        </form>
    </div>
</div>

<!-- Delay Modal -->
<div id="delay-modal" class="modal-backdrop" onclick="if(event.target === this) closeModal('delay-modal')">
    <div class="modal">
        <div class="modal-header">
            <h3 class="modal-title">Delay Task</h3>
            <button class="modal-close" onclick="closeModal('delay-modal')">&times;</button>
        </div>
        <div class="modal-body">
            <p style="color: var(--gray-500); margin-bottom: 16px;">Quick delay options:</p>
            <div class="delay-buttons">
                <button class="delay-btn" onclick="delayTask(currentTaskId, 1, 0)">+1 Hour</button>
                <button class="delay-btn" onclick="delayTask(currentTaskId, 3, 0)">+3 Hours</button>
                <button class="delay-btn" onclick="delayTask(currentTaskId, 0, 1)">+1 Day</button>
                <button class="delay-btn" onclick="delayTask(currentTaskId, 0, 7)">+1 Week</button>
                <button class="delay-btn" style="background:#0EA5E9;color:white;" onclick="delayTaskPreset(currentTaskId, 'next_day_8am')">🌅 Tmrw 8am</button>
                <button class="delay-btn" style="background:#0EA5E9;color:white;" onclick="delayTaskPreset(currentTaskId, 'next_day_9am')">☀️ Tmrw 9am</button>
                <button class="delay-btn" style="background:#F59E0B;color:white;" onclick="delayTaskPreset(currentTaskId, 'next_monday_9am')">📆 Mon 9am</button>
            </div>

            <hr style="margin: 20px 0; border: none; border-top: 1px solid var(--gray-200);">

            <form method="POST" action="{{ url_for('delay_task_custom') }}" id="custom-delay-form">
                <input type="hidden" name="task_id" id="delay-task-id">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                    <div class="form-group">
                        <label class="form-label">New Date</label>
                        <input type="date" name="new_date" class="form-input" value="{{ today }}">
                    </div>
                    <div class="form-group">
                        <label class="form-label">New Time</label>
                        <input type="time" name="new_time" class="form-input" value="09:00">
                    </div>
                </div>
                <button type="submit" class="btn btn-primary" style="width: 100%;">Set Custom Date/Time</button>
            </form>
        </div>
    </div>
</div>

<script>
    let currentTaskId = null;

    function openDelayModal(taskId) {
        currentTaskId = taskId;
        document.getElementById('delay-task-id').value = taskId;
        openModal('delay-modal');
    }

    function openEditModal(taskId) {
        window.location.href = `/tasks/${taskId}/edit`;
    }

    // Tab filtering
    const today = '{{ today }}';
    document.querySelectorAll('.tabs .tab').forEach(tab => {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.tabs .tab').forEach(t => t.classList.remove('active'));
            this.classList.add('active');
            const filter = this.getAttribute('data-filter');
            document.querySelectorAll('.task-item').forEach(item => {
                const status = item.getAttribute('data-status');
                const dueDate = item.getAttribute('data-due-date');
                let show = false;
                if (filter === 'all') {
                    show = (status === 'pending');
                } else if (filter === 'today') {
                    show = (status === 'pending' && dueDate === today);
                } else if (filter === 'overdue') {
                    show = (status === 'pending' && dueDate && dueDate < today);
                } else if (filter === 'completed') {
                    show = (status === 'completed');
                }
                item.style.display = show ? '' : 'none';
            });
        });
    });
    // Apply default filter on load - show pending tasks (All tab)
    document.querySelector('.tabs .tab[data-filter="all"]').click();
</script>
{% endblock %}
"""

# ============================================
# SETTINGS TEMPLATE
# ============================================

SETTINGS_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<nav class="nav">
    <a href="{{ url_for('dashboard') }}" class="nav-brand">
        <svg viewBox="0 0 512 512" width="32" height="32">
            <defs>
                <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" style="stop-color:#8B5CF6" />
                    <stop offset="100%" style="stop-color:#6366F1" />
                </linearGradient>
            </defs>
            <rect width="512" height="512" rx="96" fill="white"/>
            <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
            <circle cx="310" cy="350" r="70" fill="#10B981"/>
            <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Jottask
    </a>

    <div class="nav-links">
        <a href="{{ url_for('dashboard') }}" class="nav-link">Tasks</a>
        <a href="{{ url_for('projects') }}" class="nav-link">Projects</a>
        <a href="{{ url_for('settings') }}" class="nav-link active">Settings</a>
    </div>

    <div class="nav-user">
        <span style="color: var(--gray-500);">{{ session.user_name }}</span>
        <div class="avatar">{{ session.user_name[0].upper() }}</div>
        <a href="{{ url_for('logout') }}" class="btn btn-secondary btn-sm">Logout</a>
    </div>
</nav>

<main class="main">
    {% if message %}
    <div class="alert alert-success">{{ message }}</div>
    {% endif %}

    <div style="display: grid; grid-template-columns: 250px 1fr; gap: 24px;">
        <!-- Sidebar -->
        <div>
            <div class="card">
                <div class="card-body" style="padding: 8px;">
                    <a href="#profile" class="nav-link active" style="display: block;">Profile</a>
                    <a href="#notifications" class="nav-link" style="display: block;">Notifications</a>
                    <a href="#email" class="nav-link" style="display: block;">Email Connection</a>
                    <a href="#subscription" class="nav-link" style="display: block;">Subscription</a>
                </div>
            </div>
        </div>

        <!-- Content -->
        <div>
            <!-- Profile Section -->
            <div class="card" style="margin-bottom: 24px;">
                <div class="card-header">
                    <h2 class="card-title">Profile Settings</h2>
                </div>
                <div class="card-body">
                    <form method="POST" action="{{ url_for('update_profile') }}">
                        <div class="form-group">
                            <label class="form-label">Full Name</label>
                            <input type="text" name="full_name" class="form-input" value="{{ user.full_name or '' }}">
                        </div>

                        <div class="form-group">
                            <label class="form-label">Email</label>
                            <input type="email" class="form-input" value="{{ user.email }}" disabled>
                            <small style="color: var(--gray-500);">Contact support to change email</small>
                        </div>

                        <div class="form-group">
                            <label class="form-label">Company Name</label>
                            <input type="text" name="company_name" class="form-input" value="{{ user.company_name or '' }}">
                        </div>

                        <div class="form-group">
                            <label class="form-label">Timezone</label>
                            <select name="timezone" class="form-input">
                                <option value="Australia/Brisbane" {% if user.timezone == 'Australia/Brisbane' %}selected{% endif %}>Australia/Brisbane (AEST)</option>
                                <option value="Australia/Sydney" {% if user.timezone == 'Australia/Sydney' %}selected{% endif %}>Australia/Sydney</option>
                                <option value="Australia/Melbourne" {% if user.timezone == 'Australia/Melbourne' %}selected{% endif %}>Australia/Melbourne</option>
                                <option value="America/New_York" {% if user.timezone == 'America/New_York' %}selected{% endif %}>US Eastern</option>
                                <option value="Europe/London" {% if user.timezone == 'Europe/London' %}selected{% endif %}>UK (GMT/BST)</option>
                            </select>
                        </div>

                        <button type="submit" class="btn btn-primary">Save Changes</button>
                    </form>
                </div>
            </div>

            <!-- Daily Summary Section -->
            <div class="card" style="margin-bottom: 24px;">
                <div class="card-header">
                    <h2 class="card-title">Daily Summary</h2>
                </div>
                <div class="card-body">
                    <form method="POST" action="{{ url_for('update_summary_settings') }}">
                        <div class="form-group">
                            <label style="display: flex; align-items: center; gap: 12px; cursor: pointer;">
                                <input type="checkbox" name="daily_summary_enabled" {% if user.daily_summary_enabled %}checked{% endif %} style="width: 20px; height: 20px;">
                                <span class="form-label" style="margin: 0;">Enable daily summary email</span>
                            </label>
                            <small style="color: var(--gray-500); display: block; margin-top: 8px;">
                                Receive a daily email with your tasks and projects overview
                            </small>
                        </div>

                        <div class="form-group">
                            <label class="form-label">Summary Time</label>
                            <select name="daily_summary_time" class="form-input" style="max-width: 200px;">
                                <option value="06:00:00" {% if user.daily_summary_time == '06:00:00' %}selected{% endif %}>6:00 AM</option>
                                <option value="07:00:00" {% if user.daily_summary_time == '07:00:00' %}selected{% endif %}>7:00 AM</option>
                                <option value="08:00:00" {% if user.daily_summary_time == '08:00:00' or not user.daily_summary_time %}selected{% endif %}>8:00 AM</option>
                                <option value="09:00:00" {% if user.daily_summary_time == '09:00:00' %}selected{% endif %}>9:00 AM</option>
                            </select>
                            <small style="color: var(--gray-500);">Time in your local timezone ({{ user.timezone }})</small>
                        </div>

                        <button type="submit" class="btn btn-primary">Save Settings</button>
                    </form>
                </div>
            </div>

            <!-- Subscription Section -->
            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">Subscription</h2>
                </div>
                <div class="card-body">
                    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
                        <span class="status-badge" style="background: {% if user.subscription_status == 'active' %}var(--success){% elif user.subscription_status == 'trial' %}var(--warning){% else %}var(--gray-300){% endif %}; color: white;">
                            {{ user.subscription_status|capitalize }}
                        </span>
                        <span style="font-weight: 600;">{{ user.subscription_tier|capitalize }} Plan</span>
                    </div>

                    {% if user.subscription_status == 'trial' %}
                    <p style="color: var(--gray-500); margin-bottom: 20px;">
                        Your trial ends on {{ user.trial_ends_at[:10] if user.trial_ends_at else 'soon' }}
                    </p>
                    {% endif %}

                    <a href="{{ url_for('billing') }}" class="btn btn-primary">Manage Subscription</a>
                </div>
            </div>
        </div>
    </div>
</main>
{% endblock %}
"""

# ============================================
# PROJECTS TEMPLATES
# ============================================

PROJECTS_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<nav class="nav">
    <a href="{{ url_for('dashboard') }}" class="nav-brand">
        <svg viewBox="0 0 512 512" width="32" height="32">
            <defs>
                <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" style="stop-color:#8B5CF6" />
                    <stop offset="100%" style="stop-color:#6366F1" />
                </linearGradient>
            </defs>
            <rect width="512" height="512" rx="96" fill="white"/>
            <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
            <circle cx="310" cy="350" r="70" fill="#10B981"/>
            <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Jottask
    </a>

    <div class="nav-links">
        <a href="{{ url_for('dashboard') }}" class="nav-link">Tasks</a>
        <a href="{{ url_for('projects') }}" class="nav-link active">Projects</a>
        <a href="{{ url_for('settings') }}" class="nav-link">Settings</a>
    </div>

    <div class="nav-user">
        <span style="color: var(--gray-500);">{{ session.user_name }}</span>
        <div class="avatar">{{ session.user_name[0].upper() }}</div>
        <a href="{{ url_for('logout') }}" class="btn btn-secondary btn-sm">Logout</a>
    </div>
</nav>

<main class="main">
    <!-- Stats -->
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-value">{{ stats.active }}</div>
            <div class="stat-label">Active Projects</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.total_items }}</div>
            <div class="stat-label">Total Items</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.completed_items }}</div>
            <div class="stat-label">Items Completed</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{{ stats.completed_projects }}</div>
            <div class="stat-label">Projects Completed</div>
        </div>
    </div>

    <!-- Projects Card -->
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Projects</h2>
            <a href="{{ url_for('project_create') }}" class="btn btn-primary">
                + New Project
            </a>
        </div>

        <div class="tabs" style="padding: 0 20px;">
            <div class="tab {% if filter == 'active' %}active{% endif %}" onclick="location.href='?filter=active'">Active</div>
            <div class="tab {% if filter == 'completed' %}active{% endif %}" onclick="location.href='?filter=completed'">Completed</div>
            <div class="tab {% if filter == 'archived' %}active{% endif %}" onclick="location.href='?filter=archived'">Archived</div>
            <div class="tab {% if filter == 'all' %}active{% endif %}" onclick="location.href='?filter=all'">All</div>
        </div>

        <div class="project-list">
            {% if projects %}
                {% for project in projects %}
                <a href="{{ url_for('project_detail', project_id=project.id) }}" class="project-item" style="text-decoration: none; color: inherit;">
                    <div class="project-color" style="background: {{ project.color or '#6366F1' }};"></div>

                    <div class="project-content">
                        <div class="project-title">{{ project.name }}</div>
                        <div class="project-meta">
                            <span>{{ project.item_count or 0 }} items</span>
                            {% if project.description %}
                            <span>{{ project.description[:50] }}{% if project.description|length > 50 %}...{% endif %}</span>
                            {% endif %}
                        </div>
                    </div>

                    <div class="project-progress">
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: {{ project.progress or 0 }}%;"></div>
                        </div>
                        <span class="progress-text">{{ project.progress or 0 }}%</span>
                    </div>

                    <div class="project-status">
                        <span class="status-badge" style="background: {% if project.status == 'active' %}var(--primary){% elif project.status == 'completed' %}var(--success){% else %}var(--gray-300){% endif %}; color: white;">
                            {{ project.status|capitalize }}
                        </span>
                    </div>
                </a>
                {% endfor %}
            {% else %}
                <div class="empty-state">
                    <div class="empty-state-icon">📁</div>
                    <h3>No projects yet</h3>
                    <p>Create your first project or email "Project: Name - items" to jottask@flowquote.ai</p>
                </div>
            {% endif %}
        </div>
    </div>
</main>

<style>
.project-list {
    display: flex;
    flex-direction: column;
}

.project-item {
    display: flex;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid var(--gray-100);
    gap: 16px;
    transition: background 0.2s;
}

.project-item:hover {
    background: var(--gray-50);
}

.project-item:last-child {
    border-bottom: none;
}

.project-color {
    width: 8px;
    height: 48px;
    border-radius: 4px;
    flex-shrink: 0;
}

.project-content {
    flex: 1;
    min-width: 0;
}

.project-title {
    font-weight: 600;
    color: var(--gray-900);
    margin-bottom: 4px;
}

.project-meta {
    display: flex;
    gap: 16px;
    font-size: 13px;
    color: var(--gray-500);
}

.project-progress {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 150px;
}

.progress-bar {
    flex: 1;
    height: 8px;
    background: var(--gray-200);
    border-radius: 4px;
    overflow: hidden;
}

.progress-fill {
    height: 100%;
    background: var(--success);
    transition: width 0.3s;
}

.progress-text {
    font-size: 13px;
    color: var(--gray-500);
    min-width: 35px;
}

.project-status {
    flex-shrink: 0;
}

@media (max-width: 768px) {
    .project-progress {
        display: none;
    }
}
</style>
{% endblock %}
"""

PROJECT_DETAIL_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<nav class="nav">
    <a href="{{ url_for('dashboard') }}" class="nav-brand">
        <svg viewBox="0 0 512 512" width="32" height="32">
            <defs>
                <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" style="stop-color:#8B5CF6" />
                    <stop offset="100%" style="stop-color:#6366F1" />
                </linearGradient>
            </defs>
            <rect width="512" height="512" rx="96" fill="white"/>
            <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
            <circle cx="310" cy="350" r="70" fill="#10B981"/>
            <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Jottask
    </a>
    <div class="nav-user">
        <a href="{{ url_for('projects') }}" class="btn btn-secondary btn-sm">← Back to Projects</a>
    </div>
</nav>

<main class="main" style="max-width: 800px;">
    <!-- Project Header -->
    <div class="card" style="margin-bottom: 24px;">
        <div class="card-body">
            <div style="display: flex; align-items: start; justify-content: space-between; margin-bottom: 16px;">
                <div style="display: flex; align-items: center; gap: 16px;">
                    <div style="width: 12px; height: 48px; border-radius: 6px; background: {{ project.color or '#6366F1' }};"></div>
                    <div>
                        <h1 style="font-size: 24px; margin-bottom: 4px;">{{ project.name }}</h1>
                        <span class="status-badge" style="background: {% if project.status == 'active' %}var(--primary){% elif project.status == 'completed' %}var(--success){% else %}var(--gray-300){% endif %}; color: white;">
                            {{ project.status|capitalize }}
                        </span>
                    </div>
                </div>
                <div style="display: flex; gap: 8px;">
                    {% if project.status == 'active' %}
                    <form method="POST" action="{{ url_for('project_complete', project_id=project.id) }}" style="display: inline;">
                        <button type="submit" class="btn btn-success btn-sm">Mark Complete</button>
                    </form>
                    {% elif project.status == 'completed' %}
                    <form method="POST" action="{{ url_for('project_reopen', project_id=project.id) }}" style="display: inline;">
                        <button type="submit" class="btn btn-secondary btn-sm">Reopen</button>
                    </form>
                    {% endif %}
                    <form method="POST" action="{{ url_for('project_delete', project_id=project.id) }}" onsubmit="return confirm('Delete this project and all its items?');" style="display: inline;">
                        <button type="submit" class="btn btn-danger btn-sm">Delete</button>
                    </form>
                </div>
            </div>

            {% if project.description %}
            <p style="color: var(--gray-600); margin-bottom: 16px;">{{ project.description }}</p>
            {% endif %}

            <!-- Progress Bar -->
            <div style="display: flex; align-items: center; gap: 12px;">
                <div style="flex: 1; height: 12px; background: var(--gray-200); border-radius: 6px; overflow: hidden;">
                    <div style="height: 100%; background: var(--success); width: {{ progress }}%; transition: width 0.3s;"></div>
                </div>
                <span style="font-weight: 600; color: var(--gray-700);">{{ completed_count }}/{{ total_count }} ({{ progress }}%)</span>
            </div>
        </div>
    </div>

    <!-- Checklist Items -->
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Checklist</h3>
        </div>
        <div class="card-body">
            {% if items %}
            <div class="checklist">
                {% for item in items %}
                <div class="checklist-item {% if item.is_completed %}completed{% endif %}">
                    <form method="POST" action="{{ url_for('project_item_toggle', project_id=project.id, item_id=item.id) }}" style="display: contents;">
                        <button type="submit" class="item-checkbox {% if item.is_completed %}checked{% endif %}">
                            {% if item.is_completed %}✓{% endif %}
                        </button>
                    </form>
                    <span class="item-text">{{ item.item_text }}</span>
                    <span class="item-source" style="font-size: 11px; color: var(--gray-400); margin-left: auto;">{{ item.source }}</span>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <p style="color: var(--gray-500); text-align: center; padding: 20px;">No items yet</p>
            {% endif %}

            <!-- Add Item Form -->
            <form method="POST" action="{{ url_for('project_item_add', project_id=project.id) }}" style="margin-top: 20px; display: flex; gap: 8px;">
                <input type="text" name="item_text" class="form-input" placeholder="Add a checklist item..." required style="flex: 1;">
                <button type="submit" class="btn btn-primary">Add</button>
            </form>
        </div>
    </div>

    <!-- Project Info -->
    <div class="card" style="margin-top: 24px;">
        <div class="card-header">
            <h3 class="card-title">Project Details</h3>
        </div>
        <div class="card-body" style="font-size: 14px;">
            <div style="display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--gray-100);">
                <span style="color: var(--gray-500);">Created</span>
                <span>{{ project.created_at[:10] if project.created_at else 'N/A' }}</span>
            </div>
            {% if project.completed_at %}
            <div style="display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--gray-100);">
                <span style="color: var(--gray-500);">Completed</span>
                <span>{{ project.completed_at[:10] }}</span>
            </div>
            {% endif %}
            <div style="display: flex; justify-content: space-between; padding: 8px 0;">
                <span style="color: var(--gray-500);">Color</span>
                <div style="width: 20px; height: 20px; border-radius: 4px; background: {{ project.color or '#6366F1' }};"></div>
            </div>
        </div>
    </div>
</main>

<style>
.checklist {
    display: flex;
    flex-direction: column;
}

.checklist-item {
    display: flex;
    align-items: center;
    padding: 12px 0;
    border-bottom: 1px solid var(--gray-100);
    gap: 12px;
}

.checklist-item:last-child {
    border-bottom: none;
}

.item-checkbox {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    border: 2px solid var(--gray-300);
    background: white;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    color: white;
    transition: all 0.2s;
    flex-shrink: 0;
}

.item-checkbox:hover {
    border-color: var(--success);
    background: rgba(16, 185, 129, 0.1);
}

.item-checkbox.checked {
    background: var(--success);
    border-color: var(--success);
}

.checklist-item.completed .item-text {
    text-decoration: line-through;
    color: var(--gray-400);
}

.item-text {
    flex: 1;
    color: var(--gray-700);
}
</style>
{% endblock %}
"""

PROJECT_CREATE_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<nav class="nav">
    <a href="{{ url_for('dashboard') }}" class="nav-brand">
        <svg viewBox="0 0 512 512" width="32" height="32">
            <defs>
                <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" style="stop-color:#8B5CF6" />
                    <stop offset="100%" style="stop-color:#6366F1" />
                </linearGradient>
            </defs>
            <rect width="512" height="512" rx="96" fill="white"/>
            <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
            <circle cx="310" cy="350" r="70" fill="#10B981"/>
            <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Jottask
    </a>
    <div class="nav-user">
        <a href="{{ url_for('projects') }}" class="btn btn-secondary btn-sm">← Back to Projects</a>
    </div>
</nav>

<main class="main" style="max-width: 600px;">
    <div class="card">
        <div class="card-header">
            <h2 class="card-title">Create New Project</h2>
        </div>
        <form method="POST" class="card-body">
            <div class="form-group">
                <label class="form-label">Project Name *</label>
                <input type="text" name="name" class="form-input" placeholder="Website Redesign" required>
            </div>

            <div class="form-group">
                <label class="form-label">Description</label>
                <textarea name="description" class="form-input" rows="3" placeholder="Project goals and details..."></textarea>
            </div>

            <div class="form-group">
                <label class="form-label">Color</label>
                <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                    <label class="color-option">
                        <input type="radio" name="color" value="#6366F1" checked>
                        <span style="background: #6366F1;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#8B5CF6">
                        <span style="background: #8B5CF6;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#EC4899">
                        <span style="background: #EC4899;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#EF4444">
                        <span style="background: #EF4444;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#F59E0B">
                        <span style="background: #F59E0B;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#10B981">
                        <span style="background: #10B981;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#3B82F6">
                        <span style="background: #3B82F6;"></span>
                    </label>
                    <label class="color-option">
                        <input type="radio" name="color" value="#6B7280">
                        <span style="background: #6B7280;"></span>
                    </label>
                </div>
            </div>

            <div class="form-group">
                <label class="form-label">Initial Checklist Items (optional)</label>
                <textarea name="initial_items" class="form-input" rows="4" placeholder="One item per line:&#10;Design mockups&#10;Build frontend&#10;Test and deploy"></textarea>
                <small style="color: var(--gray-500);">Enter one item per line</small>
            </div>

            <div style="display: flex; gap: 12px; margin-top: 24px;">
                <button type="submit" class="btn btn-primary" style="flex: 1;">Create Project</button>
                <a href="{{ url_for('projects') }}" class="btn btn-secondary">Cancel</a>
            </div>
        </form>
    </div>
</main>

<style>
.color-option {
    cursor: pointer;
}

.color-option input {
    display: none;
}

.color-option span {
    display: block;
    width: 36px;
    height: 36px;
    border-radius: 8px;
    border: 3px solid transparent;
    transition: all 0.2s;
}

.color-option input:checked + span {
    border-color: var(--gray-900);
    transform: scale(1.1);
}

.color-option:hover span {
    transform: scale(1.05);
}
</style>
{% endblock %}
"""

# ============================================
# ROUTES
# ============================================

@app.route('/version')
def version():
    """Debug endpoint to check deployment version"""
    return "v2.5-action-fix"

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    # Show landing page for non-logged in users
    return render_template('landing.html')


@app.route('/pricing')
def pricing_page():
    from billing import PLANS

    current_plan = 'starter'
    subscription_status = 'none'

    if 'user_id' in session:
        user = supabase.table('users').select('subscription_tier, subscription_status').eq('id', session['user_id']).single().execute()
        if user.data:
            current_plan = user.data.get('subscription_tier', 'starter')
            subscription_status = user.data.get('subscription_status', 'none')

    return render_template(
        'pricing.html',
        title='Pricing',
        plans=PLANS,
        current_plan=current_plan,
        subscription_status=subscription_status
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').lower()
        password = request.form.get('password', '')

        try:
            auth_response = supabase.auth.sign_in_with_password({
                'email': email,
                'password': password
            })

            if auth_response.user:
                session.permanent = True
                session['user_id'] = auth_response.user.id
                session['user_email'] = auth_response.user.email

                # Get user profile
                user = supabase.table('users').select('*').eq('id', auth_response.user.id).single().execute()
                if user.data:
                    session['user_name'] = user.data.get('full_name', email.split('@')[0])
                    session['timezone'] = user.data.get('timezone', 'Australia/Brisbane')
                    session['user_role'] = user.data.get('role', 'user')
                    session['organization_id'] = user.data.get('organization_id')
                else:
                    session['user_name'] = email.split('@')[0]
                    session['timezone'] = 'Australia/Brisbane'
                    session['user_role'] = 'user'

                return redirect(url_for('dashboard'))
            else:
                return render_template('login.html', error='Invalid credentials')

        except Exception as e:
            error_msg = 'Invalid email or password'
            return render_template('login.html', error=error_msg)

    return render_template('login.html', error=None)


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if email:
            try:
                supabase.auth.reset_password_email(email)
            except Exception:
                pass  # Don't reveal whether email exists
        # Always show success (security: don't reveal if email exists)
        return render_template('forgot_password.html', sent=True, email=email)
    return render_template('forgot_password.html', sent=False, email=None)


@app.route('/signup', methods=['GET', 'POST'])
@app.route('/r/<referral_code>', methods=['GET', 'POST'])
def signup(referral_code=None):
    # Get referral code from URL or form
    ref_code = referral_code or request.args.get('ref') or request.form.get('referral_code')

    if request.method == 'POST':
        email = request.form.get('email', '').lower()
        password = request.form.get('password', '')
        full_name = request.form.get('full_name', '')
        timezone = request.form.get('timezone', 'Australia/Brisbane')
        ref_code = request.form.get('referral_code', '').strip().upper()

        # Look up referrer if referral code provided
        referrer_id = None
        if ref_code:
            referrer = supabase.table('users').select('id').eq('referral_code', ref_code).execute()
            if referrer.data:
                referrer_id = referrer.data[0]['id']

        try:
            # Create auth user
            auth_response = supabase.auth.sign_up({
                'email': email,
                'password': password
            })

            if auth_response.user:
                # Create user profile with subscription info
                import hashlib
                new_ref_code = hashlib.md5(f"{auth_response.user.id}jottask{datetime.now(pytz.UTC).timestamp()}".encode()).hexdigest()[:8].upper()

                user_data = {
                    'id': auth_response.user.id,
                    'email': email,
                    'full_name': full_name,
                    'timezone': timezone,
                    'subscription_status': 'trial',
                    'subscription_tier': 'starter',
                    'trial_ends_at': (datetime.now(pytz.UTC) + timedelta(days=14)).isoformat(),
                    'referral_code': new_ref_code,
                    'tasks_this_month': 0,
                    'tasks_month_reset': datetime.now(pytz.timezone('Australia/Brisbane')).date().replace(day=1).isoformat()
                }

                if referrer_id:
                    user_data['referred_by'] = referrer_id

                supabase.table('users').insert(user_data).execute()

                # Create referral record if referred
                if referrer_id:
                    supabase.table('referrals').insert({
                        'referrer_id': referrer_id,
                        'referred_id': auth_response.user.id,
                        'referral_code': ref_code,
                        'status': 'trial'
                    }).execute()

                # Update referral invite status if this email was invited
                try:
                    supabase.table('referral_invites') \
                        .update({'status': 'signed_up', 'signed_up_at': datetime.now(pytz.UTC).isoformat()}) \
                        .eq('invited_email', email) \
                        .eq('status', 'sent') \
                        .execute()
                except Exception:
                    pass  # Table may not exist yet

                # Log them in
                session['user_id'] = auth_response.user.id
                session['user_email'] = email
                session['user_name'] = full_name
                session['timezone'] = timezone

                # Notify admin of new signup
                referral_info = f"<p><strong>Referred by:</strong> {ref_code}</p>" if ref_code else ""
                send_admin_notification(
                    f"New Signup: {full_name}",
                    f"""
                    <h2>New User Signup</h2>
                    <p><strong>Name:</strong> {full_name}</p>
                    <p><strong>Email:</strong> {email}</p>
                    <p><strong>Timezone:</strong> {timezone}</p>
                    {referral_info}
                    <p><strong>Time:</strong> {datetime.now(pytz.timezone('Australia/Brisbane')).strftime('%Y-%m-%d %H:%M')} AEST</p>
                    <hr>
                    <p><a href="https://www.jottask.app/admin">View Admin Dashboard</a></p>
                    """
                )

                return redirect(url_for('dashboard'))

        except Exception as e:
            error_msg = str(e)
            print(f"Signup error: {error_msg}")
            if 'already registered' in error_msg.lower() or 'already been registered' in error_msg.lower():
                error_msg = 'This email is already registered. Try logging in instead.'
            elif 'password' in error_msg.lower() and ('short' in error_msg.lower() or 'least' in error_msg.lower()):
                error_msg = 'Password must be at least 6 characters.'
            else:
                error_msg = 'Something went wrong. Please try again or contact support.'
            return render_template('signup.html', error=error_msg, referral_code=ref_code)

    return render_template('signup.html', error=None, referral_code=ref_code)


@app.route('/logout')
def logout():
    try:
        supabase.auth.sign_out()
    except:
        pass
    session.clear()
    return redirect(url_for('login'))


@app.route('/debug-tasks')
@admin_required
def debug_tasks():
    """Debug endpoint to check task user_id mapping (admin only)"""
    user_id = session['user_id']
    user_email = session.get('user_email')

    # Get tasks for this user
    user_tasks = supabase.table('tasks').select('id, title, status, user_id').eq('user_id', user_id).limit(10).execute()

    # Get all pending tasks to see their user_ids
    all_pending = supabase.table('tasks').select('id, title, user_id').eq('status', 'pending').limit(10).execute()

    # Count pending tasks by user_id
    pending_count = supabase.table('tasks').select('user_id', count='exact').eq('status', 'pending').execute()

    debug_info = f"""
    <h2>Debug Task Info</h2>
    <p><strong>Session user_id:</strong> {user_id}</p>
    <p><strong>Session email:</strong> {user_email}</p>
    <p><strong>Role:</strong> {session.get('user_role', 'unknown')}</p>
    <hr>
    <h3>Tasks for your user_id ({len(user_tasks.data) if user_tasks.data else 0} found):</h3>
    <ul>
    {''.join(f"<li>{t.get('title', 'N/A')[:50]} - status: {t.get('status')}</li>" for t in (user_tasks.data or []))}
    </ul>
    <hr>
    <h3>Sample pending tasks (any user_id):</h3>
    <ul>
    {''.join(f"<li>{t.get('title', 'N/A')[:50]} - user_id: {t.get('user_id')}</li>" for t in (all_pending.data or []))}
    </ul>
    <hr>
    <p>Total pending tasks count: {pending_count.count if pending_count else 'N/A'}</p>
    <a href="/dashboard">Back to Dashboard</a>
    """
    return debug_info


@app.route('/debug-db')
@admin_required
def debug_db():
    """Debug endpoint to check database state (admin only)"""
    expected_user_id = session['user_id']

    # Get status distribution
    all_statuses = supabase.table('tasks').select('status').limit(500).execute()
    status_counts = {}
    for t in (all_statuses.data or []):
        s = repr(t.get('status'))  # Use repr to see exact value including None/quotes
        status_counts[s] = status_counts.get(s, 0) + 1

    # Get pending tasks for expected user
    pending_for_user = supabase.table('tasks').select('id, title, status').eq('user_id', expected_user_id).eq('status', 'pending').limit(10).execute()

    # Get any pending tasks
    any_pending = supabase.table('tasks').select('id, title, status, user_id').eq('status', 'pending').limit(10).execute()

    # Get sample of all tasks to see actual status values
    sample_tasks = supabase.table('tasks').select('title, status, user_id').limit(20).execute()

    # Get users
    users = supabase.table('users').select('id, email').execute()

    debug_info = f"""
    <html><body style="font-family: monospace; padding: 20px;">
    <h2>Database Debug (Public - Remove Later)</h2>
    <h3>Status Distribution (first 500 tasks):</h3>
    <pre>{status_counts}</pre>

    <h3>Pending tasks for expected user ({len(pending_for_user.data) if pending_for_user.data else 0}):</h3>
    <ul>
    {''.join(f"<li>{t.get('title', 'N/A')[:50]} - status: {repr(t.get('status'))}</li>" for t in (pending_for_user.data or [])) or '<li>None found</li>'}
    </ul>

    <h3>Any pending tasks in DB ({len(any_pending.data) if any_pending.data else 0}):</h3>
    <ul>
    {''.join(f"<li>{t.get('title', 'N/A')[:40]} | status={repr(t.get('status'))} | user={t.get('user_id')[:8]}...</li>" for t in (any_pending.data or [])) or '<li>None found</li>'}
    </ul>

    <h3>Sample of 20 tasks (any status):</h3>
    <table border="1" cellpadding="5">
    <tr><th>Title</th><th>Status (repr)</th><th>User ID</th></tr>
    {''.join(f"<tr><td>{t.get('title', 'N/A')[:35]}</td><td>{repr(t.get('status'))}</td><td>{str(t.get('user_id'))[:12]}...</td></tr>" for t in (sample_tasks.data or []))}
    </table>

    <h3>Users:</h3>
    <ul>
    {''.join(f"<li>{u.get('email')} - {u.get('id')}</li>" for u in (users.data or []))}
    </ul>
    <p><strong>Expected user_id:</strong> {expected_user_id}</p>
    </body></html>
    """
    return debug_info


@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    tz = get_user_timezone()
    today = datetime.now(tz).date().isoformat()
    search_query = request.args.get('q', '').strip()

    # Get pending and ongoing tasks (most important)
    pending_tasks_result = supabase.table('tasks')\
        .select('*')\
        .eq('user_id', user_id)\
        .in_('status', ['pending', 'ongoing'])\
        .order('due_date')\
        .order('due_time')\
        .execute()

    # Get completed tasks — search all when filtering, otherwise last 50
    pending_list = pending_tasks_result.data or []

    if search_query:
        search_lower = search_query.lower()
        # Escape chars that break PostgREST filter parsing
        safe_q = search_query.replace(',', ' ').replace('(', '').replace(')', '')

        # Search completed AND cancelled tasks at DB level
        completed_query = supabase.table('tasks')\
            .select('*')\
            .eq('user_id', user_id)\
            .in_('status', ['completed', 'cancelled'])\
            .or_(f'title.ilike.%{safe_q}%,client_name.ilike.%{safe_q}%,description.ilike.%{safe_q}%')\
            .order('completed_at', desc=True)\
            .limit(100)\
            .execute()
        completed_list = completed_query.data or []

        # Filter pending in Python (already fully loaded)
        pending_list = [t for t in pending_list if
            search_lower in (t.get('title') or '').lower() or
            search_lower in (t.get('description') or '').lower() or
            search_lower in (t.get('client_name') or '').lower()
        ]
        all_tasks = pending_list + completed_list
    else:
        completed_tasks_result = supabase.table('tasks')\
            .select('*')\
            .eq('user_id', user_id)\
            .eq('status', 'completed')\
            .order('completed_at', desc=True)\
            .limit(50)\
            .execute()
        completed_list = completed_tasks_result.data or []
        all_tasks = pending_list + completed_list

    # Calculate stats (from unfiltered data)
    all_pending = pending_tasks_result.data or []
    stats = {
        'pending': len(all_pending),
        'due_today': len([t for t in all_pending if t.get('due_date') == today]),
        'overdue': len([t for t in all_pending if t.get('due_date') and t['due_date'] < today]),
        'completed_this_week': len([t for t in completed_list if t.get('completed_at')])
    }

    # System health for admin banner
    system_health = None
    if session.get('user_role') == 'global_admin':
        try:
            from monitoring import get_system_health
            system_health = get_system_health()
        except Exception:
            pass

    return render_template(
        'dashboard.html',
        title='Dashboard',
        tasks=all_tasks,
        stats=stats,
        today=today,
        search_query=search_query,
        system_health=system_health
    )


@app.route('/daily-report')
@login_required
def daily_report():
    """Daily report view with clickable tasks grouped by urgency"""
    user_id = session['user_id']
    tz = get_user_timezone()
    now = datetime.now(tz)
    today = now.date().isoformat()

    # Format date display
    date_display = now.strftime('%A, %B %d, %Y')

    # Get all pending tasks
    tasks_result = supabase.table('tasks')\
        .select('*')\
        .eq('user_id', user_id)\
        .eq('status', 'pending')\
        .order('due_date')\
        .order('due_time')\
        .execute()

    all_tasks = tasks_result.data or []

    # Categorize tasks
    overdue_tasks = []
    today_tasks = []
    upcoming_tasks = []

    for task in all_tasks:
        due_date = task.get('due_date')
        if not due_date:
            upcoming_tasks.append(task)
        elif due_date < today:
            overdue_tasks.append(task)
        elif due_date == today:
            today_tasks.append(task)
        else:
            upcoming_tasks.append(task)

    # Limit upcoming to 15
    upcoming_tasks = upcoming_tasks[:15]

    stats = {
        'total_pending': len(all_tasks),
        'overdue': len(overdue_tasks),
        'due_today': len(today_tasks),
        'upcoming': len(upcoming_tasks)
    }

    return render_template(
        'daily_report.html',
        title='Daily Report',
        date_display=date_display,
        overdue_tasks=overdue_tasks,
        today_tasks=today_tasks,
        upcoming_tasks=upcoming_tasks,
        stats=stats,
        today=today
    )


@app.route('/tasks/create', methods=['POST'])
@login_required
def create_task():
    user_id = session['user_id']

    # Check subscription limits
    sub = get_user_subscription(user_id)
    if not sub['can_create_task']:
        if sub.get('trial_expired'):
            return redirect(url_for('pricing_page', reason='trial_expired'))
        return redirect(url_for('pricing_page', reason='limit_reached'))

    task_data = {
        'user_id': user_id,
        'title': request.form.get('title'),
        'description': request.form.get('description'),
        'due_date': request.form.get('due_date'),
        'due_time': request.form.get('due_time', '09:00') + ':00',
        'priority': request.form.get('priority', 'medium'),
        'status': 'pending',
        'client_name': request.form.get('client_name') or None
    }

    result = supabase.table('tasks').insert(task_data).execute()
    increment_task_count(user_id)

    # Send confirmation email
    if result.data:
        task = result.data[0]
        user_email = session.get('user_email')
        user_name = session.get('user_name')
        if user_email:
            send_task_confirmation_email(
                user_email=user_email,
                task_title=task.get('title'),
                due_date=task.get('due_date'),
                due_time=task.get('due_time'),
                task_id=task.get('id'),
                user_name=user_name
            )
        else:
            print(f"⚠️ No user_email in session — skipping task confirmation email")
    else:
        print(f"⚠️ No result.data from task insert — skipping confirmation email")

    return redirect(url_for('dashboard'))


@app.route('/tasks/<task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
    user_id = session['user_id']

    try:
        # Verify ownership
        task = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user_id).maybe_single().execute()
        if not task.data:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            update_data = {
                'title': request.form.get('title'),
                'description': request.form.get('description'),
                'due_date': request.form.get('due_date'),
                'due_time': request.form.get('due_time', '09:00') + ':00',
                'priority': request.form.get('priority'),
                'status': request.form.get('status'),
                'client_name': request.form.get('client_name') or None,
                'client_email': request.form.get('client_email') or None,
                'client_phone': request.form.get('client_phone') or None,
                'project_name': request.form.get('project_name') or None
            }

            if update_data['status'] == 'completed':
                update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
            elif update_data['status'] in ('pending', 'ongoing'):
                update_data['completed_at'] = None

            supabase.table('tasks').update(update_data).eq('id', task_id).execute()
            return redirect(url_for('dashboard'))

        # Get checklist items
        checklist = supabase.table('task_checklist_items')\
            .select('*')\
            .eq('task_id', task_id)\
            .order('display_order')\
            .execute()

        # Ensure task data has safe defaults for template rendering
        task_data = task.data
        if task_data.get('due_date') is None:
            task_data['due_date'] = ''
        if task_data.get('due_time') is None:
            task_data['due_time'] = ''

        return render_template(
            'task_edit.html',
            title='Edit Task',
            task=task_data,
            checklist=checklist.data or []
        )
    except Exception as e:
        import traceback
        print(f"❌ Error editing task {task_id}: {e}")
        traceback.print_exc()
        return f"Error loading task: {e}", 500


@app.route('/tasks/<task_id>')
@login_required
def task_detail(task_id):
    user_id = session['user_id']

    # Get task. maybe_single() returns None (not a result object) when no row
    # matches — check both `task` and `task.data` to avoid the classic
    # "NoneType has no attribute 'data'" crash for tasks owned by a
    # different user.
    task = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user_id).maybe_single().execute()
    if not task or not task.data:
        return redirect(url_for('dashboard'))

    # Get checklist items
    checklist = supabase.table('task_checklist_items')\
        .select('*')\
        .eq('task_id', task_id)\
        .order('display_order')\
        .execute()

    # Get notes
    notes = supabase.table('task_notes')\
        .select('*')\
        .eq('task_id', task_id)\
        .order('created_at', desc=True)\
        .limit(20)\
        .execute()

    return render_template(
        'task_detail.html',
        title=task.data['title'],
        task=task.data,
        checklist=checklist.data or [],
        notes=notes.data or []
    )


@app.route('/tasks/<task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    user_id = session['user_id']

    # Verify ownership and delete
    supabase.table('tasks').delete().eq('id', task_id).eq('user_id', user_id).execute()
    return redirect(url_for('dashboard'))


@app.route('/tasks/<task_id>/complete', methods=['POST'])
@login_required
def complete_task_action(task_id):
    user_id = session['user_id']

    supabase.table('tasks').update({
        'status': 'completed',
        'completed_at': datetime.now(pytz.UTC).isoformat()
    }).eq('id', task_id).eq('user_id', user_id).execute()

    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/tasks/<task_id>/reopen', methods=['POST'])
@login_required
def reopen_task(task_id):
    user_id = session['user_id']

    supabase.table('tasks').update({
        'status': 'pending',
        'completed_at': None
    }).eq('id', task_id).eq('user_id', user_id).execute()

    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/tasks/<task_id>/checklist', methods=['POST'])
@login_required
def update_checklist(task_id):
    user_id = session['user_id']
    completed_ids = request.form.getlist('completed')

    # Verify task ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    # Get all checklist items
    items = supabase.table('task_checklist_items').select('id').eq('task_id', task_id).execute()

    for item in items.data or []:
        is_completed = item['id'] in completed_ids
        update_data = {'is_completed': is_completed}
        if is_completed:
            update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
        else:
            update_data['completed_at'] = None

        supabase.table('task_checklist_items').update(update_data).eq('id', item['id']).execute()

    redirect_to = request.form.get('redirect_to', 'detail')
    if redirect_to == 'edit':
        return redirect(url_for('edit_task', task_id=task_id))
    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/tasks/<task_id>/checklist/add', methods=['POST'])
@login_required
def add_checklist_item(task_id):
    user_id = session['user_id']
    item_text = request.form.get('item_text', '').strip()

    if not item_text:
        return redirect(url_for('task_detail', task_id=task_id))

    # Verify ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    # Get max display order
    existing = supabase.table('task_checklist_items')\
        .select('display_order')\
        .eq('task_id', task_id)\
        .order('display_order', desc=True)\
        .limit(1)\
        .execute()

    max_order = existing.data[0]['display_order'] if existing.data else 0

    supabase.table('task_checklist_items').insert({
        'task_id': task_id,
        'item_text': item_text,
        'is_completed': False,
        'display_order': max_order + 1
    }).execute()

    redirect_to = request.form.get('redirect_to', 'detail')
    if redirect_to == 'edit':
        return redirect(url_for('edit_task', task_id=task_id))
    elif redirect_to == 'shopping':
        return redirect(url_for('shopping_list'))
    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/api/tasks/<task_id>/checklist/<item_id>/toggle', methods=['POST'])
@login_required
def api_toggle_checklist_item(task_id, item_id):
    user_id = session['user_id']

    # Verify task ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json()
    is_completed = data.get('is_completed', False)

    update_data = {'is_completed': is_completed}
    if is_completed:
        update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
    else:
        update_data['completed_at'] = None

    supabase.table('task_checklist_items').update(update_data).eq('id', item_id).execute()
    return jsonify({'success': True})


def _get_shopping_list_user_id():
    """Look up the user_id for the shopping list owner."""
    user = supabase.table('users').select('id')\
        .eq('email', SHOPPING_LIST_USER_EMAIL)\
        .limit(1).execute()
    return user.data[0]['id'] if user.data else None


def _verify_shopping_token(token):
    """Return user_id if token is valid, else None."""
    if not SHOPPING_LIST_TOKEN or token != SHOPPING_LIST_TOKEN:
        return None
    return _get_shopping_list_user_id()


def _render_shopping_list(user_id, token=None):
    """Shared logic for rendering the shopping list."""
    task = supabase.table('tasks').select('id')\
        .eq('user_id', user_id)\
        .eq('title', 'Shopping List')\
        .execute()

    if task.data:
        task_id = task.data[0]['id']
    else:
        result = supabase.table('tasks').insert({
            'user_id': user_id,
            'title': 'Shopping List',
            'status': 'pending',
            'priority': 'medium',
            'due_date': '2099-12-31',
        }).execute()
        task_id = result.data[0]['id']

    items = supabase.table('task_checklist_items')\
        .select('*')\
        .eq('task_id', task_id)\
        .order('is_completed')\
        .order('display_order')\
        .execute()

    uncompleted = [i for i in items.data if not i['is_completed']]
    completed = [i for i in items.data if i['is_completed']]

    return render_template('shopping_list.html',
                           token=token,
                           task_id=task_id,
                           uncompleted=uncompleted,
                           completed=completed,
                           total=len(items.data),
                           title='Shopping List')


@app.route('/shopping-list')
@login_required
def shopping_list():
    return _render_shopping_list(session['user_id'])


@app.route('/sl/<token>')
def shopping_list_public(token):
    user_id = _verify_shopping_token(token)
    if not user_id:
        return 'Not found', 404
    return _render_shopping_list(user_id, token=token)


@app.route('/sl/<token>/add', methods=['POST'])
def shopping_list_add(token):
    user_id = _verify_shopping_token(token)
    if not user_id:
        return 'Not found', 404

    item_text = request.form.get('item_text', '').strip()
    if not item_text:
        return redirect(url_for('shopping_list_public', token=token))

    task = supabase.table('tasks').select('id')\
        .eq('user_id', user_id)\
        .eq('title', 'Shopping List')\
        .execute()
    if not task.data:
        return redirect(url_for('shopping_list_public', token=token))

    task_id = task.data[0]['id']

    existing = supabase.table('task_checklist_items')\
        .select('display_order')\
        .eq('task_id', task_id)\
        .order('display_order', desc=True)\
        .limit(1).execute()
    max_order = existing.data[0]['display_order'] if existing.data else 0

    supabase.table('task_checklist_items').insert({
        'task_id': task_id,
        'item_text': item_text,
        'is_completed': False,
        'display_order': max_order + 1
    }).execute()

    return redirect(url_for('shopping_list_public', token=token))


@app.route('/sl/<token>/toggle/<item_id>', methods=['POST'])
def shopping_list_toggle(token, item_id):
    user_id = _verify_shopping_token(token)
    if not user_id:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json()
    is_completed = data.get('is_completed', False)

    update_data = {'is_completed': is_completed}
    if is_completed:
        update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
    else:
        update_data['completed_at'] = None

    supabase.table('task_checklist_items').update(update_data).eq('id', item_id).execute()
    return jsonify({'success': True})


@app.route('/sl/<token>/clear', methods=['POST'])
def shopping_list_clear(token):
    user_id = _verify_shopping_token(token)
    if not user_id:
        return 'Not found', 404

    task = supabase.table('tasks').select('id')\
        .eq('user_id', user_id)\
        .eq('title', 'Shopping List')\
        .execute()

    if not task.data:
        return redirect(url_for('shopping_list_public', token=token))

    supabase.table('task_checklist_items')\
        .delete()\
        .eq('task_id', task.data[0]['id'])\
        .eq('is_completed', True)\
        .execute()

    return redirect(url_for('shopping_list_public', token=token))


@app.route('/api/shopping-list/clear', methods=['POST'])
@login_required
def clear_completed_shopping():
    user_id = session['user_id']
    task = supabase.table('tasks').select('id')\
        .eq('user_id', user_id)\
        .eq('title', 'Shopping List')\
        .execute()
    if task.data:
        supabase.table('task_checklist_items')\
            .delete()\
            .eq('task_id', task.data[0]['id'])\
            .eq('is_completed', True)\
            .execute()
    return redirect(url_for('shopping_list'))


@app.route('/tasks/<task_id>/notes/add', methods=['POST'])
@login_required
def add_note(task_id):
    user_id = session['user_id']
    content = request.form.get('content', '').strip()

    if not content:
        return redirect(url_for('task_detail', task_id=task_id))

    # Verify ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    supabase.table('task_notes').insert({
        'task_id': task_id,
        'content': content,
        'source': 'manual',
        'created_by': 'user'
    }).execute()

    return redirect(url_for('task_detail', task_id=task_id))


@app.route('/tasks/delay', methods=['POST'])
@login_required
def delay_task_custom():
    user_id = session['user_id']
    task_id = request.form.get('task_id')
    new_date = request.form.get('new_date')
    new_time = request.form.get('new_time')

    # Verify ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return redirect(url_for('dashboard'))

    supabase.table('tasks').update({
        'due_date': new_date,
        'due_time': new_time + ':00',
        'status': 'pending'
    }).eq('id', task_id).execute()

    return redirect(url_for('dashboard'))


@app.route('/settings')
@login_required
def settings():
    user_id = session['user_id']
    user = supabase.table('users').select('*').eq('id', user_id).single().execute()

    # Get subscription info
    sub = get_user_subscription(user_id)

    # Get referral stats
    referrals = supabase.table('referrals').select('*').eq('referrer_id', user_id).execute()
    referral_count = len(referrals.data) if referrals.data else 0
    converted_count = len([r for r in (referrals.data or []) if r.get('status') == 'converted'])

    # Get active CRM connection (if any)
    crm_connection = None
    try:
        from crm_manager import CRMManager
        _crm = CRMManager()
        crm_connections = _crm.get_user_connections(user_id)
        crm_connection = next((c for c in crm_connections if c.get('is_active')), None)
        if not crm_connection and crm_connections:
            crm_connection = crm_connections[0]  # Show first connection even if inactive
    except Exception:
        pass

    # Get sent invites
    sent_invites = []
    try:
        invites_result = supabase.table('referral_invites') \
            .select('invited_email, status, sent_at') \
            .eq('referrer_id', user_id) \
            .order('sent_at', desc=True) \
            .limit(20) \
            .execute()
        sent_invites = invites_result.data or []
    except Exception:
        pass

    return render_template(
        'settings.html',
        title='Settings',
        user=user.data,
        subscription=sub,
        referral_count=referral_count,
        converted_count=converted_count,
        referral_link=f"https://www.jottask.app/r/{sub.get('referral_code', '')}",
        crm_connection=crm_connection,
        sent_invites=sent_invites,
        message=request.args.get('message')
    )


@app.route('/settings/profile', methods=['POST'])
@login_required
def update_profile():
    user_id = session['user_id']

    # Parse alternate emails from individual inputs (or legacy comma-separated)
    email_list = request.form.getlist('alternate_emails_list')
    if email_list:
        alternate_emails = [e.strip().lower() for e in email_list if e.strip()]
    else:
        alternate_emails_str = request.form.get('alternate_emails', '')
        alternate_emails = [e.strip().lower() for e in alternate_emails_str.split(',') if e.strip()]

    update_data = {
        'full_name': request.form.get('full_name'),
        'company_name': request.form.get('company_name'),
        'timezone': request.form.get('timezone'),
        'alternate_emails': alternate_emails if alternate_emails else None
    }

    supabase.table('users').update(update_data).eq('id', user_id).execute()

    # Update session
    session['user_name'] = update_data['full_name']
    session['timezone'] = update_data['timezone']

    return redirect(url_for('settings', message='Profile updated successfully'))


@app.route('/settings/summary', methods=['POST'])
@login_required
def update_summary_settings():
    user_id = session['user_id']

    update_data = {
        'daily_summary_enabled': 'daily_summary_enabled' in request.form,
        'daily_summary_time': request.form.get('daily_summary_time', '08:00:00')
    }

    supabase.table('users').update(update_data).eq('id', user_id).execute()

    return redirect(url_for('settings', message='Summary settings updated successfully'))


@app.route('/settings/invite', methods=['POST'])
@login_required
def send_referral_invite():
    user_id = session['user_id']
    invited_email = request.form.get('invited_email', '').strip().lower()

    if not invited_email or '@' not in invited_email:
        return redirect(url_for('settings', message='Please enter a valid email'))

    # Get referrer info
    user = supabase.table('users').select('full_name, referral_code').eq('id', user_id).single().execute()
    if not user.data or not user.data.get('referral_code'):
        return redirect(url_for('settings', message='Referral code not found'))

    referrer_name = user.data.get('full_name', 'A Jottask user')
    referral_code = user.data['referral_code']
    referral_link = f"https://www.jottask.app/r/{referral_code}"

    # Check if already invited
    existing = supabase.table('referral_invites') \
        .select('id').eq('referrer_id', user_id).eq('invited_email', invited_email).execute()
    if existing.data:
        return redirect(url_for('settings', message=f'You already invited {invited_email}'))

    # Save the invite
    try:
        supabase.table('referral_invites').insert({
            'referrer_id': user_id,
            'invited_email': invited_email,
            'referral_code': referral_code,
            'status': 'sent',
        }).execute()
    except Exception as e:
        print(f"Error saving invite: {e}")
        return redirect(url_for('settings', message='Failed to save invite'))

    # Send the invite email
    html_content = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 560px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #6366F1, #8B5CF6); padding: 32px; border-radius: 12px 12px 0 0; text-align: center;">
            <h1 style="color: white; margin: 0; font-size: 24px;">You're Invited to Jottask</h1>
        </div>
        <div style="background: white; padding: 32px; border: 1px solid #E5E7EB; border-top: none; border-radius: 0 0 12px 12px;">
            <p style="font-size: 16px; color: #374151; margin-bottom: 16px;">
                <strong>{referrer_name}</strong> thinks you'd love Jottask — an AI-powered task management platform built for solar sales teams.
            </p>
            <p style="font-size: 15px; color: #6B7280; margin-bottom: 24px;">
                Forward your emails to Jottask and it does the rest — creates tasks, tracks follow-ups, sends reminders, and syncs your CRM.
            </p>
            <div style="text-align: center; margin-bottom: 24px;">
                <a href="{referral_link}" style="display: inline-block; background: #6366F1; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 16px;">
                    Start Free Trial
                </a>
            </div>
            <p style="font-size: 13px; color: #9CA3AF; text-align: center;">
                You'll both get <strong>$5 credit</strong> when you subscribe.
            </p>
        </div>
    </div>
    """

    success, error = send_email(invited_email, f'{referrer_name} invited you to Jottask', html_content)
    if success:
        return redirect(url_for('settings', message=f'Invite sent to {invited_email}!'))
    else:
        print(f"Failed to send referral invite: {error}")
        return redirect(url_for('settings', message=f'Invite saved but email failed to send'))


@app.route('/billing')
@login_required
def billing():
    # Placeholder for Stripe billing portal
    return redirect(url_for('settings'))


# ============================================
# ACTION ROUTE (Email Button Handler)
# ============================================


def _resend_dsw_email(task_id, task_data):
    """Resend DSW lead email with current lead_status for delayed tasks."""
    import importlib.util, sys, os
    try:
        spec = importlib.util.spec_from_file_location("dsw_lead_poller", 
            os.path.join(os.path.dirname(__file__), "dsw_lead_poller.py"))
        poller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(poller)
        from dotenv import load_dotenv
        load_dotenv()
        import requests as req
        TOKEN = os.getenv("PIPEREPLY_TOKEN")
        LOCATION_ID = os.getenv("PIPEREPLY_LOCATION_ID")
        H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Version": "2021-07-28"}
        # Extract name from task title e.g. "Call John Smith - New DSW Lead"
        title = task_data.get('title', '')
        name = title.replace('Call ', '').replace(' - New DSW Lead', '').strip()
        r = req.get("https://services.leadconnectorhq.com/contacts/", headers=H,
            params={"locationId": LOCATION_ID, "query": name, "limit": 1})
        contacts = r.json().get("contacts", [])
        if contacts:
            contact = contacts[0]
            lead_status = task_data.get('lead_status', 'new_lead')
            poller.process(contact, task_id=task_id, lead_status=lead_status)
            print(f"[DSW RESEND] Sent email for {name} status={lead_status}")
        else:
            print(f"[DSW RESEND] No contact found for: {name}")
    except Exception as e:
        print(f"[DSW RESEND] Error: {e}")

@app.route('/action')
def handle_action():
    """Handle action button clicks from emails - redirects to appropriate pages"""
    action = request.args.get('action')
    project_id = request.args.get('project_id')
    task_id = request.args.get('task_id')

    # Audit log: track who accesses action routes
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    user_agent = request.headers.get('User-Agent', 'unknown')[:100]
    print(f"🎯 ACTION ROUTE HIT: action={action}, task_id={task_id}, project_id={project_id} | IP={client_ip} | UA={user_agent}")

    # Project actions
    if action == 'view_project' and project_id:
        try:
            # Check if project exists in saas_projects
            project = supabase.table('saas_projects').select('id').eq('id', project_id).execute()
            print(f"Project query result: {project.data}")
            if project.data and len(project.data) > 0:
                return redirect(url_for('project_detail', project_id=project_id))
        except Exception as e:
            print(f"Project query error: {e}")

        # Project not found - show error page
        projects_url = url_for('projects')
        return f'''
        <!DOCTYPE html>
        <html>
        <head><title>Project Not Found - Jottask</title>
        <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
        </head>
        <body style="font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f9fafb;">
            <div style="text-align: center; padding: 40px; background: white; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                <h2 style="color: #6366F1;">Project Not Found</h2>
                <p style="color: #6b7280;">This project may have been deleted or moved.</p>
                <a href="{projects_url}" style="display: inline-block; margin-top: 20px; padding: 10px 20px; background: #6366F1; color: white; text-decoration: none; border-radius: 8px;">Go to Projects</a>
            </div>
        </body>
        </html>
        '''

    # Task actions - handle without login for email convenience
    if task_id and action:
        try:
            print(f"  📋 Querying task {task_id}...")
            # Get task details
            task = supabase.table('tasks').select('*, users!tasks_user_id_fkey(id, email, full_name)').eq('id', task_id).single().execute()
            print(f"  ✅ Task query result: {task.data is not None}")
            if not task.data:
                return render_template_string("""
                <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h2>Task Not Found</h2>
                    <p>This task may have been completed or deleted.</p>
                    <a href="https://www.jottask.app/dashboard">Go to Dashboard</a>
                </body></html>
                """)

            task_data = task.data
            task_title = task_data.get('title', 'Task')
            user_id = task_data.get('user_id')

            if action == 'complete':
                supabase.table('tasks').update({
                    'status': 'completed',
                    'completed_at': datetime.now(pytz.UTC).isoformat()
                }).eq('id', task_id).execute()

                return render_template_string("""
                <html><head><title>Task Completed</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #f0fdf4;">
                    <h2 style="color: #10B981;">✅ Task Completed!</h2>
                    <p><strong>{{ title }}</strong></p>
                    <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
                </body></html>
                """, title=task_title)

            elif action == 'cancel':
                supabase.table('tasks').update({
                    'status': 'cancelled',
                    'completed_at': datetime.now(pytz.UTC).isoformat()
                }).eq('id', task_id).execute()

                return render_template_string("""
                <html><head><title>Lead Dismissed</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #f9fafb;">
                    <h2 style="color: #374151;">Lead Dismissed</h2>
                    <p><strong>{{ title }}</strong></p>
                    <p style="color: #6b7280;">Marked as not yours — no further reminders.</p>
                    <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
                </body></html>
                """, title=task_title)

            elif action == 'delay_1hour':
                aest = pytz.timezone('Australia/Brisbane')
                new_dt = datetime.now(aest) + timedelta(hours=1)
                new_time = new_dt.strftime('%H:%M:00')
                new_date = new_dt.date().isoformat()

                supabase.table('tasks').update({
                    'due_date': new_date,
                    'due_time': new_time,
                    'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
                }).eq('id', task_id).execute()

                # If DSW Solar task, resend lead email with current status
                if task_data.get('category') == 'DSW Solar':
                    try:
                        _resend_dsw_email(task_id, task_data)
                    except Exception as e:
                        print(f"DSW resend error: {e}")
                return render_template_string("""
                <html><head><title>Task Delayed</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #eff6ff;">
                    <h2 style="color: #6366F1;">⏰ Task Delayed +1 Hour</h2>
                    <p><strong>{{ title }}</strong></p>
                    <p>New time: {{ new_time }}</p>
                    <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
                </body></html>
                """, title=task_title, new_time=new_time[:5])

            elif action == 'delay_1day':
                current_date = task_data.get('due_date')
                try:
                    due_date = datetime.fromisoformat(current_date)
                    new_date = (due_date + timedelta(days=1)).date().isoformat()
                except:
                    new_date = (datetime.now(pytz.timezone('Australia/Brisbane')) + timedelta(days=1)).date().isoformat()

                supabase.table('tasks').update({
                    'due_date': new_date,
                    'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
                }).eq('id', task_id).execute()

                if task_data.get('category') == 'DSW Solar':
                    try:
                        _resend_dsw_email(task_id, task_data)
                    except Exception as e:
                        print(f"DSW resend error: {e}")
                return render_template_string("""
                <html><head><title>Task Delayed</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #eff6ff;">
                    <h2 style="color: #6366F1;">📅 Task Delayed +1 Day</h2>
                    <p><strong>{{ title }}</strong></p>
                    <p>New date: {{ new_date }}</p>
                    <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
                </body></html>
                """, title=task_title, new_date=new_date)

            elif action in ('delay_next_day_8am', 'delay_next_day_9am', 'delay_next_monday_9am'):
                aest = pytz.timezone('Australia/Brisbane')
                now_aest = datetime.now(aest)
                if action == 'delay_next_day_8am':
                    target = (now_aest + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
                    label = 'Tomorrow 8:00 AM'
                elif action == 'delay_next_day_9am':
                    target = (now_aest + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
                    label = 'Tomorrow 9:00 AM'
                else:
                    days_until_monday = (7 - now_aest.weekday()) % 7 or 7
                    target = (now_aest + timedelta(days=days_until_monday)).replace(hour=9, minute=0, second=0, microsecond=0)
                    label = 'Monday 9:00 AM'

                supabase.table('tasks').update({
                    'due_date': target.date().isoformat(),
                    'due_time': target.strftime('%H:%M:%S'),
                    'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
                }).eq('id', task_id).execute()

                return render_template_string("""
                <html><head><title>Task Rescheduled</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #eff6ff;">
                    <h2 style="color: #0EA5E9;">📅 Task Rescheduled</h2>
                    <p><strong>{{ title }}</strong></p>
                    <p>Moved to: {{ label }}</p>
                    <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
                </body></html>
                """, title=task_title, label=label)

            elif action == 'delay_custom' or action == 'reschedule':
                # Show reschedule form with full edit capability
                current_date = task_data.get('due_date', datetime.now(pytz.timezone('Australia/Brisbane')).date().isoformat())
                current_time = task_data.get('due_time', '09:00:00')[:5]
                checklist = task_data.get('checklist', []) or []

                # Build checklist HTML
                checklist_html = ""
                if checklist:
                    checklist_html = '<label>Checklist:</label><div class="checklist">'
                    for i, item in enumerate(checklist):
                        item_text = item.get('text', '') if isinstance(item, dict) else str(item)
                        checklist_html += f'<input type="text" name="checklist_{i}" value="{item_text}" placeholder="Checklist item">'
                    checklist_html += '</div>'
                    checklist_html += '<input type="hidden" name="checklist_count" value="' + str(len(checklist)) + '">'

                return render_template_string("""
                <html><head><title>Edit Task</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px; max-width: 500px; margin: 0 auto; background: #f9fafb; }
                    .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                    h2 { color: #374151; margin-bottom: 20px; }
                    label { display: block; margin-bottom: 5px; font-weight: 500; color: #374151; }
                    input, textarea { width: 100%; padding: 12px; margin-bottom: 15px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 16px; box-sizing: border-box; }
                    textarea { resize: vertical; min-height: 60px; }
                    .checklist input { margin-bottom: 8px; }
                    .row { display: flex; gap: 12px; }
                    .row > div { flex: 1; }
                    button { width: 100%; padding: 14px; background: #6366F1; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; margin-top: 10px; }
                    button:hover { background: #4f46e5; }
                    .delete-btn { background: #EF4444; margin-top: 20px; }
                    .delete-btn:hover { background: #DC2626; }
                </style>
                </head>
                <body>
                    <div class="card">
                        <h2>📝 Edit Task</h2>
                        <form method="POST" action="/action/reschedule_submit">
                            <input type="hidden" name="task_id" value="{{ task_id }}">

                            <label>Task Title:</label>
                            <input type="text" name="title" value="{{ title }}" required>

                            <div class="row">
                                <div>
                                    <label>Date:</label>
                                    <input type="date" name="new_date" value="{{ current_date }}" required>
                                </div>
                                <div>
                                    <label>Time:</label>
                                    <input type="time" name="new_time" value="{{ current_time }}" required>
                                </div>
                            </div>

                            {{ checklist_html|safe }}

                            <button type="submit">💾 Save Changes</button>
                        </form>
                        <form method="POST" action="/action/task_delete" onsubmit="return confirm('Delete this task?');">
                            <input type="hidden" name="task_id" value="{{ task_id }}">
                            <button type="submit" class="delete-btn">🗑️ Delete Task</button>
                        </form>
                    </div>
                </body></html>
                """, title=task_title, task_id=task_id, current_date=current_date, current_time=current_time, checklist_html=checklist_html)

        except Exception as e:
            print(f"Action error: {e}")
            return render_template_string("""
            <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h2>Error</h2><p>{{ error }}</p>
                <a href="https://www.jottask.app/dashboard">Go to Dashboard</a>
            </body></html>
            """, error=str(e))

    # Merge duplicate tasks
    if action == 'merge_tasks':
        keep_id = request.args.get('keep_id')
        delete_ids = request.args.get('delete_ids', '')
        if not keep_id or not delete_ids:
            return render_template_string("""
            <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h2>Error</h2><p>Missing task IDs for merge.</p>
            </body></html>
            """)
        try:
            # Delete the duplicate tasks
            for did in delete_ids.split(','):
                did = did.strip()
                if did:
                    supabase.table('tasks').delete().eq('id', did).execute()

            # Update kept task: set due to 2 hours from now
            now = datetime.now(pytz.UTC)
            new_due = now + timedelta(hours=2)
            supabase.table('tasks').update({
                'due_date': new_due.date().isoformat(),
                'due_time': new_due.strftime('%H:%M') + ':00',
                'reminder_sent_at': now.isoformat()
            }).eq('id', keep_id).execute()

            # Get the kept task title for display
            kept = supabase.table('tasks').select('title').eq('id', keep_id).single().execute()
            kept_title = kept.data.get('title', 'Task') if kept.data else 'Task'
            deleted_count = len([d for d in delete_ids.split(',') if d.strip()])

            return render_template_string("""
            <html><head><title>Tasks Merged</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            </head>
            <body style="font-family: -apple-system, sans-serif; text-align: center; padding: 50px; background: #f0fdf4;">
                <h2 style="color: #10B981;">Tasks Merged</h2>
                <p><strong>{{ title }}</strong></p>
                <p>{{ deleted_count }} duplicate(s) removed. Remaining task due in 2 hours.</p>
                <a href="https://www.jottask.app/dashboard" style="display: inline-block; margin-top: 20px; padding: 12px 24px; background: #6366F1; color: white; text-decoration: none; border-radius: 8px;">Open Dashboard</a>
            </body></html>
            """, title=kept_title, deleted_count=deleted_count)

        except Exception as e:
            print(f"Merge tasks error: {e}")
            return render_template_string("""
            <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h2>Error</h2><p>{{ error }}</p>
            </body></html>
            """, error=str(e))

    # Keep both duplicates (dismiss notification)
    if action == 'keep_duplicates':
        task_ids_param = request.args.get('task_ids', '')
        if not task_ids_param:
            return render_template_string("""
            <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h2>Error</h2><p>Missing task IDs.</p>
            </body></html>
            """)
        try:
            sorted_ids = ','.join(sorted(task_ids_param.split(',')))
            # Get user_id from the first task
            first_id = task_ids_param.split(',')[0].strip()
            task_result = supabase.table('tasks').select('user_id').eq('id', first_id).single().execute()
            user_id = task_result.data['user_id'] if task_result.data else None

            supabase.table('duplicate_dismissed').insert({
                'task_ids': sorted_ids,
                'user_id': user_id
            }).execute()

            return render_template_string("""
            <html><head><title>Duplicates Kept</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            </head>
            <body style="font-family: -apple-system, sans-serif; text-align: center; padding: 50px; background: #eff6ff;">
                <h2 style="color: #6366F1;">Both Tasks Kept</h2>
                <p>You won't be notified about these duplicates again.</p>
                <a href="https://www.jottask.app/dashboard" style="display: inline-block; margin-top: 20px; padding: 12px 24px; background: #6366F1; color: white; text-decoration: none; border-radius: 8px;">Open Dashboard</a>
            </body></html>
            """)

        except Exception as e:
            print(f"Keep duplicates error: {e}")
            return render_template_string("""
            <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h2>Error</h2><p>{{ error }}</p>
            </body></html>
            """, error=str(e))

    elif action == 'set_custom' and task_id:
        r_date = request.args.get('date', '')
        r_time = request.args.get('time', '09:00')
        if r_date:
            supabase.table('tasks').update({
                'due_date': r_date,
                'due_time': r_time + ':00',
                'reminder_sent_at': datetime.now(pytz.UTC).isoformat(),
            }).eq('id', task_id).execute()
        return redirect(url_for('lead_detail', task_id=task_id))

    elif action == 'no_reply' and task_id:
        try:
            task_row = supabase.table('tasks').select('due_time').eq('id', task_id).single().execute()
            current_time = (task_row.data or {}).get('due_time', '09:00:00') or '09:00:00'
        except Exception:
            current_time = '09:00:00'
        aest = pytz.timezone('Australia/Brisbane')
        tomorrow = (datetime.now(aest) + timedelta(days=1)).date().isoformat()
        supabase.table('tasks').update({
            'due_date': tomorrow,
            'due_time': current_time,
            'lead_status': 'no_reply',
            'reminder_sent_at': datetime.now(pytz.UTC).isoformat(),
        }).eq('id', task_id).execute()
        return redirect(url_for('lead_detail', task_id=task_id))

    elif action == 'set_status' and task_id:
            status_val = request.args.get('status', '')
            lost_reason = request.args.get('lost_reason', '')
            update = {'lead_status': status_val, 'reminder_sent_at': None}
            if status_val in ['won', 'lost']:
                update['status'] = 'completed'
                update['completed_at'] = datetime.now(pytz.UTC).isoformat()
            if status_val == 'lost' and lost_reason:
                update['lost_reason'] = lost_reason
            supabase.table('tasks').update(update).eq('id', task_id).execute()
            status_labels = {
                'intro_call':'Intro Call','site_visit_booked':'Site Visit Booked',
                'awaiting_docs':'Awaiting Docs','build_quote':'Build Quote',
                'quote_submitted':'Quote Sent','quote_followup':'Quote Follow Up',
                'revise_quote':'Revise Quote','customer_deciding':'Customer Deciding',
                'nurture':'Nurture','won':'WON 🎉','lost':'LOST ❌'
            }
            label = status_labels.get(status_val, status_val.replace('_',' ').title())
            color = '#10B981' if status_val == 'won' else '#ef4444' if status_val == 'lost' else '#1e40af'
            au = "https://www.jottask.app/action"
            delay_buttons = [
                ("+1 Hour",    f"{au}?action=delay_1hour&task_id={task_id}"),
                ("+1 Day",     f"{au}?action=delay_1day&task_id={task_id}"),
                ("Tmrw 8am",   f"{au}?action=delay_next_day_8am&task_id={task_id}"),
                ("Tmrw 9am",   f"{au}?action=delay_next_day_9am&task_id={task_id}"),
                ("Mon 9am",    f"{au}?action=delay_next_monday_9am&task_id={task_id}"),
            ]
            btn_style = "display:inline-block;padding:8px 14px;background:#1e40af;color:white;text-decoration:none;border-radius:8px;font-size:13px;font-weight:600"
            btns_html = " ".join(f'<a href="{url}" style="{btn_style}">{label_}</a>' for label_, url in delay_buttons)
            return render_template_string("""
            <html><head><title>Status Updated</title>
            <meta name="viewport" content="width=device-width,initial-scale=1">
            </head>
            <body style="font-family:sans-serif;text-align:center;padding:50px;background:#f8fafc;">
                <div style="font-size:48px;margin-bottom:16px">✅</div>
                <h2 style="color:{{ color }}">{{ label }}</h2>
                <p style="color:#6b7280">Lead status updated</p>
                <div style="margin:28px 0 8px">
                    <p style="color:#6b7280;font-size:13px;font-weight:600;margin-bottom:10px">Remind me again:</p>
                    <div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:center">{{ btns_html | safe }}</div>
                </div>
                <p style="margin-top:28px"><a href="https://www.jottask.app/dashboard" style="color:#6366F1">Open Dashboard</a></p>
            </body></html>
            """, label=label, color=color, btns_html=btns_html)

    # Default - show error (don't redirect to login-required dashboard)
    return render_template_string("""
    <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
        <h2>Invalid Action</h2>
        <p>This link may be expired or invalid.</p>
        <a href="https://www.jottask.app">Go to Jottask</a>
    </body></html>
    """)


@app.route('/action/reschedule_submit', methods=['POST'])
def handle_reschedule_submit():
    """Handle reschedule form submission from email links"""
    task_id = request.form.get('task_id')
    new_date = request.form.get('new_date')
    new_time = request.form.get('new_time')
    new_title = request.form.get('title')
    checklist_count = request.form.get('checklist_count')

    if not task_id or not new_date or not new_time:
        return render_template_string("""
        <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2>Error</h2><p>Missing required fields</p>
        </body></html>
        """)

    try:
        # Build update data
        update_data = {
            'due_date': new_date,
            'due_time': new_time + ':00',
            'reminder_sent_at': datetime.now(pytz.UTC).isoformat(),
            'status': 'pending'
        }

        # Update title if provided
        if new_title:
            update_data['title'] = new_title.strip()

        # Update checklist if provided
        if checklist_count:
            try:
                count = int(checklist_count)
                new_checklist = []
                for i in range(count):
                    item_text = request.form.get(f'checklist_{i}', '').strip()
                    if item_text:
                        new_checklist.append({'text': item_text, 'completed': False})
                if new_checklist:
                    update_data['checklist'] = new_checklist
            except:
                pass

        # Update task
        supabase.table('tasks').update(update_data).eq('id', task_id).execute()

        task_title = new_title or 'Task'

        return render_template_string("""
        <html><head><title>Task Updated</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        </head>
        <body style="font-family: -apple-system, sans-serif; text-align: center; padding: 50px; background: #f0fdf4;">
            <h2 style="color: #10B981;">✅ Task Updated!</h2>
            <p><strong>{{ title }}</strong></p>
            <p>Scheduled: {{ date }} at {{ time }}</p>
            <a href="https://www.jottask.app/dashboard" style="display: inline-block; margin-top: 20px; padding: 12px 24px; background: #6366F1; color: white; text-decoration: none; border-radius: 8px;">Open Dashboard</a>
        </body></html>
        """, title=task_title, date=new_date, time=new_time)

    except Exception as e:
        return render_template_string("""
        <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2>Error</h2><p>{{ error }}</p>
        </body></html>
        """, error=str(e))


@app.route('/action/task_delete', methods=['POST'])
def handle_task_delete():
    """Handle task deletion from email edit page"""
    task_id = request.form.get('task_id')

    if not task_id:
        return render_template_string("""
        <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2>Error</h2><p>Missing task ID</p>
        </body></html>
        """)

    try:
        supabase.table('tasks').delete().eq('id', task_id).execute()

        return render_template_string("""
        <html><head><title>Task Deleted</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        </head>
        <body style="font-family: -apple-system, sans-serif; text-align: center; padding: 50px; background: #fef2f2;">
            <h2 style="color: #EF4444;">🗑️ Task Deleted</h2>
            <p>The task has been removed.</p>
            <a href="https://www.jottask.app/dashboard" style="display: inline-block; margin-top: 20px; padding: 12px 24px; background: #6366F1; color: white; text-decoration: none; border-radius: 8px;">Open Dashboard</a>
        </body></html>
        """)

    except Exception as e:
        return render_template_string("""
        <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2>Error</h2><p>{{ error }}</p>
        </body></html>
        """, error=str(e))


# ============================================
# PROJECT ROUTES
# ============================================

@app.route('/projects')
@login_required
def projects():
    user_id = session['user_id']
    filter_status = request.args.get('filter', 'active')

    # Build query - fetch all projects for kanban board
    query = supabase.table('saas_projects').select('*').eq('user_id', user_id)

    result = query.order('created_at', desc=True).execute()
    projects_list = result.data or []

    # Get item counts and progress for each project
    for project in projects_list:
        items_result = supabase.table('saas_project_items')\
            .select('id, is_completed')\
            .eq('project_id', project['id'])\
            .execute()
        items = items_result.data or []
        project['item_count'] = len(items)
        completed = len([i for i in items if i['is_completed']])
        project['progress'] = int((completed / len(items) * 100)) if items else 0

    # Calculate stats
    all_projects = supabase.table('saas_projects').select('id, status').eq('user_id', user_id).execute().data or []
    all_items = []
    for p in all_projects:
        items_result = supabase.table('saas_project_items').select('is_completed').eq('project_id', p['id']).execute()
        all_items.extend(items_result.data or [])

    stats = {
        'active': len([p for p in all_projects if p['status'] == 'active']),
        'completed_projects': len([p for p in all_projects if p['status'] == 'completed']),
        'total_items': len(all_items),
        'completed_items': len([i for i in all_items if i['is_completed']])
    }

    return render_template(
        'projects.html',
        title='Projects',
        projects=projects_list,
        stats=stats,
        filter=filter_status
    )


@app.route('/projects/create', methods=['GET', 'POST'])
@login_required
def project_create():
    user_id = session['user_id']

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        color = request.form.get('color', '#6366F1')
        initial_items = request.form.get('initial_items', '').strip()

        if not name:
            return redirect(url_for('project_create'))

        # Create project
        project_result = supabase.table('saas_projects').insert({
            'user_id': user_id,
            'name': name,
            'description': description or None,
            'color': color,
            'status': 'active'
        }).execute()

        if project_result.data:
            project_id = project_result.data[0]['id']

            # Add initial items if provided
            if initial_items:
                lines = [line.strip() for line in initial_items.split('\n') if line.strip()]
                for idx, item_text in enumerate(lines):
                    supabase.table('saas_project_items').insert({
                        'project_id': project_id,
                        'item_text': item_text,
                        'display_order': idx,
                        'source': 'manual'
                    }).execute()

            return redirect(url_for('project_detail', project_id=project_id))

        return redirect(url_for('projects'))

    return render_template(
        'project_create.html',
        title='Create Project'
    )


@app.route('/projects/<project_id>')
@login_required
def project_detail(project_id):
    user_id = session['user_id']

    # Get project
    project = supabase.table('saas_projects')\
        .select('*')\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .single()\
        .execute()

    if not project.data:
        return redirect(url_for('projects'))

    # Get items
    items = supabase.table('saas_project_items')\
        .select('*')\
        .eq('project_id', project_id)\
        .order('display_order')\
        .execute()

    items_list = items.data or []
    total_count = len(items_list)
    completed_count = len([i for i in items_list if i['is_completed']])
    progress = int((completed_count / total_count * 100)) if total_count else 0

    return render_template(
        'project_detail.html',
        title=project.data['name'],
        project=project.data,
        items=items_list,
        total_count=total_count,
        completed_count=completed_count,
        progress=progress
    )


@app.route('/projects/<project_id>/items/add', methods=['POST'])
@login_required
def project_item_add(project_id):
    user_id = session['user_id']
    item_text = request.form.get('item_text', '').strip()

    if not item_text:
        return redirect(url_for('project_detail', project_id=project_id))

    # Verify ownership
    project = supabase.table('saas_projects')\
        .select('id')\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .execute()

    if not project.data:
        return redirect(url_for('projects'))

    # Get max display order
    existing = supabase.table('saas_project_items')\
        .select('display_order')\
        .eq('project_id', project_id)\
        .order('display_order', desc=True)\
        .limit(1)\
        .execute()

    max_order = existing.data[0]['display_order'] if existing.data else 0

    supabase.table('saas_project_items').insert({
        'project_id': project_id,
        'item_text': item_text,
        'display_order': max_order + 1,
        'source': 'manual'
    }).execute()

    return redirect(url_for('project_detail', project_id=project_id))


@app.route('/projects/<project_id>/items/<item_id>/toggle', methods=['POST'])
@login_required
def project_item_toggle(project_id, item_id):
    user_id = session['user_id']

    # Verify ownership
    project = supabase.table('saas_projects')\
        .select('id')\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .execute()

    if not project.data:
        return redirect(url_for('projects'))

    # Get current state
    item = supabase.table('saas_project_items')\
        .select('is_completed')\
        .eq('id', item_id)\
        .eq('project_id', project_id)\
        .single()\
        .execute()

    if item.data:
        new_state = not item.data['is_completed']
        update_data = {'is_completed': new_state}
        if new_state:
            update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
        else:
            update_data['completed_at'] = None

        supabase.table('saas_project_items')\
            .update(update_data)\
            .eq('id', item_id)\
            .execute()

    return redirect(url_for('project_detail', project_id=project_id))


@app.route('/projects/<project_id>/complete', methods=['POST'])
@login_required
def project_complete(project_id):
    user_id = session['user_id']

    supabase.table('saas_projects').update({
        'status': 'completed',
        'completed_at': datetime.now(pytz.UTC).isoformat()
    }).eq('id', project_id).eq('user_id', user_id).execute()

    return redirect(url_for('project_detail', project_id=project_id))


@app.route('/projects/<project_id>/reopen', methods=['POST'])
@login_required
def project_reopen(project_id):
    user_id = session['user_id']

    supabase.table('saas_projects').update({
        'status': 'active',
        'completed_at': None
    }).eq('id', project_id).eq('user_id', user_id).execute()

    return redirect(url_for('project_detail', project_id=project_id))


@app.route('/projects/<project_id>/delete', methods=['POST'])
@login_required
def project_delete(project_id):
    user_id = session['user_id']

    # Delete will cascade to items due to FK constraint
    supabase.table('saas_projects')\
        .delete()\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .execute()

    return redirect(url_for('projects'))


@app.route('/api/projects/<project_id>/items/<item_id>/toggle', methods=['POST'])
@login_required
def api_project_item_toggle(project_id, item_id):
    user_id = session['user_id']

    # Verify ownership
    project = supabase.table('saas_projects')\
        .select('id')\
        .eq('id', project_id)\
        .eq('user_id', user_id)\
        .execute()

    if not project.data:
        return jsonify({'error': 'Not found'}), 404

    # Get current state
    item = supabase.table('saas_project_items')\
        .select('is_completed')\
        .eq('id', item_id)\
        .eq('project_id', project_id)\
        .single()\
        .execute()

    if not item.data:
        return jsonify({'error': 'Item not found'}), 404

    new_state = not item.data['is_completed']
    update_data = {'is_completed': new_state}
    if new_state:
        update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
    else:
        update_data['completed_at'] = None

    supabase.table('saas_project_items')\
        .update(update_data)\
        .eq('id', item_id)\
        .execute()

    return jsonify({'success': True, 'is_completed': new_state})


# ============================================
# API ENDPOINTS
# ============================================

@app.route('/api/tasks/<task_id>/status', methods=['POST'])
@login_required
def api_update_task_status(task_id):
    user_id = session['user_id']
    data = request.get_json()
    new_status = data.get('status')

    # Verify ownership
    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return jsonify({'error': 'Not found'}), 404

    update_data = {'status': new_status}
    if new_status == 'completed':
        update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
    elif new_status in ('pending', 'ongoing'):
        update_data['completed_at'] = None

    supabase.table('tasks').update(update_data).eq('id', task_id).execute()
    return jsonify({'success': True})


@app.route('/api/tasks/reminder-debug', methods=['GET'])
def api_reminder_debug():
    """Diagnostic endpoint: show tasks that should get reminders.
    Auth: logged-in session or internal API key.
    """
    user_id = session.get('user_id')
    api_key = request.headers.get('X-Internal-Key') or request.args.get('key')
    internal_key = os.getenv('INTERNAL_API_KEY', '')

    if not user_id and not (api_key and internal_key and api_key == internal_key):
        return jsonify({'error': 'Unauthorized'}), 401

    aest = pytz.timezone('Australia/Brisbane')
    now_aest = datetime.now(aest)
    today_str = now_aest.strftime('%Y-%m-%d')
    tomorrow_str = (now_aest + timedelta(days=1)).strftime('%Y-%m-%d')
    fourteen_days_ago = (now_aest - timedelta(days=14)).strftime('%Y-%m-%d')

    query = supabase.table('tasks')\
        .select('id, title, due_date, due_time, priority, status, client_name, user_id, reminder_sent_at, created_at')\
        .eq('status', 'pending')\
        .gte('due_date', fourteen_days_ago)\
        .lte('due_date', tomorrow_str)\
        .order('due_date')\
        .order('due_time')\
        .limit(50)

    if user_id:
        query = query.eq('user_id', user_id)

    result = query.execute()
    tasks = result.data or []

    output = []
    for t in tasks:
        reminded = t.get('reminder_sent_at')
        output.append({
            'id': t['id'][:8],
            'title': t['title'][:60],
            'due_date': t.get('due_date'),
            'due_time': t.get('due_time'),
            'client': t.get('client_name', ''),
            'reminder_sent_at': reminded[:19] if reminded else None,
            'created_at': t.get('created_at', '')[:19],
            'user_id': t.get('user_id', '')[:8],
        })

    return jsonify({
        'now_aest': now_aest.strftime('%Y-%m-%d %H:%M:%S'),
        'query_range': f'{fourteen_days_ago} to {tomorrow_str}',
        'total_pending': len(tasks),
        'needs_reminder': len([t for t in tasks if not t.get('reminder_sent_at')]),
        'tasks': output,
    })


@app.route('/api/emails/reprocess', methods=['POST'])
def api_reprocess_emails():
    """Re-process recent emails that were marked as no_action.
    Auth: logged-in session or internal API key.
    Clears processed_emails entries with outcome='no_action' from last 24h,
    so the next email processor cycle will re-analyze them.
    """
    user_id = session.get('user_id')
    api_key = request.headers.get('X-Internal-Key') or request.args.get('key')
    internal_key = os.getenv('INTERNAL_API_KEY', '')

    if not user_id and not (api_key and internal_key and api_key == internal_key):
        return jsonify({'error': 'Unauthorized'}), 401

    hours = request.args.get('hours', '24')
    try:
        hours = int(hours)
    except ValueError:
        hours = 24

    cutoff = (datetime.now(pytz.UTC) - timedelta(hours=hours)).isoformat()

    # Find no_action emails in the time window
    try:
        query = supabase.table('processed_emails')\
            .select('id, subject, sender_email, outcome, processed_at')\
            .eq('outcome', 'no_action')\
            .gte('processed_at', cutoff)\
            .order('processed_at', desc=True)\
            .limit(50)

        result = query.execute()
        emails = result.data or []
    except Exception as e:
        return jsonify({'error': f'DB query failed: {str(e)}. Has migration 020 been run?'}), 500

    if not emails:
        return jsonify({'message': 'No no_action emails found in the last ' + str(hours) + ' hours', 'count': 0})

    dry_run = request.args.get('dry_run', 'true').lower() == 'true'

    if dry_run:
        return jsonify({
            'message': f'Found {len(emails)} no_action emails to reprocess (dry_run=true, POST with dry_run=false to execute)',
            'count': len(emails),
            'emails': [{'subject': e.get('subject', '')[:80], 'sender': e.get('sender_email', ''), 'processed_at': e.get('processed_at', '')[:19]} for e in emails],
        })

    # Delete the processed_emails entries so the processor picks them up again
    deleted = 0
    for e in emails:
        try:
            supabase.table('processed_emails').delete().eq('id', e['id']).execute()
            deleted += 1
        except Exception as err:
            print(f"Failed to delete processed_email {e['id']}: {err}")

    return jsonify({
        'message': f'Cleared {deleted} no_action emails — they will be re-processed on the next cycle',
        'count': deleted,
    })


@app.route('/api/tasks/cleanup-duplicates', methods=['POST'])
def api_cleanup_duplicates():
    """Find and cancel duplicate tasks for a user.
    Groups tasks by client_name, keeps the newest pending task in each group,
    and cancels the rest. Accepts logged-in session OR internal API key.
    """
    # Auth: logged-in user OR internal API key (header or query param)
    api_key = request.headers.get('X-Internal-Key') or request.args.get('key')
    internal_key = os.getenv('INTERNAL_API_KEY', '')
    data = request.get_json() or {}

    if api_key and internal_key and api_key == internal_key:
        user_id = data.get('user_id')  # Optional: if omitted, cleans all users
    elif 'user_id' in session:
        user_id = session['user_id']
    else:
        return jsonify({'error': 'Authentication required'}), 401
    client_name_filter = data.get('client_name')  # Optional: filter by client_name column
    title_filter = data.get('title')  # Optional: filter by title (ilike)
    dry_run = data.get('dry_run', False)

    try:
        # Get all pending tasks (filtered by user if specified)
        query = supabase.table('tasks')\
            .select('id, title, client_name, due_date, due_time, created_at, status, user_id')\
            .eq('status', 'pending')\
            .order('created_at', desc=True)\
            .limit(500)

        if user_id:
            query = query.eq('user_id', user_id)

        if client_name_filter:
            query = query.ilike('client_name', f'%{client_name_filter}%')

        if title_filter:
            query = query.ilike('title', f'%{title_filter}%')

        result = query.execute()
        tasks = result.data or []

        # Group by client_name (lowercased), falling back to title_filter as group key
        from collections import defaultdict
        groups = defaultdict(list)
        for task in tasks:
            key = (task.get('client_name') or '').strip().lower()
            if not key and title_filter:
                # No client_name — group all title-matched tasks together
                key = title_filter.strip().lower()
            if key:
                groups[key].append(task)

        cancelled_ids = []
        kept_tasks = []

        for client_key, client_tasks in groups.items():
            if len(client_tasks) <= 1:
                continue  # No duplicates

            # Keep the newest (first, since sorted by created_at desc), cancel the rest
            keep = client_tasks[0]
            dupes = client_tasks[1:]
            kept_tasks.append({'id': keep['id'], 'title': keep['title'], 'client_name': keep.get('client_name')})

            for dupe in dupes:
                cancelled_ids.append(dupe['id'])

        if not dry_run and cancelled_ids:
            # Cancel in batches of 20
            for i in range(0, len(cancelled_ids), 20):
                batch = cancelled_ids[i:i+20]
                for task_id in batch:
                    q = supabase.table('tasks').update({
                        'status': 'cancelled'
                    }).eq('id', task_id)
                    if user_id:
                        q = q.eq('user_id', user_id)
                    q.execute()

        return jsonify({
            'success': True,
            'dry_run': dry_run,
            'duplicates_found': len(cancelled_ids),
            'cancelled_ids': cancelled_ids if not dry_run else [],
            'would_cancel': cancelled_ids if dry_run else [],
            'kept_tasks': kept_tasks,
        })

    except Exception as e:
        print(f"Cleanup error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/tasks/<task_id>/delay', methods=['POST'])
@login_required
def api_delay_task(task_id):
    user_id = session['user_id']
    data = request.get_json()
    hours = data.get('hours', 0)
    days = data.get('days', 0)
    preset = data.get('preset')

    # Verify ownership
    task = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user_id).maybe_single().execute()
    if not task.data:
        return jsonify({'error': 'Not found'}), 404

    tz = get_user_timezone()
    now = datetime.now(tz)

    if preset == 'next_day_8am':
        new_dt = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    elif preset == 'next_day_9am':
        new_dt = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif preset == 'next_monday_9am':
        days_until_monday = (7 - now.weekday()) % 7 or 7
        new_dt = (now + timedelta(days=days_until_monday)).replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        new_dt = now + timedelta(hours=hours, days=days)

    supabase.table('tasks').update({
        'due_date': new_dt.date().isoformat(),
        'due_time': new_dt.strftime('%H:%M:%S'),
        'status': 'pending',
        'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
    }).eq('id', task_id).execute()

    return jsonify({'success': True, 'new_due': new_dt.isoformat()})


@app.route('/api/tasks/<task_id>/title', methods=['POST'])
@login_required
def api_update_task_title(task_id):
    user_id = session['user_id']
    data = request.get_json()
    new_title = data.get('title', '').strip()

    if not new_title:
        return jsonify({'error': 'Title required'}), 400

    task = supabase.table('tasks').select('id').eq('id', task_id).eq('user_id', user_id).execute()
    if not task.data:
        return jsonify({'error': 'Not found'}), 404

    supabase.table('tasks').update({'title': new_title}).eq('id', task_id).execute()
    return jsonify({'success': True})


@app.route('/api/projects/<project_id>/status', methods=['POST'])
@login_required
def api_update_project_status(project_id):
    user_id = session['user_id']
    data = request.get_json()
    new_status = data.get('status')

    if new_status not in ('active', 'completed', 'archived'):
        return jsonify({'error': 'Invalid status'}), 400

    # Verify ownership
    project = supabase.table('saas_projects').select('id').eq('id', project_id).eq('user_id', user_id).execute()
    if not project.data:
        return jsonify({'error': 'Not found'}), 404

    update_data = {'status': new_status}
    if new_status == 'completed':
        update_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
    elif new_status == 'active':
        update_data['completed_at'] = None

    supabase.table('saas_projects').update(update_data).eq('id', project_id).execute()
    return jsonify({'success': True})


# ============================================
# SUPPORT CHAT
# ============================================

CHAT_WIDGET_TEMPLATE = """
<div id="chat-widget" class="chat-widget">
    <button id="chat-toggle" class="chat-toggle" onclick="toggleChat()">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        </svg>
        <span class="chat-badge" id="chat-badge" style="display: none;">1</span>
    </button>
    <div id="chat-panel" class="chat-panel" style="display: none;">
        <div class="chat-header">
            <span>Jottask Support</span>
            <button onclick="toggleChat()" style="background: none; border: none; color: white; cursor: pointer;">&times;</button>
        </div>
        <div id="chat-messages" class="chat-messages">
            <div class="chat-message bot">
                <p>Hi! I'm here to help. Ask me anything about Jottask, or type <strong>"speak to human"</strong> to escalate to our team.</p>
            </div>
        </div>
        <form id="chat-form" class="chat-input-form" onsubmit="sendMessage(event)">
            <input type="text" id="chat-input" placeholder="Type a message..." autocomplete="off">
            <button type="submit">Send</button>
        </form>
    </div>
</div>

<style>
.chat-widget { position: fixed; bottom: 20px; right: 20px; z-index: 1000; }
.chat-toggle { width: 56px; height: 56px; border-radius: 50%; background: var(--primary, #6366F1); border: none; color: white; cursor: pointer; box-shadow: 0 4px 12px rgba(0,0,0,0.15); display: flex; align-items: center; justify-content: center; position: relative; }
.chat-toggle:hover { transform: scale(1.05); }
.chat-badge { position: absolute; top: -4px; right: -4px; background: #EF4444; color: white; font-size: 12px; padding: 2px 6px; border-radius: 10px; }
.chat-panel { position: absolute; bottom: 70px; right: 0; width: 350px; height: 450px; background: white; border-radius: 12px; box-shadow: 0 8px 32px rgba(0,0,0,0.15); display: flex; flex-direction: column; overflow: hidden; }
.chat-header { background: var(--primary, #6366F1); color: white; padding: 16px; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
.chat-messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.chat-message { max-width: 85%; padding: 10px 14px; border-radius: 12px; font-size: 14px; line-height: 1.4; }
.chat-message.user { background: var(--primary, #6366F1); color: white; align-self: flex-end; border-bottom-right-radius: 4px; }
.chat-message.bot, .chat-message.admin { background: #F3F4F6; color: #374151; align-self: flex-start; border-bottom-left-radius: 4px; }
.chat-message.admin { background: #FEF3C7; border-left: 3px solid #F59E0B; }
.chat-input-form { display: flex; padding: 12px; border-top: 1px solid #E5E7EB; gap: 8px; }
.chat-input-form input { flex: 1; padding: 10px 14px; border: 1px solid #E5E7EB; border-radius: 20px; outline: none; }
.chat-input-form input:focus { border-color: var(--primary, #6366F1); }
.chat-input-form button { padding: 10px 16px; background: var(--primary, #6366F1); color: white; border: none; border-radius: 20px; cursor: pointer; }
@media (max-width: 480px) { .chat-panel { width: calc(100vw - 40px); right: -10px; } }
</style>

<script>
let conversationId = null;

function toggleChat() {
    const panel = document.getElementById('chat-panel');
    panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    if (panel.style.display === 'flex' && !conversationId) {
        startConversation();
    }
}

async function startConversation() {
    try {
        const res = await fetch('/api/chat/start', { method: 'POST' });
        const data = await res.json();
        conversationId = data.conversation_id;
    } catch (e) {
        console.error('Failed to start chat:', e);
    }
}

async function sendMessage(e) {
    e.preventDefault();
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message) return;

    // Add user message to UI
    addMessage(message, 'user');
    input.value = '';

    try {
        const res = await fetch('/api/chat/message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conversation_id: conversationId, message: message })
        });
        const data = await res.json();

        if (data.response) {
            addMessage(data.response, data.sender_type || 'bot');
        }
        if (data.escalated) {
            addMessage("Your message has been escalated to our team. We'll respond via email shortly.", 'bot');
        }
    } catch (e) {
        addMessage("Sorry, something went wrong. Please try again.", 'bot');
    }
}

function addMessage(text, type) {
    const container = document.getElementById('chat-messages');
    const msg = document.createElement('div');
    msg.className = 'chat-message ' + type;
    msg.innerHTML = '<p>' + text + '</p>';
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
}
</script>
"""

# Smart chat - topic definitions with responses
CHAT_TOPICS = {
    'task': {
        'actions': ['create', 'make', 'add', 'new', 'set', 'how', 'start', 'begin', 'setup', 'set up'],
        'response': 'To create a task:\n\n1. Click "+ New Task" on the dashboard\n2. Email your task to jottask@flowquote.ai\n3. CC jottask@flowquote.ai on any email to auto-create a follow-up\n\nTry clicking the blue "+ New Task" button!'
    },
    'project': {
        'actions': ['create', 'make', 'add', 'new', 'set', 'how', 'start', 'begin', 'setup', 'set up'],
        'response': 'To create a project:\n\n1. Go to Projects tab → click "+ New Project"\n2. Or email with subject: "Project: Name - Item 1, Item 2"\n\nExample: "Project: Website Redesign - Add contact form, Fix nav"\n\nEach item becomes a checklist item!'
    },
    'delay': {
        'actions': ['how', 'can i', 'to', 'a task', 'postpone', 'reschedule', 'snooze', 'push', 'move'],
        'response': 'To delay/postpone a task:\n\n1. Hover over the task → click "Delay"\n2. Choose quick option (+1 hour, +1 day, +1 week)\n3. Or set a custom date/time\n\nThe task will reappear when it\'s due!'
    },
    'complete': {
        'actions': ['how', 'mark', 'finish', 'done', 'check', 'tick'],
        'response': 'To complete a task:\n\nJust click the circle checkbox next to the task! ✓\n\nIt will be marked done and move to the Completed tab. You can reopen it later if needed.'
    },
    'delete': {
        'actions': ['how', 'remove', 'trash', 'get rid'],
        'response': 'To delete a task:\n\n1. Click "Edit" on the task\n2. Scroll down → click "Delete"\n\n⚠️ Deleted tasks cannot be recovered!'
    },
    'email': {
        'actions': ['what', 'which', 'send', 'use', 'how', 'address', 'integration'],
        'response': 'Email your tasks to:\njottask@flowquote.ai\n\n• Regular email → Creates a task\n• Subject "Project: Name - items" → Creates a project\n• CC jottask on any email → Creates follow-up reminder'
    }
}

# Direct keyword matches (no topic needed)
DIRECT_RESPONSES = {
    'help': 'Welcome to Jottask! I can help with:\n\n• Creating tasks and projects\n• Email integration (jottask@flowquote.ai)\n• Delaying, completing, or editing tasks\n• Settings and billing\n\nJust ask naturally - like "how do I make a task?" or "what\'s the email address?"',
    'pricing': 'Jottask Pricing:\n\n• 14-day free trial (no card needed)\n• Starter - Core task management\n• Pro - Advanced automation\n\nGo to Settings → Subscription to manage your plan.',
    'settings': 'Click "Settings" in the top navigation to:\n\n• Update your profile & timezone\n• Configure daily summary emails\n• Manage subscription & billing',
    'thanks': 'You\'re welcome! Let me know if you need anything else. 😊',
    'thank you': 'You\'re welcome! Happy to help. Let me know if you have more questions!',
}

GREETINGS = ['hi', 'hello', 'hey', 'hola', 'good morning', 'good afternoon', 'good evening', 'howdy']


def get_chat_response(message):
    """Smart matching - understands natural language questions"""
    msg = message.lower().strip()
    words = msg.split()

    # Check greetings first
    if any(g in msg for g in GREETINGS):
        return 'Hello! 👋 I\'m here to help you with Jottask.\n\nAsk me anything like:\n• "How do I create a task?"\n• "What\'s the email address?"\n• "How to delay a task?"\n\nOr type "speak to human" for live support.'

    # Check direct keyword matches
    for keyword, response in DIRECT_RESPONSES.items():
        if keyword in msg:
            return response

    # Smart topic + action matching
    for topic, data in CHAT_TOPICS.items():
        if topic in msg:
            # Topic found - check if any action word is present
            if any(action in msg for action in data['actions']):
                return data['response']
            # Topic alone (e.g., just "task?" or "projects")
            if len(words) <= 3:
                return data['response']

    # Check for question patterns without explicit topic
    if any(w in msg for w in ['how do i', 'how to', 'how can i', 'what is', 'where']):
        # Try to infer topic from remaining words
        if any(w in msg for w in ['task', 'todo', 'reminder', 'to-do']):
            return CHAT_TOPICS['task']['response']
        if any(w in msg for w in ['project', 'checklist', 'list']):
            return CHAT_TOPICS['project']['response']

    return None


# ============================================
# INTERNAL EMAIL API (for worker service)
# ============================================

INTERNAL_API_KEY = os.getenv('INTERNAL_API_KEY', 'jottask-internal-2026')


@app.route('/api/internal/generate-token', methods=['POST'])
def internal_generate_token():
    """Generate action token for email links"""
    api_key = request.headers.get('X-Internal-Key')
    if api_key != INTERNAL_API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    task_id = data.get('task_id')
    user_id = data.get('user_id')
    action = data.get('action', 'edit')

    if not task_id or not user_id:
        return jsonify({'error': 'Missing task_id or user_id'}), 400

    token = generate_action_token(task_id, user_id, action)
    return jsonify({'token': token})


@app.route('/api/internal/send-email', methods=['POST'])
def internal_send_email():
    """Internal API for worker to send emails through web service"""
    # Verify internal API key
    api_key = request.headers.get('X-Internal-Key')
    if api_key != INTERNAL_API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    to_email = data.get('to_email')
    subject = data.get('subject')
    body_html = data.get('body_html')

    if not all([to_email, subject, body_html]):
        return jsonify({'error': 'Missing required fields'}), 400

    result = send_email(to_email, subject, body_html)
    if isinstance(result, tuple):
        success, error = result
        return jsonify({'success': success, 'error': error})
    return jsonify({'success': result})


# ============================================
# EMAIL ACTION TOKENS (passwordless task actions)
# ============================================

import secrets

def generate_action_token(task_id, user_id, action, hours_valid=72):
    """Generate a secure token for email action links"""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(pytz.UTC) + timedelta(hours=hours_valid)

    supabase.table('email_action_tokens').insert({
        'task_id': task_id,
        'user_id': user_id,
        'token': token,
        'action': action,
        'expires_at': expires_at.isoformat()
    }).execute()

    return token


def validate_action_token(token):
    """Validate token and return task_id, user_id if valid"""
    result = supabase.table('email_action_tokens')\
        .select('*')\
        .eq('token', token)\
        .is_('used_at', 'null')\
        .execute()

    if not result.data:
        return None, None, None

    token_data = result.data[0]
    expires_at = datetime.fromisoformat(token_data['expires_at'].replace('Z', '+00:00'))

    if datetime.now(pytz.UTC) > expires_at:
        return None, None, None

    return token_data['task_id'], token_data['user_id'], token_data['action']


def _render_lead_detail(task_id):
    """DSW lead detail page — no login required.

    Wrapped by lead_detail() which catches exceptions and shows a friendly
    "Lead unavailable — Rebuild" page instead of a 500.
    """
    import re, urllib.parse

    aest = pytz.timezone('Australia/Brisbane')

    STATUS_LABELS = {
        'new_lead':          '🔵 NEW LEAD',
        'intro_call':        '📞 INTRO CALL',
        'site_visit_booked': '📅 SITE VISIT BOOKED',
        'awaiting_docs':     '📋 AWAITING DOCS',
        'build_quote':       '🔨 BUILD QUOTE',
        'quote_submitted':   '📤 QUOTE SENT',
        'quote_followup':    '🔔 QUOTE FOLLOW UP',
        'revise_quote':      '✏️ REVISE QUOTE',
        'customer_deciding': '🤔 DECIDING',
        'nurture':           '💧 NURTURE',
        'won':               '🎉 WON',
        'lost':              '❌ LOST',
        'no_reply':          '📵 NO REPLY',
    }
    STATUS_COLORS = {
        'new_lead': '#1e40af', 'intro_call': '#1e40af',
        'site_visit_booked': '#7c3aed', 'awaiting_docs': '#b45309',
        'build_quote': '#0369a1', 'quote_submitted': '#0891b2',
        'quote_followup': '#0e7490', 'revise_quote': '#7c3aed',
        'customer_deciding': '#b45309', 'nurture': '#6b7280',
        'won': '#10b981', 'lost': '#ef4444', 'no_reply': '#6b7280',
    }

    # ── Fetch task ────────────────────────────────────────────────────────
    try:
        res = supabase.table('tasks').select('*').eq('id', task_id).single().execute()
        t = res.data
    except Exception:
        t = None
    if not t:
        return '<html><body style="font-family:sans-serif;text-align:center;padding:50px"><h2>Lead not found</h2></body></html>', 404

    # ── Handle GET actions (delay / status) — redirect back to clean URL ──
    action = request.args.get('action', '')
    if action:
        update = {}
        _rem_now = datetime.now(pytz.UTC).isoformat()
        if action == 'delay_1hour':
            nd = datetime.now(aest) + timedelta(hours=1)
            update = {'due_date': nd.date().isoformat(), 'due_time': nd.strftime('%H:%M:00'), 'reminder_sent_at': _rem_now}
        elif action == 'delay_1day':
            try:
                base = datetime.fromisoformat(t.get('due_date', '')).replace(tzinfo=aest)
            except Exception:
                base = datetime.now(aest)
            nd = base + timedelta(days=1)
            update = {'due_date': nd.date().isoformat(), 'reminder_sent_at': _rem_now}
        elif action == 'delay_next_day_8am':
            tgt = (datetime.now(aest) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            update = {'due_date': tgt.date().isoformat(), 'due_time': '08:00:00', 'reminder_sent_at': _rem_now}
        elif action == 'delay_next_day_9am':
            tgt = (datetime.now(aest) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            update = {'due_date': tgt.date().isoformat(), 'due_time': '09:00:00', 'reminder_sent_at': _rem_now}
        elif action == 'delay_next_monday_9am':
            now = datetime.now(aest)
            days = (7 - now.weekday()) % 7 or 7
            tgt = (now + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
            update = {'due_date': tgt.date().isoformat(), 'due_time': '09:00:00', 'reminder_sent_at': _rem_now}
        elif action == 'set_status':
            sv = request.args.get('status', '')
            if sv in STATUS_LABELS:
                update = {'lead_status': sv, 'reminder_sent_at': None}
                if sv in ('won', 'lost'):
                    update['status'] = 'completed'
                    update['completed_at'] = datetime.now(pytz.UTC).isoformat()
        elif action == 'set_custom':
            r_date = request.args.get('date', '')
            r_time = request.args.get('time', '09:00')
            if r_date:
                update = {
                    'due_date': r_date,
                    'due_time': r_time + ':00',
                    'reminder_sent_at': _rem_now,
                }
                supabase.table('tasks').update(update).eq('id', task_id).execute()
                return redirect(url_for('lead_detail', task_id=task_id,
                                        reminder_set='1', rdate=r_date, rtime=r_time))
        if update:
            supabase.table('tasks').update(update).eq('id', task_id).execute()
        return redirect(url_for('lead_detail', task_id=task_id))

    # ── Parse description ─────────────────────────────────────────────────
    desc = t.get('description') or ''

    def _field(prefix, text):
        m = re.search(rf'^{re.escape(prefix)}\s*(.+)$', text, re.MULTILINE)
        return m.group(1).strip() if m else ''

    phone   = _field('Phone:', desc)
    email   = _field('Email:', desc)
    crm_url = _field('CRM:', desc)
    os_url  = _field('OpenSolar:', desc)
    source_badge_text = _field('Source:', desc)
    if os_url.lower() in ('pending', ''): os_url = ''

    # Split description into CUSTOMER REQUIREMENTS / MY NOTES sections
    NOTES_SEP = 'MY NOTES:'
    if NOTES_SEP in desc:
        cust_raw, notes_raw = desc.split(NOTES_SEP, 1)
        notes_raw = notes_raw.strip()
    else:
        cust_raw, notes_raw = desc, ''

    # Strip Phone/Email/CRM/OpenSolar/Source/Sub-note header lines from customer requirements,
    # then strip SolarQuotes API junk fields (Id:, Supplierid:, Claimed:, etc.)
    cust_lines = [ln for ln in cust_raw.splitlines()
                  if not ln.startswith(('Phone:', 'Email:', 'CRM:', 'OpenSolar:', 'Source:', 'Sub-note:'))]
    cust_text = _filter_lead_junk('\n'.join(cust_lines).strip())
    # Users sometimes paste HTML (copied from emails) into MY NOTES — strip
    # tags before render so the notes panel shows plain text.
    notes_raw = _strip_lead_html(notes_raw)

    sub_note = _field('Sub-note:', desc)

    # Extract Pipereply contact ID from CRM URL
    crm_cid = ''
    if crm_url:
        m = re.search(r'/detail/([A-Za-z0-9]+)', crm_url)
        if m: crm_cid = m.group(1)

    # ── Page data ─────────────────────────────────────────────────────────
    name        = t.get('client_name') or t.get('title') or 'Unknown Lead'
    lead_status = t.get('lead_status') or 'new_lead'
    badge_text  = STATUS_LABELS.get(lead_status, '🔵 NEW LEAD')
    badge_color = STATUS_COLORS.get(lead_status, '#1e40af')
    src = t.get('source') or t.get('category') or 'DSW Solar'
    try:
        cdt = datetime.fromisoformat(t['created_at'].replace('Z', '+00:00')).astimezone(aest)
        created_str = cdt.strftime('%-d %b %Y')
    except Exception:
        created_str = (t.get('created_at') or '')[:10]

    addr_raw = _field('Address:', desc)
    maps_url = ('https://maps.google.com/?q=' + urllib.parse.quote(addr_raw)) if addr_raw else ''

    STATUSES = [
        ('Intro Call',        'intro_call',        '#1e40af'),
        ('Site Visit',        'site_visit_booked', '#7c3aed'),
        ('Awaiting Docs',     'awaiting_docs',     '#b45309'),
        ('Build Quote',       'build_quote',       '#0369a1'),
        ('Quote Sent',        'quote_submitted',   '#0891b2'),
        ('Quote Follow Up',   'quote_followup',    '#0e7490'),
        ('Revise Quote',      'revise_quote',      '#7c3aed'),
        ('Deciding',          'customer_deciding', '#b45309'),
        ('Nurture',           'nurture',           '#6b7280'),
        ('No Reply 📵',       'no_reply',          '#6b7280'),
        ('WON 🎉',            'won',               '#10b981'),
        ('LOST ❌',           'lost',              '#ef4444'),
    ]

    tomorrow = (datetime.now(aest) + timedelta(days=1)).strftime('%Y-%m-%d')

    # Lead tags — for the checkbox UI on this page. Each entry: (key, label,
    # currently_set). Template renders the checkboxes and the JS below saves
    # via AJAX to /task/<id>/tags on toggle.
    current_tags = _fetch_task_tags(task_id)
    tag_options = [
        (k, LEAD_TAG_META[k][0], k in current_tags) for k in LEAD_TAG_KEYS
    ]

    return render_template_string(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;min-height:100vh}
.hdr{background:#1e40af;color:#fff;padding:18px 16px 14px}
.hdr-row{display:flex;justify-content:space-between;align-items:center;gap:8px}
.hdr h1{font-size:17px;font-weight:700;opacity:.85;margin-bottom:6px}
.badge{display:inline-block;padding:5px 13px;border-radius:20px;font-size:12px;font-weight:700;white-space:nowrap;background:rgba(255,255,255,.22)}
.hdr-meta{font-size:12px;opacity:.7;margin-top:6px}
.card{background:#fff;border-radius:12px;padding:16px;margin:10px 12px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.sec{font-size:10px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.8px;margin-bottom:10px}
.name-row{display:flex;justify-content:space-between;align-items:center;gap:10px}
.lead-name{font-size:20px;font-weight:700;color:#111;flex:1}
.call-btn{display:inline-flex;align-items:center;gap:6px;background:#10b981;color:#fff;padding:10px 16px;border-radius:9px;text-decoration:none;font-weight:700;font-size:14px;white-space:nowrap;flex-shrink:0}
.addr-link{display:block;color:#1e40af;text-decoration:none;font-size:14px;margin-top:10px;font-weight:500}
.btn-row{display:flex;flex-wrap:wrap;gap:8px}
.btn{display:inline-block;padding:10px 18px;border-radius:9px;text-decoration:none;font-weight:700;font-size:14px;color:#fff;text-align:center;border:none;cursor:pointer}
.btn-blue{background:#1e40af}
.btn-amber{background:#f59e0b}
.req-box{background:#f8fafc;border-radius:8px;padding:13px;font-size:14px;line-height:1.7;white-space:pre-wrap;color:#374151}
textarea{width:100%;border:1.5px solid #e2e8f0;border-radius:9px;padding:11px;font-size:14px;font-family:inherit;resize:vertical;min-height:110px;outline:none;color:#111}
textarea:focus{border-color:#1e40af;box-shadow:0 0 0 3px rgba(30,64,175,.1)}
.save-btn{background:#1e40af;color:#fff;border:none;border-radius:9px;padding:11px;font-size:14px;font-weight:700;cursor:pointer;width:100%;margin-top:8px}
.sbtn{display:inline-block;padding:8px 12px;border-radius:8px;font-size:12px;font-weight:700;color:#fff;text-decoration:none;white-space:nowrap}
.dbtn{display:inline-block;padding:8px 13px;border-radius:8px;font-size:13px;font-weight:600;color:#fff;text-decoration:none;background:#6b7280}
.toast{position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#065f46;color:#fff;padding:10px 20px;border-radius:10px;font-size:14px;font-weight:600;z-index:999;display:none}
.sub-note-input::placeholder{color:rgba(255,255,255,0.6)}
</style>
</head>
<body>

<div id="toast" class="toast"></div>

<div class="hdr">
  <div>
    <h1>New DSW Lead</h1>
    <form id="sub-note-form" action="/task/{{ task_id }}/sub_note" method="POST" style="margin:6px 0 4px">
      <input id="sub-note-input" type="text" name="sub_note" value="{{ sub_note | e }}"
             class="sub-note-input"
             placeholder="Sub-status note (e.g. Tried once — try again tomorrow)..."
             style="width:100%;background:rgba(255,255,255,0.15);border:1.5px solid rgba(255,255,255,0.5);border-radius:6px;padding:7px 10px;color:#fff;font-size:13px;outline:none;font-family:inherit">
    </form>
    <div class="hdr-row">
      <span class="badge" style="background:{{ badge_color }}">{{ badge_text }}</span>
    </div>
    <div class="hdr-meta">{{ created_str }} &middot; {{ src }}</div>
  </div>
</div>

<!-- Name + Call -->
<div class="card">
  <div class="name-row">
    <div class="lead-name">{{ name }}</div>
    {% if phone %}<a href="tel:{{ phone }}" class="call-btn">📞 Call</a>{% endif %}
  </div>
  {% if email %}<a href="mailto:{{ email }}" class="addr-link">✉️ {{ email }}</a>{% endif %}
  {% if addr_raw %}<a href="{{ maps_url }}" class="addr-link" target="_blank">📍 {{ addr_raw }}</a>{% endif %}
  {% if source_badge_text %}<div style="margin-top:10px"><span style="display:inline-block;background:#eef2ff;color:#3730a3;padding:4px 12px;border-radius:16px;font-size:12px;font-weight:600">Lead Source: {{ source_badge_text }}</span></div>{% endif %}
</div>

<!-- Pipereply + OpenSolar -->
{% if crm_url or os_url %}
<div class="card">
  <div class="btn-row">
    {% if crm_url %}<a href="{{ crm_url }}" class="btn btn-blue" target="_blank">Pipereply</a>{% endif %}
    {% if os_url %}<a href="{{ os_url }}" class="btn btn-amber" target="_blank">☀️ OpenSolar</a>{% endif %}
    {% if crm_cid %}
    <form action="/task/{{ task_id }}/migrate" method="POST" style="display:inline"
          onsubmit="return confirm('Migrate this task to the new format? The current task will be cancelled and replaced with a fresh one carrying all notes forward.');">
      <button type="submit" class="btn" style="background:#374151;color:#fff">🔄 Migrate to new format</button>
    </form>
    {% endif %}
  </div>
</div>
{% endif %}

<!-- Customer Requirements -->
<div class="card" id="custReqCard" style="{% if not cust_text %}display:none;{% endif %}">
  <div class="sec">Customer Requirements</div>
  <div class="req-box" id="custReqBox">{{ cust_text }}</div>
</div>

<!-- Tags -->
<div class="card">
  <div class="sec">Tags</div>
  <div id="tagsBox" style="display:flex;flex-wrap:wrap;gap:10px 18px;">
    {% for key, label, checked in tag_options %}
    <label style="display:inline-flex;align-items:center;gap:8px;font-size:14px;cursor:pointer;user-select:none;">
      <input type="checkbox" class="tag-cb" data-tag="{{ key }}"
             {% if checked %}checked{% endif %}
             style="width:18px;height:18px;cursor:pointer;accent-color:#1e40af;">
      <span>{{ label }}</span>
    </label>
    {% endfor %}
  </div>
  <div id="tagsStatus" style="font-size:12px;color:#9ca3af;margin-top:8px;min-height:14px;"></div>
</div>

<!-- My Notes -->
<div class="card">
  <div class="sec">My Notes</div>
  <form action="/task/{{ task_id }}/notes" method="POST" id="notesForm">
    <textarea id="notesTextarea" name="notes" placeholder="Call notes, outcome, next steps...">{{ notes_raw }}</textarea>
    <button type="submit" class="save-btn" id="saveNotesBtn">Save Notes</button>
  </form>
</div>

<!-- Lead Status -->
<div class="card">
  <div class="sec">Lead Status</div>
  <div class="btn-row">
    {% for label, slug, col in statuses %}
    <a href="?action=set_status&status={{ slug }}"
       class="sbtn"
       style="background:{{ col }};{% if lead_status == slug %}outline:3px solid #000;{% endif %}">{{ label }}</a>
    {% endfor %}
  </div>
</div>

<!-- Task Delay -->
<div class="card">
  <div class="sec">Remind Me</div>
  <div class="btn-row">
    <a href="?action=delay_1hour"          class="dbtn">+1 Hour</a>
    <a href="?action=delay_1day"           class="dbtn">+1 Day</a>
    <a href="?action=delay_next_day_8am"   class="dbtn">Tmrw 8am</a>
    <a href="?action=delay_next_day_9am"   class="dbtn">Tmrw 9am</a>
    <a href="?action=delay_next_monday_9am" class="dbtn">Mon 9am</a>
  </div>
  <form method="GET" action="" style="margin-top:12px;display:flex;flex-wrap:wrap;gap:8px;align-items:center">
    <input type="hidden" name="action" value="set_custom">
    <input type="date" name="date" value="{{ tomorrow }}"
           style="flex:1;min-width:130px;border:1.5px solid #e2e8f0;border-radius:8px;padding:9px 10px;font-size:14px;font-family:inherit;outline:none;color:#111">
    <input type="time" name="time" value="09:00"
           style="width:110px;border:1.5px solid #e2e8f0;border-radius:8px;padding:9px 10px;font-size:14px;font-family:inherit;outline:none;color:#111">
    <button type="submit"
            style="background:#7c3aed;color:#fff;border:none;border-radius:8px;padding:9px 16px;font-size:14px;font-weight:700;cursor:pointer;white-space:nowrap">Set Reminder</button>
  </form>
</div>

<script>
function showToast(msg,ms){
  var t=document.getElementById('toast');
  t.textContent=msg; t.style.display='block';
  clearTimeout(t._tid);
  t._tid=setTimeout(function(){t.style.display='none';},ms||2500);
}
// Show toast if ?saved=1
if(location.search.includes('saved=1')){
  showToast('Notes saved ✓');
  history.replaceState(null,'',location.pathname);
}
// Show toast if ?reminder_set=1
var sp=new URLSearchParams(location.search);
if(sp.get('reminder_set')==='1'){
  var rd=sp.get('rdate')||'';
  var rt=sp.get('rtime')||'';
  showToast('Reminder set for '+rd+' at '+rt+' ✓',3000);
  history.replaceState(null,'',location.pathname);
}
// Sub-note: async save on Enter or blur (no page reload)
(function(){
  var form=document.getElementById('sub-note-form');
  var inp=document.getElementById('sub-note-input');
  if(!inp||!form) return;
  var _orig=inp.value;
  function _save(){
    var val=inp.value;
    if(val===_orig) return;
    _orig=val;
    fetch(form.action,{
      method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded','X-Requested-With':'fetch'},
      body:'sub_note='+encodeURIComponent(val)
    }).then(function(r){ if(r.ok) showToast('Sub-note saved ✓'); })
      .catch(function(){ showToast('Save failed — try again'); });
  }
  inp.addEventListener('keydown',function(e){
    if(e.key==='Enter'){ e.preventDefault(); _save(); inp.blur(); }
  });
  inp.addEventListener('blur',_save);
  form.addEventListener('submit',function(e){ e.preventDefault(); _save(); });
})();

// Tags: AJAX toggle on each checkbox. POSTs to /task/<id>/tags with
// {tag, enabled}. Optimistic — flips immediately; on server error reverts
// the checkbox and shows a brief status hint.
(function(){
  var taskId = '{{ task_id }}';
  var status = document.getElementById('tagsStatus');
  document.querySelectorAll('.tag-cb').forEach(function(cb){
    cb.addEventListener('change', async function(){
      var tag = cb.dataset.tag;
      var enabled = cb.checked;
      if(status) status.textContent = 'Saving…';
      try {
        var r = await fetch('/task/'+taskId+'/tags', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({tag: tag, enabled: enabled})
        });
        if(!r.ok) throw new Error('HTTP '+r.status);
        var data = await r.json();
        if(!data.ok) throw new Error(data.error || 'save failed');
        if(status) status.textContent = (enabled ? 'Tagged ' : 'Untagged ') + tag + ' ✓';
        setTimeout(function(){ if(status) status.textContent=''; }, 1800);
      } catch (e) {
        cb.checked = !enabled;  // revert
        if(status) status.textContent = 'Save failed — try again';
      }
    });
  });
})();

// MY NOTES: AJAX save + auto-refresh from PipeReply (no page reload).
// Server pulls fresh contact + CRM notes, re-runs summarise(), and returns
// the rebuilt cust_text so we can swap it into the req-box in place.
(function(){
  var form = document.getElementById('notesForm');
  var ta   = document.getElementById('notesTextarea');
  var btn  = document.getElementById('saveNotesBtn');
  var box  = document.getElementById('custReqBox');
  var card = document.getElementById('custReqCard');
  if(!form||!ta||!btn) return;

  form.addEventListener('submit', async function(e){
    e.preventDefault();
    var origLabel = btn.textContent;
    btn.textContent = 'Saving…'; btn.disabled = true;
    try {
      var res = await fetch(form.action, {
        method: 'POST',
        headers: { 'Content-Type':'application/json', 'Accept':'application/json' },
        body: JSON.stringify({ notes: ta.value })
      });
      if(!res.ok) throw new Error('HTTP '+res.status);
      var data = await res.json();
      if(data && data.refreshed && data.refreshed.cust_text) {
        if(box) box.textContent = data.refreshed.cust_text;
        if(card) card.style.display = '';
        showToast('Saved & refreshed from PipeReply ✓', 2200);
      } else {
        showToast('Notes saved ✓');
      }
      // Update sub-note input if the refresh derived a new one
      var subInp = document.getElementById('sub-note-input');
      if(subInp && data && data.refreshed && data.refreshed.sub_note != null) {
        subInp.value = data.refreshed.sub_note;
      }
    } catch (err) {
      // Fall back to a regular form submit so the user never loses their note
      console.error('Notes save AJAX failed:', err);
      form.submit();
      return;
    } finally {
      btn.textContent = origLabel; btn.disabled = false;
    }
  });
})();
</script>
</body>
</html>""",
        name=name, badge_text=badge_text, badge_color=badge_color,
        src=src, created_str=created_str,
        phone=phone, email=email, addr_raw=addr_raw, maps_url=maps_url,
        crm_url=crm_url, os_url=os_url, crm_cid=crm_cid,
        source_badge_text=source_badge_text,
        cust_text=cust_text, notes_raw=notes_raw,
        lead_status=lead_status, statuses=STATUSES,
        task_id=task_id, tomorrow=tomorrow, sub_note=sub_note,
        tag_options=tag_options,
    )


# ── Friendly error page for lead_detail render failures ─────────────────────
_LEAD_DETAIL_ERROR_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lead unavailable</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;min-height:100vh;padding:40px 16px;color:#111}
.card{max-width:520px;margin:0 auto;background:#fff;border-radius:12px;padding:22px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
h2{color:#b91c1c;margin:0 0 10px;font-size:20px}
.meta{color:#6b7280;font-size:13px;margin:10px 0}
.err{background:#fef2f2;border:1px solid #fecaca;padding:10px;border-radius:8px;font-family:monospace;font-size:12px;color:#991b1b;white-space:pre-wrap;margin:12px 0}
form{margin-top:18px}
.btn{display:inline-block;padding:11px 18px;border-radius:9px;font-weight:700;font-size:14px;text-decoration:none;color:#fff;border:none;cursor:pointer;margin-right:8px}
.btn-rebuild{background:#1e40af}
.btn-edit{background:#6b7280}
</style></head><body>
<div class="card">
  <h2>Lead detail unavailable</h2>
  <p>Something went wrong rendering this lead. The task itself is safe — only the
     formatted view failed. Use <strong>Rebuild</strong> to pull fresh data from
     PipeReply and regenerate the description.</p>
  <div class="meta">Task ID: <code>{{ task_id }}</code></div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form action="/task/{{ task_id }}/rebuild" method="POST"
        onsubmit="return confirm('Rebuild this task from PipeReply? Description will be overwritten with fresh data.');">
    <button type="submit" class="btn btn-rebuild">Rebuild from PipeReply</button>
    <a href="/tasks/{{ task_id }}/edit" class="btn btn-edit">Edit task instead</a>
  </form>
</div>
</body></html>"""


@app.route('/task/<task_id>', methods=['GET'])
def lead_detail(task_id):
    """Safe wrapper around _render_lead_detail. A malformed task — missing
    description, bad CRM URL, PipeReply outage, whatever — now shows a
    friendly error + Rebuild button instead of a 500."""
    try:
        return _render_lead_detail(task_id)
    except Exception as e:
        print(f"[lead_detail] render failed for {task_id}: {e}")
        return render_template_string(
            _LEAD_DETAIL_ERROR_HTML,
            task_id=task_id,
            error=str(e)[:400],
        ), 200


@app.route('/task/<task_id>/rebuild', methods=['POST'])
def rebuild_task_from_pipereply(task_id):
    """Rebuild a task's description from fresh PipeReply data.

    Flow:
      1. Look up the task; use client_name + any CRM URL in the description
         to locate the PipeReply contact.
      2. If no CRM URL, search PipeReply by client_name and take the best match.
      3. Pull full contact + CRM notes, run summarise(), rebuild the
         description in the make_task format, set category=DSW Solar.
      4. Send a fresh lead email with all action buttons.

    Any failure returns a plain HTML message (no template dep) so the
    recovery path itself can't 500.
    """
    import re, importlib.util as ilu, requests as rq

    try:
        res = supabase.table('tasks').select('*').eq('id', task_id).single().execute()
        t = res.data if res else None
    except Exception:
        t = None
    if not t:
        return '<p>Task not found.</p>', 404

    desc = t.get('description') or ''
    name = t.get('client_name') or t.get('title') or ''
    # Drop "Call " / "- New DSW Lead" boilerplate so we search on the raw name
    name_query = re.sub(r'^Call\s+', '', name, flags=re.IGNORECASE)
    name_query = re.sub(r'\s*-\s*New DSW Lead.*$', '', name_query, flags=re.IGNORECASE).strip()

    # 1. CRM cid from description
    cid = ''
    m = re.search(r'/detail/([A-Za-z0-9]+)', desc)
    if m:
        cid = m.group(1)

    # 2. Fallback: PipeReply search by name
    if not cid and name_query:
        try:
            PR_TOKEN = os.getenv('PIPEREPLY_TOKEN')
            PR_LOC = os.getenv('PIPEREPLY_LOCATION_ID')
            r = rq.get('https://services.leadconnectorhq.com/contacts/',
                       headers={'Authorization': f'Bearer {PR_TOKEN}',
                                'Content-Type': 'application/json',
                                'Version': '2021-07-28'},
                       params={'locationId': PR_LOC, 'query': name_query, 'limit': 3},
                       timeout=10)
            contacts = (r.json() or {}).get('contacts', []) if r.ok else []
            if contacts:
                cid = contacts[0]['id']
        except Exception as e:
            print(f'[REBUILD] PipeReply search failed: {e}')

    if not cid:
        return (f'<div style="font-family:sans-serif;padding:40px;text-align:center">'
                f'<h3>PipeReply contact not found</h3>'
                f'<p>No CRM URL in the task description and no search match for '
                f'<code>{name_query or "(empty)"}</code>.</p>'
                f'<p><a href="/tasks/{task_id}/edit">Edit task</a></p></div>'), 200

    # 3. Load poller, fetch fresh data, rebuild
    try:
        spec = ilu.spec_from_file_location('dsw_lead_poller',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dsw_lead_poller.py'))
        dsw = ilu.module_from_spec(spec)
        spec.loader.exec_module(dsw)

        full = dsw.get_full(cid) or {}
        fresh_name = ' '.join(w.capitalize() for w in (full.get('contactName') or name_query or 'Unknown').split())
        phone = full.get('phone') or ''
        email = full.get('email') or ''
        addr_parts = [full.get('address1') or '', full.get('city') or '',
                      full.get('state') or '', full.get('postalCode') or '']
        addr = ', '.join(p for p in addr_parts if p)
        src = dsw.source(full) or 'Referral'
        crm_notes_text = dsw.get_crm_notes_bodies(cid)
        summary, referred_by = dsw.summarise(fresh_name, phone, addr, src,
                                             full.get('notes', '') or '',
                                             full.get('customFields', []) or [])
        if not referred_by and src.lower().startswith('referral'):
            try:
                referred_by = dsw.get_referred_by_from_crm(cid)
            except Exception:
                pass
        badge = dsw.source_badge(src, referred_by)
        os_url = dsw.get_os_url_from_crm(cid) or ''
        crm_url = f'https://app.pipereply.com/v2/location/0k6Ix1hW5QoHuUh2YSru/contacts/detail/{cid}'

        email_line  = f"Email: {email}\n" if email else ''
        source_line = f"Source: {badge}\n" if badge else ''
        new_desc = (
            f"Phone: {phone or 'N/A'}\n"
            f"{email_line}{source_line}"
            f"CRM: {crm_url}\n"
            f"OpenSolar: {os_url or 'pending'}\n\n"
            f"{summary}"
        )
        if crm_notes_text:
            new_desc += "\n\nCRM NOTES:\n" + crm_notes_text

        supabase.table('tasks').update({
            'title': f'Call {fresh_name} - New DSW Lead',
            'description': new_desc,
            'client_name': fresh_name,
            'client_phone': phone or None,
            'client_email': email or None,
            'category': 'DSW Solar',
            'lead_status': t.get('lead_status') or 'new_lead',
            'priority': 'high',
            'reminder_sent_at': datetime.now(pytz.UTC).isoformat(),
        }).eq('id', task_id).execute()

        # Send fresh lead email with all action buttons
        try:
            dsw.send_email(fresh_name, phone, addr, src, summary, crm_url, os_url,
                           task_id=task_id, lead_status=(t.get('lead_status') or 'new_lead'),
                           email=email, source_badge_text=badge)
        except Exception as e:
            print(f'[REBUILD] lead email send failed: {e}')

        return redirect(url_for('lead_detail', task_id=task_id, rebuilt='1'))

    except Exception as e:
        print(f'[REBUILD] failed for {task_id}: {e}')
        return (f'<div style="font-family:sans-serif;padding:40px;text-align:center">'
                f'<h3>Rebuild failed</h3><p>{str(e)[:300]}</p>'
                f'<p><a href="/tasks/{task_id}/edit">Edit task</a></p></div>'), 500


@app.route('/task/<task_id>/tags', methods=['GET', 'POST'])
def task_tags(task_id):
    """GET → return the tag set for a task. POST → toggle one tag.

    Body for POST: {"tag": "v2g", "enabled": true}.
    Returns {ok, tags: ['v2g', ...]} on either verb.
    """
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        tag = (body.get('tag') or '').strip()
        enabled = bool(body.get('enabled'))
        if tag not in LEAD_TAG_META:
            return jsonify({'ok': False, 'error': f'unknown tag {tag!r}'}), 400
        try:
            _set_task_tag(task_id, tag, enabled)
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 500
    tags = sorted(_fetch_task_tags(task_id))
    return jsonify({'ok': True, 'tags': tags})


@app.route('/admin/leads-tag/retroscan', methods=['POST'])
def admin_leads_tag_retroscan():
    """Walk every DSW Solar task, regex-scan its description for tag
    keywords (V2G / vehicle-to-grid / three-phase / single-phase / 1-phase /
    3-phase) and insert matching rows into lead_tags. Idempotent — the
    UNIQUE(task_id, tag) constraint silently skips already-tagged rows.

    Returns counts per tag + a list of (task_id, tag) pairs added.
    """
    # Auth: simple internal-API-key gate or admin login
    api_key = request.headers.get('X-Internal-API-Key', '')
    expected = os.getenv('INTERNAL_API_KEY', 'jottask-internal-2026')
    if api_key != expected and 'user_id' not in session:
        return jsonify({'error': 'auth required'}), 401

    compiled = {tag: [re.compile(p, re.IGNORECASE) for p in pats]
                for tag, pats in LEAD_TAG_SCAN_PATTERNS.items()}

    res = supabase.table('tasks').select('id, description')\
        .eq('category', 'DSW Solar').execute()
    tasks = res.data or []

    by_tag = {tag: 0 for tag in LEAD_TAG_SCAN_PATTERNS}
    matched_by_tag = {tag: 0 for tag in LEAD_TAG_SCAN_PATTERNS}
    added = []
    skipped = 0
    insert_errors = []  # surface non-duplicate failures so a missing table
                        # doesn't silently look like a successful zero-tag run
    for t in tasks:
        desc = t.get('description') or ''
        if not desc:
            continue
        for tag, regexes in compiled.items():
            if any(rx.search(desc) for rx in regexes):
                matched_by_tag[tag] += 1
                try:
                    supabase.table('lead_tags').insert(
                        {'task_id': t['id'], 'tag': tag}).execute()
                    by_tag[tag] += 1
                    added.append({'task_id': t['id'], 'tag': tag})
                except Exception as e:
                    msg = str(e)
                    if '23505' in msg or 'duplicate' in msg.lower():
                        skipped += 1
                    else:
                        # Capture up to 5 distinct error shapes for the response
                        snippet = msg[:200]
                        if snippet not in insert_errors and len(insert_errors) < 5:
                            insert_errors.append(snippet)
                        print(f"[retroscan] insert error for {t['id'][:8]}/{tag}: {e}")

    overall_ok = not insert_errors
    return jsonify({
        'ok':                       overall_ok,
        'tasks_scanned':            len(tasks),
        'regex_matches_by_tag':     matched_by_tag,
        'tags_added':               sum(by_tag.values()),
        'inserted_by_tag':          by_tag,
        'already_tagged_skipped':   skipped,
        'insert_errors':            insert_errors,
        'added':                    added if overall_ok else added[:50],
    }), (200 if overall_ok else 500)


@app.route('/admin/reminder-diagnostic', methods=['GET'])
def admin_reminder_diagnostic():
    """Diagnostic JSON for "did the reminder loop fire?" investigations.

    Auth: same X-Internal-API-Key header pattern as the retroscan endpoint
    (default 'jottask-internal-2026' if INTERNAL_API_KEY env unset) OR a
    logged-in session.

    Returns:
      • email_events:     all email_sent + email_failed system_events in
                          the [from_iso, to_iso] window (default last 24h),
                          newest first, max 200.
      • pending_due_today: every pending DSW Solar task with due_date=today
                          (AEST) including its reminder_sent_at and a
                          computed throttle_blocks_now flag.
    """
    api_key = request.headers.get('X-Internal-API-Key', '')
    expected = os.getenv('INTERNAL_API_KEY', 'jottask-internal-2026')
    if api_key != expected and 'user_id' not in session:
        return jsonify({'error': 'auth required'}), 401

    aest = pytz.timezone('Australia/Brisbane')
    now_utc = datetime.now(pytz.UTC)

    from_iso = request.args.get('from') or (now_utc - timedelta(hours=24)).isoformat()
    to_iso   = request.args.get('to')   or now_utc.isoformat()

    # Email events
    ev_q = supabase.table('system_events')\
        .select('created_at, event_type, category, status, error_detail, metadata')\
        .in_('event_type', ['email_sent', 'email_failed'])\
        .gte('created_at', from_iso).lte('created_at', to_iso)\
        .order('created_at', desc=True).limit(200).execute()
    ev_rows = ev_q.data or []
    sent_count   = sum(1 for r in ev_rows if r['event_type'] == 'email_sent')
    failed_count = sum(1 for r in ev_rows if r['event_type'] == 'email_failed')
    by_category = {}
    for r in ev_rows:
        c = r.get('category') or '-'
        by_category[c] = by_category.get(c, 0) + 1

    # Pending DSW Solar tasks due today
    today_aest = datetime.now(aest).date().isoformat()
    four_hours_ago = (now_utc - timedelta(hours=4)).isoformat()
    twentyfour_hours_ago = (now_utc - timedelta(hours=24)).isoformat()

    t_q = supabase.table('tasks')\
        .select('id, title, due_date, due_time, status, lead_status, reminder_sent_at, client_name, category')\
        .eq('status', 'pending').eq('category', 'DSW Solar')\
        .eq('due_date', today_aest)\
        .order('due_time').execute()
    pending = t_q.data or []
    for t in pending:
        rem = t.get('reminder_sent_at')
        t['four_hour_throttle_blocks'] = bool(rem and rem >= four_hours_ago)
        t['twentyfour_hour_throttle_blocks'] = bool(rem and rem >= twentyfour_hours_ago)

    # Heartbeats in same window — confirms worker was alive
    hb_q = supabase.table('system_events')\
        .select('created_at, metadata')\
        .eq('event_type', 'heartbeat')\
        .gte('created_at', from_iso).lte('created_at', to_iso)\
        .order('created_at', desc=True).limit(20).execute()
    hb_rows = hb_q.data or []

    return jsonify({
        'window':           {'from': from_iso, 'to': to_iso},
        'now_utc':          now_utc.isoformat(),
        'now_aest':         datetime.now(aest).isoformat(),
        'today_aest':       today_aest,
        'email_events': {
            'total':        len(ev_rows),
            'sent':         sent_count,
            'failed':       failed_count,
            'by_category':  by_category,
            'rows':         ev_rows,
        },
        'pending_dsw_due_today': pending,
        'recent_heartbeats':    hb_rows[:5],
        'heartbeat_count':      len(hb_rows),
    })


@app.route('/admin/leads-by-tag', methods=['GET'])
@login_required
def admin_leads_by_tag():
    """List every DSW Solar task with the given tag, formatted for batch
    outreach (e.g. "all V2G leads when the SolaX V2G charger lands").

    Query: ?tag=v2g  (defaults to v2g if omitted)
    """
    tag = (request.args.get('tag') or 'v2g').strip()
    if tag not in LEAD_TAG_META:
        return f'<p>Unknown tag: <code>{tag}</code>. Valid: {", ".join(LEAD_TAG_KEYS)}</p>', 400

    # Fetch task_ids for this tag, then hydrate from tasks
    tag_rows = supabase.table('lead_tags').select('task_id, created_at')\
        .eq('tag', tag).order('created_at', desc=True).execute().data or []
    task_ids = [r['task_id'] for r in tag_rows]
    if not task_ids:
        leads = []
    else:
        leads = supabase.table('tasks')\
            .select('id, title, client_name, client_phone, client_email, '
                    'lead_status, status, due_date, created_at')\
            .in_('id', task_ids).order('created_at', desc=True)\
            .execute().data or []

    label = LEAD_TAG_META[tag][0]
    rows_html = ''
    for t in leads:
        name = t.get('client_name') or t.get('title') or 'Unknown'
        phone = t.get('client_phone') or ''
        email = t.get('client_email') or ''
        ls = t.get('lead_status') or t.get('status') or ''
        rows_html += (
            f'<tr>'
            f'<td><a href="/task/{t["id"]}" target="_blank">{name}</a></td>'
            f'<td>{phone or "—"}</td>'
            f'<td>{email or "—"}</td>'
            f'<td>{ls}</td>'
            f'<td>{(t.get("created_at") or "")[:10]}</td>'
            f'</tr>'
        )

    tag_links = ''.join(
        f'<a href="?tag={k}" style="margin-right:8px;padding:4px 10px;'
        f'border-radius:14px;background:{LEAD_TAG_META[k][2]};'
        f'color:{LEAD_TAG_META[k][1]};text-decoration:none;font-size:13px;'
        f'font-weight:700;border:1px solid {LEAD_TAG_META[k][3]};'
        f'{"opacity:1" if k == tag else "opacity:0.55"}">{LEAD_TAG_META[k][0]}</a>'
        for k in LEAD_TAG_KEYS
    )

    emails = [l.get('client_email') for l in leads if l.get('client_email')]
    emails_csv = ', '.join(emails)

    return render_template_string(r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Leads by tag — {{ label }}</title>
<style>
  body{font-family:-apple-system,sans-serif;background:#f1f5f9;margin:0;padding:24px;color:#111}
  .wrap{max-width:1100px;margin:0 auto}
  h1{margin:0 0 14px;font-size:22px}
  .tag-row{margin:0 0 18px;line-height:2}
  table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  th,td{padding:10px 14px;text-align:left;font-size:14px;border-bottom:1px solid #e5e7eb}
  th{background:#f8fafc;font-size:12px;text-transform:uppercase;color:#6b7280;letter-spacing:.5px}
  tr:last-child td{border-bottom:none}
  .meta{color:#6b7280;font-size:13px;margin-bottom:6px}
  details{margin-top:18px;background:#fff;border-radius:10px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
  summary{cursor:pointer;font-weight:700;font-size:14px;color:#1e40af}
  textarea{width:100%;min-height:90px;padding:8px;font-family:monospace;font-size:12px;border:1px solid #e5e7eb;border-radius:6px;margin-top:8px;color:#111}
</style></head><body>
<div class="wrap">
  <h1>Leads tagged {{ label }}</h1>
  <div class="tag-row">{{ tag_links | safe }}</div>
  <div class="meta">{{ count }} lead(s) · sorted newest first</div>
  {% if leads %}
  <table>
    <thead><tr><th>Lead</th><th>Phone</th><th>Email</th><th>Status</th><th>Created</th></tr></thead>
    <tbody>{{ rows | safe }}</tbody>
  </table>
  <details>
    <summary>Copy email addresses ({{ email_count }} with email)</summary>
    <textarea readonly onclick="this.select()">{{ emails_csv }}</textarea>
  </details>
  {% else %}
  <p style="color:#6b7280">No leads tagged with this yet.</p>
  {% endif %}
</div></body></html>""",
        label=label, tag_links=tag_links, count=len(leads),
        rows=rows_html, leads=leads, email_count=len(emails), emails_csv=emails_csv,
    )


@app.route('/task/<task_id>/notes', methods=['POST'])
def lead_save_notes(task_id):
    """Save MY NOTES section, mirror to PipeReply, then refresh customer
    requirements + CRM notes from PipeReply so the lead view reflects the
    latest contact data without a manual migrate.

    Response shape:
      - JSON request (Content-Type: application/json or Accept: application/json):
        returns {ok, saved, refreshed: {cust_text, phone, email, name, ...}}
        so the client can update the DOM in place without a full reload.
      - Form POST: redirects to /task/<id>?saved=1 as before.

    The refresh step is best-effort — if PipeReply is unreachable or the
    AI summary call fails, the local + PipeReply note saves still complete
    and the response just omits the `refreshed` payload.
    """
    import re, requests as rq, importlib.util as ilu

    wants_json = (
        'application/json' in (request.headers.get('Accept') or '').lower()
        or (request.headers.get('Content-Type') or '').startswith('application/json')
    )

    # Read notes from JSON body or form POST
    if (request.headers.get('Content-Type') or '').startswith('application/json'):
        notes_text = ((request.get_json(silent=True) or {}).get('notes') or '').strip()
    else:
        notes_text = (request.form.get('notes') or '').strip()

    # Strip email signature lines
    _sig_re = re.compile(
        r'^(best regards|kind regards|regards|cheers|thanks|thank you)\s*[,.]?\s*$'
        r'|^rob\s+lowe\s*$'
        r'|^m:\s*[\d\s\+]+$'
        r'|^e:\s*\S+@\S+$'
        r'|^w:\s*https?://'
        r'|^(qld|sa|vic|nsw|act|wa|nt|tas)\s*:'
        r'|^--\s*$',
        re.IGNORECASE,
    )
    trimmed = []
    for _line in notes_text.split('\n'):
        if _sig_re.match(_line.strip()):
            break
        trimmed.append(_line)
    notes_text = '\n'.join(trimmed).strip()

    # Fetch current task
    try:
        res = supabase.table('tasks').select('description').eq('id', task_id).single().execute()
        t = res.data
    except Exception:
        if wants_json:
            return jsonify({'ok': False, 'error': 'task not found'}), 404
        return redirect(url_for('lead_detail', task_id=task_id, saved='1'))

    desc = t.get('description') or ''
    NOTES_SEP = 'MY NOTES:'

    # ── Step 1: save locally (existing flow — unchanged) ──────────────────
    if NOTES_SEP in desc:
        cust_part = desc.split(NOTES_SEP, 1)[0].rstrip()
    else:
        cust_part = desc.rstrip()

    # Auto-populate Sub-note from first line of notes if no sub_note exists yet
    if notes_text and not re.search(r'^Sub-note:', cust_part, re.MULTILINE):
        first_note_line = next((l.strip() for l in notes_text.split('\n') if l.strip()), '')
        if first_note_line:
            first_note_line = first_note_line[:80]
            if re.search(r'^OpenSolar:', cust_part, re.MULTILINE):
                cust_part = re.sub(r'^(OpenSolar:[^\n]*)', rf'\1\nSub-note: {first_note_line}',
                                   cust_part, flags=re.MULTILINE, count=1)
            else:
                cust_part = cust_part.rstrip() + f'\nSub-note: {first_note_line}'

    new_desc = cust_part + '\n\n' + NOTES_SEP + '\n' + notes_text if notes_text else cust_part
    supabase.table('tasks').update({'description': new_desc}).eq('id', task_id).execute()

    # ── Step 2: mirror to PipeReply CRM note (existing) ────────────────────
    PIPEREPLY_TOKEN = os.getenv('PIPEREPLY_TOKEN')
    crm_url = ''
    m = re.search(r'^CRM:\s*(.+)$', desc, re.MULTILINE)
    if m: crm_url = m.group(1).strip()
    crm_cid = ''
    if crm_url:
        m2 = re.search(r'/detail/([A-Za-z0-9]+)', crm_url)
        if m2: crm_cid = m2.group(1)

    if crm_cid and PIPEREPLY_TOKEN and notes_text:
        PR_H = {'Authorization': f'Bearer {PIPEREPLY_TOKEN}',
                'Content-Type': 'application/json', 'Version': '2021-07-28'}
        PR_BASE = 'https://services.leadconnectorhq.com'
        aest = pytz.timezone('Australia/Brisbane')
        ts = datetime.now(aest).strftime('%-d %b %Y %I:%M %p')
        try:
            r_notes = rq.get(f'{PR_BASE}/contacts/{crm_cid}/notes', headers=PR_H, timeout=8)
            existing_notes = r_notes.json().get('notes', []) if r_notes.ok else []
            my_note = next((n for n in existing_notes if 'MY NOTES' in (n.get('body') or '')), None)
            note_body = f'MY NOTES ({ts}):\n{notes_text}'
            if my_note:
                rq.put(f'{PR_BASE}/contacts/{crm_cid}/notes/{my_note["id"]}',
                       headers=PR_H, json={'body': note_body}, timeout=8)
            else:
                rq.post(f'{PR_BASE}/contacts/{crm_cid}/notes',
                        headers=PR_H, json={'body': note_body}, timeout=8)
        except Exception as e:
            print(f'[LEAD NOTES] Pipereply note error: {e}')

    # ── Step 3: refresh from PipeReply — pull fresh contact + CRM notes,
    #            re-run summarise(), rebuild customer-requirements section,
    #            overwrite description so the lead view reflects current data.
    refreshed = None
    if crm_cid:
        try:
            spec = ilu.spec_from_file_location('dsw_lead_poller',
                os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dsw_lead_poller.py'))
            dsw = ilu.module_from_spec(spec); spec.loader.exec_module(dsw)

            full = dsw.get_full(crm_cid) or {}
            fresh_name = ' '.join(w.capitalize() for w in (full.get('contactName') or '').split())
            phone = full.get('phone') or ''
            email = full.get('email') or ''
            addr_parts = [full.get('address1') or '', full.get('city') or '',
                          full.get('state') or '', full.get('postalCode') or '']
            addr = ', '.join(p for p in addr_parts if p)
            src = dsw.source(full) or 'Referral'
            crm_notes_text = dsw.get_crm_notes_bodies(crm_cid)
            summary, referred_by = dsw.summarise(
                fresh_name or 'Unknown', phone, addr, src,
                full.get('notes', '') or '',
                full.get('customFields', []) or [],
            )
            badge = dsw.source_badge(src, referred_by)
            os_url = dsw.get_os_url_from_crm(crm_cid) or ''

            # Preserve existing Sub-note if any (might've been auto-set above)
            sub_m = re.search(r'^Sub-note:\s*(.+)$', new_desc, re.MULTILINE)
            sub_note = sub_m.group(1).strip() if sub_m else ''

            # Rebuild the customer-requirements block in the same shape as
            # dsw_lead_poller.make_task — Phone/Email/Source/CRM/OpenSolar
            # headers, Sub-note (if present), blank line, summary, CRM notes.
            email_line  = f"Email: {email}\n" if email else ''
            source_line = f"Source: {badge}\n" if badge else ''
            sub_line    = f"Sub-note: {sub_note}\n" if sub_note else ''
            rebuilt_cust = (
                f"Phone: {phone or 'N/A'}\n"
                f"{email_line}{source_line}"
                f"CRM: {crm_url}\n"
                f"OpenSolar: {os_url or 'pending'}\n"
                f"{sub_line}\n"
                f"{summary.strip()}"
            )
            if crm_notes_text:
                rebuilt_cust += "\n\nCRM NOTES:\n" + crm_notes_text

            full_desc = rebuilt_cust.rstrip()
            if notes_text:
                full_desc += '\n\n' + NOTES_SEP + '\n' + notes_text

            supabase.table('tasks').update({'description': full_desc}).eq('id', task_id).execute()

            # Compute the rendered cust_text the same way lead_detail does
            cust_lines = [ln for ln in rebuilt_cust.splitlines()
                          if not ln.startswith(('Phone:', 'Email:', 'CRM:', 'OpenSolar:', 'Source:', 'Sub-note:'))]
            cust_text_rendered = _filter_lead_junk('\n'.join(cust_lines).strip())

            refreshed = {
                'cust_text':    cust_text_rendered,
                'name':         fresh_name,
                'phone':        phone,
                'email':        email,
                'address':      addr,
                'os_url':       os_url,
                'source_badge': badge,
                'sub_note':     sub_note,
                'notes_text':   notes_text,
            }
        except Exception as e:
            print(f'[LEAD NOTES] PipeReply refresh failed: {e}')

    if wants_json:
        return jsonify({'ok': True, 'saved': True, 'refreshed': refreshed})
    return redirect(url_for('lead_detail', task_id=task_id, saved='1'))


@app.route('/task/<task_id>/migrate', methods=['POST'])
def migrate_dsw_task(task_id):
    """Run the DSW lead flow for an existing task, superseding it with a new one.

    process() in dsw_lead_poller auto-detects any pending DSW Solar task with the
    same client_name (this one), scrapes its MY NOTES + task_notes into a
    PREVIOUS NOTES block on the new task, reuses its OpenSolar URL, and cancels
    the old task with a supersede note.
    """
    import re, importlib.util as ilu

    try:
        res = supabase.table('tasks').select('*').eq('id', task_id).single().execute()
        t = res.data
    except Exception:
        t = None

    if not t:
        return '<html><body style="font-family:sans-serif;text-align:center;padding:50px"><h2>Task not found</h2></body></html>', 404

    if t.get('category') != 'DSW Solar':
        return '<html><body style="font-family:sans-serif;text-align:center;padding:50px"><h2>Migration only supported for DSW Solar tasks</h2></body></html>', 400

    if t.get('status') != 'pending':
        return '<html><body style="font-family:sans-serif;text-align:center;padding:50px"><h2>Task is not pending — nothing to migrate</h2></body></html>', 400

    desc = t.get('description') or ''
    crm_m = re.search(r'^CRM:\s*(\S+)', desc, re.MULTILINE)
    if not crm_m:
        return '<html><body style="font-family:sans-serif;text-align:center;padding:50px"><h2>No Pipereply CRM URL found in task description</h2></body></html>', 400
    cid_m = re.search(r'/detail/([A-Za-z0-9]+)', crm_m.group(1))
    if not cid_m:
        return '<html><body style="font-family:sans-serif;text-align:center;padding:50px"><h2>Could not parse Pipereply contact ID from CRM URL</h2></body></html>', 400
    cid = cid_m.group(1)

    name = t.get('client_name') or t.get('title') or 'Unknown'

    try:
        spec = ilu.spec_from_file_location(
            'dsw_lead_poller',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dsw_lead_poller.py'),
        )
        dsw = ilu.module_from_spec(spec)
        spec.loader.exec_module(dsw)
    except Exception as e:
        print(f'[MIGRATE] Could not load dsw_lead_poller: {e}')
        return f'<html><body style="font-family:sans-serif;text-align:center;padding:50px"><h2>Internal error</h2><p>{e}</p></body></html>', 500

    try:
        dsw.process({'id': cid, 'contactName': name}, task_id=None, lead_status=None, is_new_contact=False)
    except Exception as e:
        print(f'[MIGRATE] dsw.process failed for {task_id}: {e}')
        return f'<html><body style="font-family:sans-serif;text-align:center;padding:50px"><h2>Migration failed</h2><p>{e}</p></body></html>', 500

    # Find the new (pending) task for the same client_name, created after the old one
    try:
        nq = supabase.table('tasks').select('id').eq('category', 'DSW Solar')\
            .eq('status', 'pending').ilike('client_name', f'%{name}%')\
            .order('created_at', desc=True).limit(1).execute()
        new_id = nq.data[0]['id'] if nq.data else task_id
    except Exception:
        new_id = task_id

    return redirect(url_for('lead_detail', task_id=new_id, migrated='1'))


@app.route('/task/<task_id>/sub_note', methods=['POST'])
def lead_save_sub_note(task_id):
    """Save the Sub-status note line in the task description."""
    import re as _re
    sub_note = request.form.get('sub_note', '').strip()

    try:
        res = supabase.table('tasks').select('description').eq('id', task_id).single().execute()
        t = res.data
    except Exception:
        return redirect(url_for('lead_detail', task_id=task_id))

    desc = t.get('description') or ''

    if _re.search(r'^Sub-note:.*$', desc, _re.MULTILINE):
        if sub_note:
            new_desc = _re.sub(r'^Sub-note:.*$', f'Sub-note: {sub_note}', desc, flags=_re.MULTILINE)
        else:
            new_desc = _re.sub(r'^Sub-note:.*\n?', '', desc, flags=_re.MULTILINE)
    else:
        if sub_note:
            # Insert after the OpenSolar line if present, otherwise prepend
            if _re.search(r'^OpenSolar:', desc, _re.MULTILINE):
                new_desc = _re.sub(r'^(OpenSolar:[^\n]*)', rf'\1\nSub-note: {sub_note}', desc, flags=_re.MULTILINE, count=1)
            else:
                new_desc = f'Sub-note: {sub_note}\n' + desc
        else:
            new_desc = desc

    supabase.table('tasks').update({'description': new_desc}).eq('id', task_id).execute()
    if request.headers.get('X-Requested-With') == 'fetch':
        return ('', 204)
    return redirect(url_for('lead_detail', task_id=task_id, saved='1'))


@app.route('/action/<token>')
def email_action(token):
    """Handle email action links without login"""
    task_id, user_id, action = validate_action_token(token)

    if not task_id:
        return render_template_string("""
        <html>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2>Link Expired</h2>
            <p>This action link has expired or already been used.</p>
            <a href="https://www.jottask.app/login" style="color: #6366F1;">Login to Jottask</a>
        </body>
        </html>
        """)

    # Get task details
    task = supabase.table('tasks').select('*').eq('id', task_id).single().execute()
    if not task.data:
        return redirect(url_for('login'))

    task_data = task.data

    if action == 'complete':
        supabase.table('tasks').update({
            'status': 'completed',
            'completed_at': datetime.now(pytz.UTC).isoformat()
        }).eq('id', task_id).execute()

        # Mark token as used
        supabase.table('email_action_tokens').update({
            'used_at': datetime.now(pytz.UTC).isoformat()
        }).eq('token', token).execute()

        return render_template_string("""
        <html>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2 style="color: #10B981;">✅ Task Completed!</h2>
            <p><strong>{{ title }}</strong></p>
            <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
        </body>
        </html>
        """, title=task_data.get('title', 'Task'))

    elif action == 'edit':
        # Auto-login user for edit and redirect to edit page
        session['user_id'] = user_id
        return redirect(url_for('edit_task', task_id=task_id))

    elif action == 'delay_1hour':
        # Delay task by 1 hour from NOW (not from original due time)
        aest = pytz.timezone('Australia/Brisbane')
        new_dt = datetime.now(aest) + timedelta(hours=1)
        new_time = new_dt.strftime('%H:%M:00')
        new_date = new_dt.date().isoformat()

        supabase.table('tasks').update({
            'due_date': new_date,
            'due_time': new_time,
            'reminder_sent_at': datetime.now(pytz.UTC).isoformat()  # Re-remind at new time (throttled)
        }).eq('id', task_id).execute()

        return render_template_string("""
        <html>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2 style="color: #6366F1;">⏰ Task Delayed +1 Hour</h2>
            <p><strong>{{ title }}</strong></p>
            <p>New time: {{ new_time }}</p>
            <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
        </body>
        </html>
        """, title=task_data.get('title', 'Task'), new_time=new_time[:5])

    elif action == 'delay_1day':
        # Delay task by 1 day
        current_date = task_data.get('due_date')
        try:
            due_date = datetime.fromisoformat(current_date)
            new_date = (due_date + timedelta(days=1)).date().isoformat()
        except:
            new_date = (datetime.now(pytz.timezone('Australia/Brisbane')) + timedelta(days=1)).date().isoformat()

        supabase.table('tasks').update({
            'due_date': new_date,
            'reminder_sent_at': datetime.now(pytz.UTC).isoformat()  # Re-remind at new time (throttled)
        }).eq('id', task_id).execute()

        return render_template_string("""
        <html>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2 style="color: #6366F1;">📅 Task Delayed +1 Day</h2>
            <p><strong>{{ title }}</strong></p>
            <p>New date: {{ new_date }}</p>
            <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
        </body>
        </html>
        """, title=task_data.get('title', 'Task'), new_date=new_date)

    elif action in ('delay_next_day_8am', 'delay_next_day_9am', 'delay_next_monday_9am'):
        aest = pytz.timezone('Australia/Brisbane')
        now_aest = datetime.now(aest)
        if action == 'delay_next_day_8am':
            target = (now_aest + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            label = 'Tomorrow 8:00 AM'
        elif action == 'delay_next_day_9am':
            target = (now_aest + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            label = 'Tomorrow 9:00 AM'
        else:
            days_until_monday = (7 - now_aest.weekday()) % 7 or 7
            target = (now_aest + timedelta(days=days_until_monday)).replace(hour=9, minute=0, second=0, microsecond=0)
            label = 'Monday 9:00 AM'

        supabase.table('tasks').update({
            'due_date': target.date().isoformat(),
            'due_time': target.strftime('%H:%M:%S'),
            'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
        }).eq('id', task_id).execute()

        return render_template_string("""
        <html>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h2 style="color: #0EA5E9;">📅 Task Rescheduled</h2>
            <p><strong>{{ title }}</strong></p>
            <p>Moved to: {{ label }}</p>
            <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
        </body>
        </html>
        """, title=task_data.get('title', 'Task'), label=label)

    return redirect(url_for('login'))


@app.route('/api/chat/start', methods=['POST'])
@login_required
def chat_start():
    """Start a new support conversation"""
    user_id = session['user_id']

    # Create conversation
    conv = supabase.table('support_conversations').insert({
        'user_id': user_id,
        'status': 'open'
    }).execute()

    return jsonify({'conversation_id': conv.data[0]['id']})


@app.route('/api/chat/message', methods=['POST'])
@login_required
def chat_message():
    """Handle chat message from user"""
    user_id = session['user_id']
    data = request.get_json()
    conversation_id = data.get('conversation_id')
    message = data.get('message', '').strip()

    if not conversation_id or not message:
        return jsonify({'error': 'Missing data'}), 400

    # Save user message
    supabase.table('support_messages').insert({
        'conversation_id': conversation_id,
        'sender_type': 'user',
        'message': message
    }).execute()

    # Check for escalation keywords
    escalate_keywords = ['speak to human', 'talk to human', 'real person', 'escalate', 'support team', 'help me']
    should_escalate = any(kw in message.lower() for kw in escalate_keywords)

    if should_escalate:
        # Escalate conversation
        supabase.table('support_conversations').update({
            'status': 'escalated',
            'escalated_at': datetime.now(pytz.UTC).isoformat()
        }).eq('id', conversation_id).execute()

        # Get user info and conversation history
        user = supabase.table('users').select('email, full_name').eq('id', user_id).single().execute()
        messages = supabase.table('support_messages').select('*').eq('conversation_id', conversation_id).order('created_at').execute()

        # Build conversation history for email
        history = ""
        for msg in messages.data:
            sender = msg['sender_type'].upper()
            history += f"<p><strong>{sender}:</strong> {msg['message']}</p>"

        # Send admin notification
        send_admin_notification(
            f"Chat Escalation: {user.data['full_name'] or user.data['email']}",
            f"""
            <h2>Support Chat Escalated</h2>
            <p><strong>User:</strong> {user.data['full_name']} ({user.data['email']})</p>
            <p><strong>Time:</strong> {datetime.now(pytz.timezone('Australia/Brisbane')).strftime('%Y-%m-%d %H:%M')} AEST</p>
            <hr>
            <h3>Conversation History:</h3>
            {history}
            <hr>
            <p><a href="https://www.jottask.app/admin/chats/{conversation_id}">View in Admin</a></p>
            """
        )

        return jsonify({
            'escalated': True,
            'response': "I've notified our support team. They'll respond to you via email shortly."
        })

    # Try to find a matching response using smart matching
    response = get_chat_response(message)

    # Default response if no match
    if not response:
        response = "I'm not sure about that specific question. Try asking about:\n\n• Creating tasks or projects\n• Using email integration\n• Delaying or completing tasks\n• Settings and pricing\n\nOr type 'speak to human' to reach our support team directly."

    # Save bot response
    supabase.table('support_messages').insert({
        'conversation_id': conversation_id,
        'sender_type': 'bot',
        'message': response
    }).execute()

    return jsonify({'response': response, 'sender_type': 'bot'})


# ============================================
# SYSTEM HEALTH ENDPOINTS
# ============================================

@app.route('/health')
def health_check():
    """Public health endpoint for uptime monitors. No auth required.

    Returns 200 as long as the web process can serve requests and reach
    the database.  Worker/canary status is included in the JSON body for
    informational purposes but does NOT cause a 503 — the web service
    being up is what the uptime monitor cares about.  Worker-down alerts
    are handled separately by send_self_alert / daily health digest.
    """
    try:
        from monitoring import get_system_health, get_last_canary_status
        health = get_system_health()
        canary = get_last_canary_status()
        return jsonify({
            'status': 'ok',
            'web': 'healthy',
            'worker': health['worker_status'],
            'last_heartbeat': health['last_heartbeat'],
            'heartbeat_age_minutes': health['heartbeat_age_minutes'],
            'canary_status': canary['status'],
            'last_canary': canary['last_canary'],
        }), 200
    except Exception as e:
        # If we can't even query the DB, THEN the web service is unhealthy
        return jsonify({'status': 'error', 'web': 'unhealthy', 'detail': str(e)}), 503


@app.route('/api/system/health')
@login_required
def api_system_health():
    """Authenticated endpoint with full health data."""
    from monitoring import get_system_health
    return jsonify(get_system_health())


# ============================================
# ADMIN DASHBOARD
# ============================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard showing signups, activity, and escalated chats"""

    # Get recent signups
    users = supabase.table('users')\
        .select('*')\
        .order('created_at', desc=True)\
        .limit(20)\
        .execute()

    # Get escalated chats
    escalated = supabase.table('support_conversations')\
        .select('*, users(email, full_name)')\
        .eq('status', 'escalated')\
        .order('escalated_at', desc=True)\
        .limit(10)\
        .execute()

    # Get stats
    total_users = supabase.table('users').select('id', count='exact').execute()
    total_tasks = supabase.table('tasks').select('id', count='exact').execute()
    total_projects = supabase.table('saas_projects').select('id', count='exact').execute()

    stats = {
        'total_users': total_users.count or 0,
        'total_tasks': total_tasks.count or 0,
        'total_projects': total_projects.count or 0
    }

    admin_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Jottask Admin</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F3F4F6; }}
            .nav {{ background: white; padding: 16px 24px; border-bottom: 1px solid #E5E7EB; display: flex; justify-content: space-between; align-items: center; }}
            .nav-brand {{ font-weight: 700; font-size: 20px; color: #6366F1; text-decoration: none; }}
            .main {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }}
            .stat-card {{ background: white; padding: 24px; border-radius: 12px; text-align: center; }}
            .stat-value {{ font-size: 36px; font-weight: 700; color: #6366F1; }}
            .stat-label {{ color: #6B7280; margin-top: 4px; }}
            .card {{ background: white; border-radius: 12px; margin-bottom: 24px; overflow: hidden; }}
            .card-header {{ padding: 16px 20px; border-bottom: 1px solid #E5E7EB; font-weight: 600; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #F3F4F6; }}
            th {{ background: #F9FAFB; font-weight: 600; color: #374151; }}
            .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 12px; }}
            .badge-trial {{ background: #FEF3C7; color: #92400E; }}
            .badge-active {{ background: #D1FAE5; color: #065F46; }}
            .badge-escalated {{ background: #FEE2E2; color: #991B1B; }}
            a {{ color: #6366F1; }}
        </style>
    </head>
    <body>
        <nav class="nav">
            <a href="/admin" class="nav-brand">Jottask Admin</a>
            <a href="/dashboard">Back to App</a>
        </nav>
        <main class="main">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-value">{stats['total_users']}</div>
                    <div class="stat-label">Total Users</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{stats['total_tasks']}</div>
                    <div class="stat-label">Total Tasks</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{stats['total_projects']}</div>
                    <div class="stat-label">Total Projects</div>
                </div>
            </div>

            <div class="card" style="padding: 20px;">
                <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                    <div style="flex: 1; min-width: 200px;">
                        <strong>Reminder Tools</strong>
                        <div style="color: #6B7280; font-size: 14px; margin-top: 2px;">Resend sends emails now. Reset clears reminder flags so the scheduler picks them up again.</div>
                    </div>
                    <button id="resendBtn" onclick="resendReminders()" style="background: #6366F1; color: white; border: none; padding: 10px 16px; border-radius: 8px; font-weight: 600; cursor: pointer; white-space: nowrap; font-size: 13px;">Resend Now</button>
                    <button id="resetBtn" onclick="resetReminders()" style="background: #F59E0B; color: white; border: none; padding: 10px 16px; border-radius: 8px; font-weight: 600; cursor: pointer; white-space: nowrap; font-size: 13px;">Reset Flags</button>
                </div>
                <div id="reminderResult" style="display: none; margin-top: 12px; padding: 12px; background: #F9FAFB; border-radius: 8px; font-size: 13px; font-family: monospace; white-space: pre-wrap; max-height: 300px; overflow-y: auto;"></div>
                <script>
                async function adminAction(url, btn, label) {{
                    const result = document.getElementById('reminderResult');
                    btn.disabled = true;
                    btn.textContent = 'Working...';
                    btn.style.opacity = '0.6';
                    result.style.display = 'block';
                    result.textContent = 'Processing...';
                    try {{
                        const res = await fetch(url, {{ method: 'POST' }});
                        const data = await res.json();
                        btn.textContent = data.message ? 'Done' : 'No results';
                        btn.style.background = '#10B981';
                        result.textContent = JSON.stringify(data, null, 2);
                        setTimeout(() => {{ btn.textContent = label; btn.style.background = url.includes('reset') ? '#F59E0B' : '#6366F1'; btn.style.opacity = '1'; btn.disabled = false; }}, 5000);
                    }} catch(e) {{
                        btn.textContent = 'Error';
                        btn.style.background = '#EF4444';
                        result.textContent = 'Error: ' + e.message;
                        setTimeout(() => {{ btn.textContent = label; btn.style.background = url.includes('reset') ? '#F59E0B' : '#6366F1'; btn.style.opacity = '1'; btn.disabled = false; }}, 5000);
                    }}
                }}
                function resendReminders() {{ adminAction('/admin/resend-reminders', document.getElementById('resendBtn'), 'Resend Now'); }}
                function resetReminders() {{ adminAction('/admin/reset-reminders', document.getElementById('resetBtn'), 'Reset Flags'); }}
                </script>
            </div>

            <div class="card">
                <div class="card-header">Escalated Support Chats</div>
                <table>
                    <tr><th>User</th><th>Email</th><th>Escalated</th><th>Action</th></tr>
                    {''.join(f"<tr><td>{c.get('users', {}).get('full_name', 'N/A')}</td><td>{c.get('users', {}).get('email', 'N/A')}</td><td>{c.get('escalated_at', '')[:16] if c.get('escalated_at') else 'N/A'}</td><td><a href='/admin/chats/{c['id']}'>View</a></td></tr>" for c in (escalated.data or [])) or '<tr><td colspan="4" style="text-align: center; color: #9CA3AF;">No escalated chats</td></tr>'}
                </table>
            </div>

            <div class="card">
                <div class="card-header">Recent Signups</div>
                <table>
                    <tr><th>Name</th><th>Email</th><th>Status</th><th>Joined</th></tr>
                    {''.join(f"<tr><td>{u.get('full_name', 'N/A')}</td><td>{u.get('email')}</td><td><span class='badge badge-{u.get('subscription_status', 'trial')}'>{u.get('subscription_status', 'trial')}</span></td><td>{u.get('created_at', '')[:10] if u.get('created_at') else 'N/A'}</td></tr>" for u in (users.data or []))}
                </table>
            </div>
        </main>
    </body>
    </html>
    """
    return admin_html


@app.route('/admin/email-log')
@admin_required
def admin_email_log():
    """Last 100 email send attempts from system_events (email_sent + email_failed)."""
    try:
        res = supabase.table('system_events')\
            .select('created_at,event_type,category,status,error_detail,metadata,user_id')\
            .in_('event_type', ['email_sent', 'email_failed'])\
            .order('created_at', desc=True)\
            .limit(100)\
            .execute()
        rows = res.data or []
    except Exception as e:
        rows = []
        print(f"[admin_email_log] query failed: {e}")

    sent_count = sum(1 for r in rows if r.get('event_type') == 'email_sent')
    failed_count = sum(1 for r in rows if r.get('event_type') == 'email_failed')

    def _row_html(r):
        md = r.get('metadata') or {}
        ts = (r.get('created_at') or '')[:19].replace('T', ' ')
        to_email = (md.get('to_email') or '—')
        subject = (md.get('subject') or '—')[:80]
        cat = r.get('category') or '—'
        is_fail = r.get('event_type') == 'email_failed'
        status_badge = ('<span class="bd bd-fail">FAILED</span>' if is_fail
                        else '<span class="bd bd-ok">sent</span>')
        err = r.get('error_detail') or ''
        err_cell = f'<td class="err">{_escape_html(err[:200])}</td>' if is_fail else '<td></td>'
        return (
            f'<tr class="{ "row-fail" if is_fail else "" }">'
            f'<td class="mono">{ts}</td>'
            f'<td>{status_badge}</td>'
            f'<td>{_escape_html(cat)}</td>'
            f'<td>{_escape_html(to_email)}</td>'
            f'<td>{_escape_html(subject)}</td>'
            f'{err_cell}'
            f'</tr>'
        )

    rows_html = ''.join(_row_html(r) for r in rows) or '<tr><td colspan="6" style="text-align:center;padding:30px;color:#6b7280">No email events logged yet.</td></tr>'

    return f"""<!DOCTYPE html><html><head>
<meta charset="utf-8"><title>Jottask — Email Log</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f3f4f6; margin: 0; padding: 24px; color: #111827; }}
  h1 {{ margin: 0 0 6px; font-size: 22px; }}
  .sub {{ color: #6b7280; font-size: 13px; margin-bottom: 20px; }}
  .wrap {{ max-width: 1200px; margin: 0 auto; background: white; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.06); overflow: hidden; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 12px 14px; background: #f9fafb; border-bottom: 1px solid #e5e7eb; font-weight: 600; color: #374151; text-transform: uppercase; font-size: 11px; letter-spacing: .3px; }}
  td {{ padding: 10px 14px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }}
  tr.row-fail td {{ background: #fef2f2; }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: nowrap; color: #6b7280; }}
  .bd {{ display: inline-block; padding: 2px 9px; border-radius: 11px; font-size: 11px; font-weight: 700; }}
  .bd-ok {{ background: #dcfce7; color: #166534; }}
  .bd-fail {{ background: #fee2e2; color: #991b1b; }}
  .err {{ color: #991b1b; font-family: ui-monospace, monospace; font-size: 12px; max-width: 340px; word-break: break-word; }}
  .counts {{ display: flex; gap: 12px; margin-bottom: 14px; }}
  .pill {{ background: white; border: 1px solid #e5e7eb; padding: 8px 14px; border-radius: 8px; font-size: 13px; }}
  .pill strong {{ color: #111827; }}
  a.back {{ color: #6366F1; text-decoration: none; font-size: 13px; }}
</style></head><body>
<div style="max-width:1200px;margin:0 auto">
  <p><a href="/admin" class="back">← Back to admin</a></p>
  <h1>Email Log</h1>
  <div class="sub">Last 100 email events from <code>system_events</code>.</div>
  <div class="counts">
    <div class="pill">Sent: <strong>{sent_count}</strong></div>
    <div class="pill">Failed: <strong style="color:#991b1b">{failed_count}</strong></div>
  </div>
  <div class="wrap">
    <table>
      <thead><tr><th>When (UTC)</th><th>Status</th><th>Category</th><th>To</th><th>Subject</th><th>Error</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>
</body></html>"""


@app.route('/admin/chats/<conversation_id>')
@admin_required
def admin_chat_view(conversation_id):
    """View a support conversation"""

    # Get conversation with user info
    conv = supabase.table('support_conversations')\
        .select('*, users(email, full_name)')\
        .eq('id', conversation_id)\
        .single()\
        .execute()

    if not conv.data:
        return "Not found", 404

    # Get messages
    messages = supabase.table('support_messages')\
        .select('*')\
        .eq('conversation_id', conversation_id)\
        .order('created_at')\
        .execute()

    user_info = conv.data.get('users', {})

    messages_html = ""
    for msg in (messages.data or []):
        sender_class = msg['sender_type']
        messages_html += f"<div class='message {sender_class}'><strong>{msg['sender_type'].upper()}</strong><p>{msg['message']}</p><small>{msg['created_at'][:16]}</small></div>"

    admin_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Chat - Jottask Admin</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F3F4F6; }}
            .nav {{ background: white; padding: 16px 24px; border-bottom: 1px solid #E5E7EB; display: flex; justify-content: space-between; }}
            .nav-brand {{ font-weight: 700; font-size: 20px; color: #6366F1; text-decoration: none; }}
            .main {{ max-width: 800px; margin: 0 auto; padding: 24px; }}
            .card {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 24px; }}
            .message {{ padding: 12px; margin-bottom: 12px; border-radius: 8px; }}
            .message.user {{ background: #EEF2FF; }}
            .message.bot {{ background: #F3F4F6; }}
            .message.admin {{ background: #FEF3C7; }}
            .message small {{ color: #9CA3AF; }}
            .reply-form {{ display: flex; gap: 8px; }}
            .reply-form input {{ flex: 1; padding: 12px; border: 1px solid #E5E7EB; border-radius: 8px; }}
            .reply-form button {{ padding: 12px 24px; background: #6366F1; color: white; border: none; border-radius: 8px; cursor: pointer; }}
        </style>
    </head>
    <body>
        <nav class="nav">
            <a href="/admin" class="nav-brand">← Back to Admin</a>
        </nav>
        <main class="main">
            <div class="card">
                <h2>Chat with {user_info.get('full_name', 'User')}</h2>
                <p style="color: #6B7280;">Email: {user_info.get('email', 'N/A')} | Status: {conv.data.get('status', 'open')}</p>
            </div>

            <div class="card">
                <h3 style="margin-bottom: 16px;">Messages</h3>
                {messages_html}
            </div>

            <div class="card">
                <form method="POST" action="/admin/chats/{conversation_id}/reply" class="reply-form">
                    <input type="text" name="message" placeholder="Type your reply..." required>
                    <button type="submit">Send Reply</button>
                </form>
            </div>
        </main>
    </body>
    </html>
    """
    return admin_html


@app.route('/admin/chats/<conversation_id>/reply', methods=['POST'])
@admin_required
def admin_chat_reply(conversation_id):
    """Send admin reply to a chat"""
    message = request.form.get('message', '').strip()

    if not message:
        return redirect(url_for('admin_chat_view', conversation_id=conversation_id))

    # Save admin message
    supabase.table('support_messages').insert({
        'conversation_id': conversation_id,
        'sender_type': 'admin',
        'message': message
    }).execute()

    # Get user email to send notification
    conv = supabase.table('support_conversations')\
        .select('users(email, full_name)')\
        .eq('id', conversation_id)\
        .single()\
        .execute()

    if conv.data and conv.data.get('users'):
        user_email = conv.data['users']['email']
        user_name = conv.data['users'].get('full_name', 'there')

        # Send email to user about admin reply
        html = f"""
        <h2>Hi {user_name},</h2>
        <p>We've replied to your support request:</p>
        <blockquote style="background: #F3F4F6; padding: 16px; border-radius: 8px; margin: 16px 0;">
            {message}
        </blockquote>
        <p>You can continue the conversation in the chat widget on your <a href="https://www.jottask.app/dashboard">Jottask dashboard</a>.</p>
        <p>Best,<br>Jottask Support</p>
        """
        send_email(user_email, "Reply from Jottask Support", html)

    # Mark as resolved if this is a reply
    supabase.table('support_conversations').update({
        'status': 'resolved',
        'resolved_at': datetime.now(pytz.UTC).isoformat()
    }).eq('id', conversation_id).execute()

    return redirect(url_for('admin_chat_view', conversation_id=conversation_id))


@app.route('/admin/resend-reminders', methods=['POST'])
@admin_required
def admin_resend_reminders():
    """Resend reminders for ALL tasks due in the last 48 hours (whether or not they were already sent)."""
    from saas_scheduler import generate_reminder_email_html

    today = datetime.now(pytz.UTC).strftime('%Y-%m-%d')
    cutoff = (datetime.now(pytz.UTC) - timedelta(hours=48)).strftime('%Y-%m-%d')

    print(f"[resend-reminders] cutoff={cutoff} today={today}")

    # Find PENDING tasks due between cutoff and today (skip completed — they don't need reminders)
    result = supabase.table('tasks') \
        .select('id, title, due_date, due_time, priority, status, client_name, user_id') \
        .eq('status', 'pending') \
        .gte('due_date', cutoff) \
        .lte('due_date', today) \
        .execute()

    all_tasks = result.data or []
    print(f"[resend-reminders] found {len(all_tasks)} pending tasks due {cutoff} to {today}")

    if not all_tasks:
        return jsonify({
            'message': 'No tasks due in the last 48 hours',
            'sent': 0,
            'debug_cutoff': cutoff,
            'debug_today': today
        })

    # Get user details for each task
    user_ids = list(set(t['user_id'] for t in all_tasks if t.get('user_id')))
    users = {}
    for uid in user_ids:
        u = supabase.table('users').select('id, email, full_name, timezone, alternate_emails').eq('id', uid).execute()
        if u.data:
            users[uid] = u.data[0]

    sent_count = 0
    details = []
    for task in all_tasks:
        user_id = task.get('user_id')
        user = users.get(user_id)
        if not user:
            continue

        display_time = task.get('due_time', 'today')
        subject = f"Overdue: {task['title'][:50]} - due {task.get('due_date')} {display_time or ''}"
        html_content = generate_reminder_email_html(task, display_time or 'today', user.get('full_name', ''), is_overdue=True)

        # Send to primary email only
        success, error = send_email(user['email'], subject, html_content,
                                   category='reminder', user_id=user_id, task_id=task['id'])
        if success:
            sent_count += 1
            details.append(f"{task['title'][:40]} -> {user['email']}")
        else:
            details.append(f"FAILED {task['title'][:40]} -> {user['email']}: {error}")

        # NOTE: Do NOT update reminder_sent_at here — this is a manual resend,
        # the scheduler should continue its normal reminder cycle independently

    return jsonify({
        'message': f'Sent {sent_count} reminder(s) for {len(all_tasks)} task(s)',
        'sent': sent_count,
        'tasks': details
    })


@app.route('/admin/tasks/reset-reminder-flags', methods=['POST'])
def admin_tasks_reset_reminder_flags():
    """Targeted reset of reminder_sent_at=NULL for a specific list of task_ids.

    Used to manually unstick reminders that got blocked by a throttle gate
    (e.g. the pre-deploy "already reminded today" issue from 2026-04-30).
    Next scheduler tick will pick them up and fire normally.

    Auth: same X-Internal-API-Key pattern as the retroscan/diagnostic
    endpoints, OR a logged-in session. Body: {"task_ids": ["uuid", ...]}.
    """
    api_key = request.headers.get('X-Internal-API-Key', '')
    expected = os.getenv('INTERNAL_API_KEY', 'jottask-internal-2026')
    if api_key != expected and 'user_id' not in session:
        return jsonify({'error': 'auth required'}), 401

    body = request.get_json(silent=True) or {}
    task_ids = [i for i in (body.get('task_ids') or []) if isinstance(i, str) and i.strip()]
    if not task_ids:
        return jsonify({'error': 'task_ids required'}), 400

    reset = []
    errors = []
    for tid in task_ids:
        try:
            r = supabase.table('tasks').update({'reminder_sent_at': None})\
                .eq('id', tid).execute()
            reset.append(tid)
        except Exception as e:
            errors.append({'task_id': tid, 'error': str(e)[:200]})
    return jsonify({'ok': not errors, 'reset_count': len(reset),
                    'reset': reset, 'errors': errors})


@app.route('/admin/reset-reminders', methods=['POST'])
@admin_required
def admin_reset_reminders():
    """Reset reminder_sent_at to NULL for pending overdue tasks so the scheduler picks them up again."""
    today = datetime.now(pytz.UTC).strftime('%Y-%m-%d')
    seven_days_ago = (datetime.now(pytz.UTC) - timedelta(days=7)).strftime('%Y-%m-%d')

    # Find pending tasks that are overdue (due_date <= today) and have reminder_sent_at set
    result = supabase.table('tasks') \
        .select('id, title, due_date, due_time, reminder_sent_at') \
        .eq('status', 'pending') \
        .not_.is_('reminder_sent_at', 'null') \
        .lte('due_date', today) \
        .gte('due_date', seven_days_ago) \
        .execute()

    tasks = result.data or []
    if not tasks:
        return jsonify({'message': 'No pending overdue tasks with reminder flags to reset', 'reset': 0})

    reset_count = 0
    details = []
    for task in tasks:
        supabase.table('tasks').update({
            'reminder_sent_at': None
        }).eq('id', task['id']).execute()
        reset_count += 1
        details.append(f"{task['title'][:50]} (due {task.get('due_date')})")

    return jsonify({
        'message': f'Reset {reset_count} task(s) — scheduler will re-remind on next tick',
        'reset': reset_count,
        'tasks': details
    })


# ============================================
# MAIN
# ============================================



# ============================================
# V2 APPROVAL ROUTES (Tiered Action System)
# ============================================

def _resolve_user_for_action(sb, pending_row):
    """Resolve user_id and business_id from a pending_actions row.
    Uses the row's user_id when present, falls back to env var for legacy actions."""
    action_user_id = pending_row.get('user_id')
    fallback_admin_id = os.getenv('FALLBACK_ADMIN_ID', '')

    if not action_user_id:
        if fallback_admin_id:
            print(f"[WARNING] pending_action has no user_id, using FALLBACK_ADMIN_ID")
            action_user_id = fallback_admin_id
        else:
            print(f"[WARNING] pending_action has no user_id and no FALLBACK_ADMIN_ID set")
            return None, None

    try:
        user_result = sb.table('users').select('id, ai_context').eq('id', action_user_id).execute()
        if user_result.data:
            user = user_result.data[0]
            ai_ctx = user.get('ai_context') or {}
            businesses = ai_ctx.get('businesses', {})
            default_biz = ai_ctx.get('default_business', '')
            business_id = businesses.get(default_biz, '')
            return str(user['id']), business_id
    except Exception:
        pass

    return str(action_user_id), ''

@app.route('/action/approve')
def approve_action():
    """Approve a pending Tier 2 action via email button click"""
    token = request.args.get('token')
    if not token:
        return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>Missing token</p></div></body></html>', 400
    try:
        import json as _json
        from datetime import datetime
        import pytz
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = get_admin_key()  # service-role first, anon fallback
        from supabase import create_client
        sb = create_client(supabase_url, supabase_key)
        result = sb.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()
        if not result.data:
            already = sb.table('pending_actions').select('status').eq('token', token).execute()
            if already.data:
                st = already.data[0]['status']
                return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fef3c7;border-radius:12px;padding:30px"><h2>Already Processed</h2><p>This action was already <strong>{st}</strong>.</p><a href="https://www.jottask.app/dashboard" style="color:#3b82f6">Dashboard</a></div></body></html>'
            return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Not Found</h2><p>Action not found or expired</p></div></body></html>', 404
        action_data = result.data[0]
        action = _json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_type = action.get('action_type', '')
        action_title = action.get('title', 'Unknown action')
        today_str = datetime.now(pytz.timezone('Australia/Brisbane')).strftime('%Y-%m-%d')
        resolved_user_id, resolved_business_id = _resolve_user_for_action(sb, action_data)
        task_data = {
            'title': action_title,
            'description': action.get('description', action.get('crm_notes', '')),
            'status': 'pending',
            'created_at': datetime.now(pytz.UTC).isoformat(),
            'due_date': action.get('due_date') or today_str,
            'business_id': resolved_business_id,
            'user_id': resolved_user_id,
            'client_name': action.get('customer_name', ''),
            'priority': 'medium',
        }
        if action_type == 'update_crm':
            task_data['category'] = 'crm'
            task_data['title'] = f"CRM Update: {action.get('customer_name', '')}" if action.get('customer_name') else action_title
        elif action_type == 'send_email':
            task_data['category'] = 'email'
        elif action_type == 'create_calendar_event':
            task_data['category'] = 'calendar'
        elif action_type == 'change_deal_status':
            task_data['category'] = 'deals'
        # Try CRM push for update_crm actions before falling back to task creation
        crm_synced = False
        crm_message = ''
        if action_type == 'update_crm':
            try:
                from crm_manager import CRMManager
                _crm = CRMManager()
                crm_result = _crm.execute_crm_update(
                    user_id=resolved_user_id,
                    customer_name=action.get('customer_name', ''),
                    crm_notes=action.get('crm_notes', action.get('description', '')),
                    customer_email=action.get('customer_email', ''),
                )
                if crm_result.success:
                    crm_synced = True
                    crm_message = crm_result.message
                    print(f"CRM sync success: {crm_message}")
                else:
                    print(f"CRM sync skipped (falling back to task): {crm_result.message}")
            except Exception as crm_err:
                print(f"CRM sync error (falling back to task): {crm_err}")
        if crm_synced:
            # CRM push succeeded — mark synced, skip task creation
            sb.table('pending_actions').update({
                'status': 'approved',
                'crm_synced': True,
                'crm_synced_at': datetime.now(pytz.UTC).isoformat(),
                'processed_at': datetime.now(pytz.UTC).isoformat()
            }).eq('token', token).execute()
            return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#dcfce7;border-radius:12px;padding:30px"><h2 style="color:#166534">CRM Updated</h2><p><strong>{_escape_html(action_title)}</strong></p><p>{_escape_html(crm_message)}</p><a href="https://www.jottask.app/dashboard" style="display:inline-block;margin-top:16px;padding:10px 24px;background:#22c55e;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Dashboard</a></div></body></html>'
        # Fallback: create task (original behavior)
        sb.table('tasks').insert(task_data).execute()
        sb.table('pending_actions').update({
            'status': 'approved',
            'processed_at': datetime.now(pytz.UTC).isoformat()
        }).eq('token', token).execute()
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#dcfce7;border-radius:12px;padding:30px"><h2 style="color:#166534">Action Approved</h2><p><strong>{action_title}</strong></p><p>The action has been executed.</p><a href="https://www.jottask.app/dashboard" style="display:inline-block;margin-top:16px;padding:10px 24px;background:#22c55e;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Dashboard</a></div></body></html>'
    except Exception as e:
        print(f'Error approving action: {e}')
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>{str(e)}</p></div></body></html>', 500


@app.route('/action/reject')
def reject_action():
    """Skip/reject a pending Tier 2 action"""
    token = request.args.get('token')
    if not token:
        return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>Missing token</p></div></body></html>', 400
    try:
        import json as _json
        from datetime import datetime
        import pytz
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = get_admin_key()  # service-role first, anon fallback
        from supabase import create_client
        sb = create_client(supabase_url, supabase_key)
        result = sb.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()
        if not result.data:
            already = sb.table('pending_actions').select('status').eq('token', token).execute()
            if already.data:
                st = already.data[0]['status']
                return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fef3c7;border-radius:12px;padding:30px"><h2>Already Processed</h2><p>This action was already <strong>{st}</strong>.</p></div></body></html>'
            return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Not Found</h2><p>Action not found or expired</p></div></body></html>', 404
        action_data = result.data[0]
        action = _json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_title = action.get('title', 'Unknown action')
        sb.table('pending_actions').update({
            'status': 'rejected',
            'processed_at': datetime.now(pytz.UTC).isoformat()
        }).eq('token', token).execute()
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Action Skipped</h2><p><strong>{action_title}</strong></p><p>This action has been skipped.</p><a href="https://www.jottask.app/dashboard" style="display:inline-block;margin-top:16px;padding:10px 24px;background:#6b7280;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Dashboard</a></div></body></html>'
    except Exception as e:
        print(f'Error rejecting action: {e}')
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>{str(e)}</p></div></body></html>', 500


@app.route('/action/edit')
def edit_action():
    """Show editable pending action details with form fields"""
    token = request.args.get('token')
    saved = request.args.get('saved', '')
    if not token:
        return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>Missing token</p></div></body></html>', 400
    try:
        import json as _json
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = get_admin_key()  # service-role first, anon fallback
        from supabase import create_client
        sb = create_client(supabase_url, supabase_key)
        result = sb.table('pending_actions').select('*').eq('token', token).execute()
        if not result.data:
            return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Not Found</h2><p>Action not found</p></div></body></html>', 404
        action_data = result.data[0]
        action = _json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_title = action.get('title', 'Unknown action')
        action_type = action.get('action_type', '')
        action_type_display = action_type.replace('_', ' ').upper()
        description = action.get('description', action.get('crm_notes', ''))
        customer = action.get('customer_name', '')
        status = action_data['status']
        due_date = action.get('due_date', '')
        due_time = action.get('due_time', '')
        priority = action.get('priority', 'medium')
        email_to = action.get('email_to', action.get('recipient', ''))
        email_body = action.get('email_body', action.get('body', ''))
        source_email = action.get('source_email_subject', action.get('source_subject', ''))

        saved_banner = ''
        if saved == '1':
            saved_banner = '<div style="background:#dcfce7;border:1px solid #86efac;border-radius:8px;padding:12px;margin-bottom:16px;text-align:center;color:#166534;font-weight:600">Changes saved successfully</div>'

        if status != 'pending':
            return f'''<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:50px auto;padding:0 16px">
<div style="background:#eff6ff;border-radius:12px;padding:30px">
<h2 style="color:#1e40af;text-align:center">Action Details</h2>
<p style="text-align:center;color:#666">This action has already been <strong>{status}</strong>.</p>
<div style="text-align:center;margin-top:16px"><a href="https://www.jottask.app/dashboard" style="color:#3b82f6">Go to Dashboard</a></div>
</div></body></html>'''

        # Build type-specific extra fields
        extra_fields = ''
        if action_type in ('send_email', 'draft_email'):
            extra_fields = f'''
            <label style="display:block;margin-bottom:4px;font-weight:600;color:#374151;font-size:14px">Recipient Email</label>
            <input type="text" name="email_to" value="{_escape_html(email_to)}" style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;margin-bottom:16px;box-sizing:border-box" />
            <label style="display:block;margin-bottom:4px;font-weight:600;color:#374151;font-size:14px">Email Body</label>
            <textarea name="email_body" rows="5" style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;margin-bottom:16px;box-sizing:border-box;resize:vertical">{_escape_html(email_body)}</textarea>'''
        if action_type == 'create_calendar_event':
            extra_fields += f'''
            <label style="display:block;margin-bottom:4px;font-weight:600;color:#374151;font-size:14px">Due Time</label>
            <input type="time" name="due_time" value="{_escape_html(due_time)}" style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;margin-bottom:16px;box-sizing:border-box" />'''

        return f'''<html>
<head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Edit Action - Jottask</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:50px auto;padding:0 16px;background:#f8fafc">
<div style="background:#eff6ff;border-radius:12px;padding:30px;box-shadow:0 1px 3px rgba(0,0,0,0.1)">
<h2 style="color:#1e40af;text-align:center;margin-top:0">Edit Action</h2>
<p style="text-align:center;color:#64748b;font-size:13px;margin-bottom:20px">{action_type_display}</p>
{saved_banner}
<form method="POST" action="/action/save">
<input type="hidden" name="token" value="{token}" />
<input type="hidden" name="action_type" value="{_escape_html(action_type)}" />

<div style="background:white;border-radius:8px;padding:20px;margin-bottom:16px">

<label style="display:block;margin-bottom:4px;font-weight:600;color:#374151;font-size:14px">Title / Subject</label>
<input type="text" name="title" value="{_escape_html(action_title)}" style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;margin-bottom:16px;box-sizing:border-box" />

<label style="display:block;margin-bottom:4px;font-weight:600;color:#374151;font-size:14px">Customer Name</label>
<input type="text" name="customer_name" value="{_escape_html(customer)}" style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;margin-bottom:16px;box-sizing:border-box" />

<label style="display:block;margin-bottom:4px;font-weight:600;color:#374151;font-size:14px">Details / CRM Notes</label>
<textarea name="description" rows="6" style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;margin-bottom:16px;box-sizing:border-box;resize:vertical">{_escape_html(description)}</textarea>

<label style="display:block;margin-bottom:4px;font-weight:600;color:#374151;font-size:14px">Due Date</label>
<input type="date" name="due_date" value="{_escape_html(due_date)}" style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;margin-bottom:16px;box-sizing:border-box" />

<label style="display:block;margin-bottom:4px;font-weight:600;color:#374151;font-size:14px">Priority</label>
<select name="priority" style="width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;margin-bottom:16px;box-sizing:border-box;background:white">
<option value="low" {"selected" if priority == "low" else ""}>Low</option>
<option value="medium" {"selected" if priority == "medium" else ""}>Medium</option>
<option value="high" {"selected" if priority == "high" else ""}>High</option>
<option value="urgent" {"selected" if priority == "urgent" else ""}>Urgent</option>
</select>

{extra_fields}

</div>

<p style="color:#94a3b8;font-size:12px;text-align:center;margin-bottom:16px">Source: {_escape_html(source_email[:80]) if source_email else 'N/A'}</p>

<div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
<button type="submit" name="action" value="save" style="padding:12px 28px;background:#3b82f6;color:white;border:none;border-radius:8px;font-weight:bold;font-size:15px;cursor:pointer">Save Changes</button>
<button type="submit" name="action" value="save_approve" style="padding:12px 28px;background:#22c55e;color:white;border:none;border-radius:8px;font-weight:bold;font-size:15px;cursor:pointer">Save &amp; Approve</button>
<button type="submit" name="action" value="save_complete" style="padding:12px 28px;background:#8b5cf6;color:white;border:none;border-radius:8px;font-weight:bold;font-size:15px;cursor:pointer">Complete</button>
<a href="/action/reject?token={token}" style="display:inline-block;padding:12px 28px;background:#ef4444;color:white;text-decoration:none;border-radius:8px;font-weight:bold;font-size:15px;text-align:center">Skip</a>
</div>
</form>
</div>
</body></html>'''
    except Exception as e:
        print(f'Error loading action: {e}')
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>{str(e)}</p></div></body></html>', 500


def _escape_html(text):
    """Escape HTML special characters for safe form value insertion"""
    if not text:
        return ''
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#x27;')


@app.route('/action/save', methods=['POST'])
def save_action():
    """Save edited action data and optionally approve"""
    try:
        import json as _json
        from datetime import datetime
        import pytz
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = get_admin_key()  # service-role first, anon fallback
        from supabase import create_client
        sb = create_client(supabase_url, supabase_key)

        token = request.form.get('token')
        submit_action = request.form.get('action', 'save')

        if not token:
            return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>Missing token</p></div></body></html>', 400

        result = sb.table('pending_actions').select('*').eq('token', token).execute()
        if not result.data:
            return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Not Found</h2><p>Action not found</p></div></body></html>', 404

        action_row = result.data[0]
        if action_row['status'] != 'pending':
            return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fef3c7;border-radius:12px;padding:30px"><h2>Already Processed</h2><p>This action was already <strong>{action_row["status"]}</strong>.</p></div></body></html>'

        # Load existing action data and merge edits
        existing_action = _json.loads(action_row['action_data']) if isinstance(action_row['action_data'], str) else action_row['action_data']

        # Update with form values
        existing_action['title'] = request.form.get('title', existing_action.get('title', ''))
        existing_action['customer_name'] = request.form.get('customer_name', existing_action.get('customer_name', ''))
        existing_action['description'] = request.form.get('description', existing_action.get('description', ''))
        existing_action['crm_notes'] = request.form.get('description', existing_action.get('crm_notes', ''))
        existing_action['due_date'] = request.form.get('due_date', existing_action.get('due_date', ''))
        existing_action['priority'] = request.form.get('priority', existing_action.get('priority', 'medium'))

        # Email-specific fields
        if request.form.get('email_to'):
            existing_action['email_to'] = request.form.get('email_to')
            existing_action['recipient'] = request.form.get('email_to')
        if request.form.get('email_body'):
            existing_action['email_body'] = request.form.get('email_body')
            existing_action['body'] = request.form.get('email_body')
        if request.form.get('due_time'):
            existing_action['due_time'] = request.form.get('due_time')

        # Save updated action_data back to DB
        sb.table('pending_actions').update({
            'action_data': _json.dumps(existing_action)
        }).eq('token', token).execute()

        print(f"Action edited: {existing_action.get('title')} (token={token[:8]}...)")

        if submit_action in ('save_approve', 'save_complete'):
            # Create task from edited data
            is_complete = (submit_action == 'save_complete')
            action_type = existing_action.get('action_type', '')
            today_str = datetime.now(pytz.timezone('Australia/Brisbane')).strftime('%Y-%m-%d')
            resolved_user_id, resolved_business_id = _resolve_user_for_action(sb, action_row)
            task_data = {
                'title': existing_action.get('title', 'Unknown action'),
                'description': existing_action.get('description', existing_action.get('crm_notes', '')),
                'status': 'completed' if is_complete else 'pending',
                'created_at': datetime.now(pytz.UTC).isoformat(),
                'due_date': existing_action.get('due_date') or today_str,
                'business_id': resolved_business_id,
                'user_id': resolved_user_id,
                'client_name': existing_action.get('customer_name', ''),
                'priority': existing_action.get('priority', 'medium'),
            }
            if is_complete:
                task_data['completed_at'] = datetime.now(pytz.UTC).isoformat()
            if action_type == 'update_crm':
                task_data['category'] = 'crm'
                task_data['title'] = f"CRM Update: {existing_action.get('customer_name', '')}" if existing_action.get('customer_name') else existing_action.get('title', '')
            elif action_type == 'send_email':
                task_data['category'] = 'email'
            elif action_type == 'create_calendar_event':
                task_data['category'] = 'calendar'
            elif action_type == 'change_deal_status':
                task_data['category'] = 'deals'

            # Try CRM push for update_crm actions
            crm_synced = False
            if action_type == 'update_crm' and not is_complete:
                try:
                    from crm_manager import CRMManager
                    _crm = CRMManager()
                    crm_result = _crm.execute_crm_update(
                        user_id=resolved_user_id,
                        customer_name=existing_action.get('customer_name', ''),
                        crm_notes=existing_action.get('crm_notes', existing_action.get('description', '')),
                        customer_email=existing_action.get('customer_email', ''),
                    )
                    if crm_result.success:
                        crm_synced = True
                        print(f"CRM sync success (save_approve): {crm_result.message}")
                except Exception as crm_err:
                    print(f"CRM sync error in save_action (falling back to task): {crm_err}")

            if crm_synced:
                sb.table('pending_actions').update({
                    'status': 'approved',
                    'crm_synced': True,
                    'crm_synced_at': datetime.now(pytz.UTC).isoformat(),
                    'processed_at': datetime.now(pytz.UTC).isoformat()
                }).eq('token', token).execute()
            else:
                sb.table('tasks').insert(task_data).execute()
                new_status = 'completed' if is_complete else 'approved'
                sb.table('pending_actions').update({
                    'status': new_status,
                    'processed_at': datetime.now(pytz.UTC).isoformat()
                }).eq('token', token).execute()

            action_title = existing_action.get('title', 'Unknown action')
            if is_complete:
                return f'''<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:500px;margin:50px auto;text-align:center;padding:0 16px">
<div style="background:#ede9fe;border-radius:12px;padding:30px;box-shadow:0 1px 3px rgba(0,0,0,0.1)">
<h2 style="color:#5b21b6">Marked Complete</h2>
<p><strong>{_escape_html(action_title)}</strong></p>
<p>Task saved as completed.</p>
<a href="https://www.jottask.app/dashboard" style="display:inline-block;margin-top:16px;padding:10px 24px;background:#8b5cf6;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Dashboard</a>
</div></body></html>'''
            return f'''<html><body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:500px;margin:50px auto;text-align:center;padding:0 16px">
<div style="background:#dcfce7;border-radius:12px;padding:30px;box-shadow:0 1px 3px rgba(0,0,0,0.1)">
<h2 style="color:#166534">Saved &amp; Approved</h2>
<p><strong>{_escape_html(action_title)}</strong></p>
<p>Changes saved and action approved.</p>
<a href="https://www.jottask.app/dashboard" style="display:inline-block;margin-top:16px;padding:10px 24px;background:#22c55e;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Dashboard</a>
</div></body></html>'''
        else:
            # Just save, redirect back to edit page
            from flask import redirect
            return redirect(f'/action/edit?token={token}&saved=1')

    except Exception as e:
        print(f'Error saving action: {e}')
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>{str(e)}</p></div></body></html>', 500


@app.route('/debug/reminders')
def debug_reminders():
    """Diagnostic: show what the scheduler sees and optionally send missed reminders.
    Add ?send=1 to actually send them. Protected by internal API key or logged-in admin."""
    api_key = request.args.get('key', '')
    internal_key = os.getenv('INTERNAL_API_KEY', '')
    user_id = session.get('user_id')
    if api_key != internal_key and not user_id:
        return 'Unauthorized', 401

    from email_utils import send_email as _send_email
    should_send = request.args.get('send') == '1'

    lines = []
    lines.append(f"<h2>Reminder Diagnostics</h2>")
    lines.append(f"<pre>")
    _aest = pytz.timezone('Australia/Brisbane')
    _now_utc = datetime.now(pytz.UTC)
    _now_aest = datetime.now(_aest)
    lines.append(f"Server UTC:  {_now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"AEST local:  {_now_aest.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"AEST date:   {_now_aest.date().isoformat()}")
    lines.append(f"")

    try:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)

        # Users
        users_result = sb.table('users').select('id, email, full_name, timezone').execute()
        users = {u['id']: u for u in (users_result.data or [])}
        lines.append(f"Users: {len(users)}")

        # All pending tasks
        all_tasks = sb.table('tasks') \
            .select('id, title, due_date, due_time, status, client_name, user_id, reminder_sent_at, priority') \
            .eq('status', 'pending') \
            .execute()

        raw = all_tasks.data or []
        with_time = [t for t in raw if t.get('due_time')]
        lines.append(f"Pending tasks total: {len(raw)}")
        lines.append(f"Pending with due_time: {len(with_time)}")
        lines.append(f"")

        from datetime import timedelta as _td
        sent_count = 0

        for t in with_time:
            uid = t.get('user_id')
            user = users.get(uid)
            if not user:
                lines.append(f"TASK {t['id'][:8]}  '{t['title'][:40]}'  -> NO USER FOUND (uid={uid})")
                continue

            user_tz = pytz.timezone(user.get('timezone', 'Australia/Brisbane'))
            now = datetime.now(user_tz)
            today_str = now.date().isoformat()
            yesterday_str = (now.date() - _td(days=1)).isoformat()

            due_date = str(t.get('due_date', ''))[:10]
            due_time = str(t.get('due_time', ''))
            reminder_sent = t.get('reminder_sent_at')

            is_today = (due_date == today_str)
            is_yesterday = (due_date == yesterday_str)

            # For send mode, look back up to 7 days; for display, just today+yesterday
            if should_send:
                try:
                    due_dt = datetime.strptime(due_date, '%Y-%m-%d').date()
                    days_ago = (now.date() - due_dt).days
                    if days_ago < 0 or days_ago > 7:
                        continue
                except:
                    if not is_today and not is_yesterday:
                        continue
            else:
                if not is_today and not is_yesterday:
                    continue

            # Parse time
            parts = due_time.split(':')
            try:
                hour, minute = int(parts[0]), int(parts[1])
            except:
                lines.append(f"TASK {t['id'][:8]}  BAD TIME FORMAT: '{due_time}'")
                continue

            try:
                due_dt = datetime.strptime(due_date, '%Y-%m-%d').date()
                task_due = user_tz.localize(datetime(due_dt.year, due_dt.month, due_dt.day, hour, minute, 0))
            except:
                if is_today:
                    task_due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                else:
                    task_due = (now - _td(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

            diff = (task_due - now).total_seconds() / 60

            # Check already sent — simple: if reminder_sent_at is set, it's done
            already_sent = bool(reminder_sent)

            status_str = "ALREADY SENT" if already_sent else ("IN WINDOW" if diff <= 30 else f"not yet ({diff:.0f}m)")
            if not already_sent and diff < 0:
                status_str = f"OVERDUE {abs(diff):.0f}m - NEEDS SEND"

            lines.append(f"TASK {t['id'][:8]}  '{t['title'][:40]}'  due={due_date} {due_time}  diff={diff:.0f}m  sent={reminder_sent}  -> {status_str}")

            # Actually send if requested
            if should_send and not already_sent and diff < 0:
                display_time = task_due.strftime('%I:%M %p')
                from saas_scheduler import generate_reminder_email_html
                html = generate_reminder_email_html(t, display_time, user.get('full_name', ''), is_overdue=True)
                subject = f"Overdue: {t['title'][:50]} - was due {display_time}"
                ok, err = _send_email(user['email'], subject, html)
                if ok:
                    sb.table('tasks').update({
                        'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
                    }).eq('id', t['id']).execute()
                    lines.append(f"   >>> SENT to {user['email']}")
                    sent_count += 1
                else:
                    lines.append(f"   >>> SEND FAILED: {err}")
                import time as _time
                _time.sleep(1)  # Resend rate limit: 2 req/sec

        if should_send:
            lines.append(f"\nSent {sent_count} reminder(s)")
        else:
            lines.append(f"\nAdd ?send=1 to actually send overdue reminders")

    except Exception as e:
        import traceback
        lines.append(f"ERROR: {e}\n{traceback.format_exc()}")

    lines.append("</pre>")
    return f'<html><body style="font-family:monospace;padding:20px;">{"<br>".join(lines)}</body></html>'


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
