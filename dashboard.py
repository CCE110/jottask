"""
Jottask Dashboard - Main Web Application
Full SaaS task management interface
"""

import os
import json
from flask import Flask, render_template_string, render_template, request, redirect, url_for, session, jsonify, flash
from datetime import datetime, timedelta
import pytz
from functools import wraps
from supabase import create_client, Client

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))

# Register blueprints
from billing import billing_bp
from onboarding import onboarding_bp
from email_setup import email_setup_bp
app.register_blueprint(billing_bp)
app.register_blueprint(onboarding_bp)
app.register_blueprint(email_setup_bp)

# Supabase client
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Shopping list secret token (allows access without login)
SHOPPING_LIST_TOKEN = os.getenv('SHOPPING_LIST_TOKEN', '')
SHOPPING_LIST_USER_EMAIL = os.getenv('SHOPPING_LIST_USER_EMAIL', '')

# Admin notification settings
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'admin@flowquote.ai')
RESEND_API_KEY = os.getenv('RESEND_API_KEY')
FROM_EMAIL = os.getenv('FROM_EMAIL', 'jottask@flowquote.ai')


def send_admin_notification(subject, body_html):
    """Send notification email to admin using Resend"""
    import requests

    if not RESEND_API_KEY:
        print("❌ RESEND_API_KEY not configured for admin notification")
        return False

    try:
        response = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json',
                'User-Agent': 'Jottask/1.0'
            },
            json={
                'from': f'Jottask <{FROM_EMAIL}>',
                'to': [ADMIN_EMAIL],
                'subject': f'[Jottask Admin] {subject}',
                'html': body_html
            },
            timeout=30
        )
        if response.status_code in [200, 201]:
            print(f"✅ Admin notification sent: {subject}")
            return True
        else:
            print(f"❌ Resend error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"❌ Failed to send admin notification: {type(e).__name__}: {e}")
        return False


def send_email(to_email, subject, body_html):
    """Send email using Resend API (using urllib for Railway compatibility)"""
    import urllib.request
    import urllib.error

    if not RESEND_API_KEY:
        print("❌ RESEND_API_KEY not configured")
        return False, "RESEND_API_KEY not configured"

    print(f"📧 Sending to {to_email} via Resend...")

    try:
        data = json.dumps({
            'from': f'Jottask <{FROM_EMAIL}>',
            'to': [to_email],
            'subject': subject,
            'html': body_html
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://api.resend.com/emails',
            data=data,
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json',
                'User-Agent': 'Jottask/1.0'
            },
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            status = response.getcode()
            print(f"📧 Resend response: {status}")
            if status in [200, 201]:
                print(f"✅ Email sent to {to_email}: {subject}")
                return True, None
            else:
                return False, f"Status {status}"

    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8', errors='ignore')
        print(f"❌ Resend HTTP error ({e.code}): {error_msg}")
        return False, error_msg
    except Exception as e:
        print(f"❌ Failed to send email: {type(e).__name__}: {e}")
        return False, str(e)


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

    success, error = send_email(user_email, f"✅ Task Created: {task_title}", html_content)

    if success:
        print(f"✅ Task confirmation email SENT successfully to {user_email}")
    else:
        print(f"❌ Task confirmation email FAILED for {user_email}: {error}")

    return success


# ============================================
# AUTH HELPERS
# ============================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_user_timezone():
    return pytz.timezone(session.get('timezone', 'Australia/Brisbane'))


# ============================================
# SUBSCRIPTION HELPERS
# ============================================

TIER_LIMITS = {
    'free_trial': {'tasks_per_month': 20, 'projects': True},
    'starter': {'tasks_per_month': 100, 'projects': True},
    'pro': {'tasks_per_month': float('inf'), 'projects': True}
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
    today = datetime.now().date()
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
    </script>
</body>
</html>
"""

# ============================================
# AUTH PAGES
# ============================================

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Jottask</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
    <meta name="theme-color" content="#6366F1">
    <style>
        :root {
            --primary: #6366F1;
            --primary-dark: #4F46E5;
            --success: #10B981;
            --gray-50: #F9FAFB;
            --gray-100: #F3F4F6;
            --gray-300: #D1D5DB;
            --gray-500: #6B7280;
            --gray-700: #374151;
            --gray-900: #111827;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
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
        .auth-logo { text-align: center; margin-bottom: 32px; }
        .auth-logo svg { width: 48px; height: 48px; }
        .auth-logo h1 { color: var(--primary); font-size: 24px; margin-top: 12px; }
        .form-group { margin-bottom: 16px; }
        .form-label { display: block; margin-bottom: 6px; font-weight: 500; font-size: 14px; color: var(--gray-700); }
        .form-input {
            width: 100%;
            padding: 10px 14px;
            border: 1px solid var(--gray-300);
            border-radius: 8px;
            font-size: 14px;
        }
        .form-input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1); }
        .btn {
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            border: none;
            text-decoration: none;
        }
        .btn-primary { background: var(--primary); color: white; }
        .btn-primary:hover { background: var(--primary-dark); }
        .alert { padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
        .alert-error { background: #FEE2E2; color: #991B1B; }
    </style>
</head>
<body>
<div class="auth-container">
    <div class="auth-card">
        <div class="auth-logo">
            <svg viewBox="0 0 512 512">
                <defs>
                    <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" style="stop-color:#8B5CF6" />
                        <stop offset="100%" style="stop-color:#6366F1" />
                    </linearGradient>
                </defs>
                <rect width="512" height="512" rx="96" fill="white"/>
                <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
                <line x1="160" y1="150" x2="300" y2="150" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <line x1="160" y1="200" x2="300" y2="200" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <line x1="160" y1="250" x2="260" y2="250" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <circle cx="310" cy="350" r="70" fill="#10B981"/>
                <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <h1>Jottask</h1>
        </div>

        {% if error %}
        <div class="alert alert-error">{{ error }}</div>
        {% endif %}

        <form method="POST">
            <div class="form-group">
                <label class="form-label">Email</label>
                <input type="email" name="email" class="form-input" required autofocus>
            </div>

            <div class="form-group">
                <label class="form-label">Password</label>
                <input type="password" name="password" class="form-input" required>
            </div>

            <button type="submit" class="btn btn-primary" style="width: 100%; margin-top: 8px;">
                Sign In
            </button>
        </form>

        <p style="text-align: center; margin-top: 24px; color: var(--gray-500);">
            Don't have an account? <a href="{{ url_for('signup') }}" style="color: var(--primary);">Sign up</a>
        </p>
    </div>
</div>
</body>
</html>
"""

SIGNUP_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sign Up - Jottask</title>
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
    <meta name="theme-color" content="#6366F1">
    <style>
        :root {
            --primary: #6366F1;
            --primary-dark: #4F46E5;
            --success: #10B981;
            --gray-50: #F9FAFB;
            --gray-100: #F3F4F6;
            --gray-300: #D1D5DB;
            --gray-500: #6B7280;
            --gray-700: #374151;
            --gray-900: #111827;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
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
        .auth-logo { text-align: center; margin-bottom: 32px; }
        .auth-logo svg { width: 48px; height: 48px; }
        .auth-logo h1 { color: var(--primary); font-size: 24px; margin-top: 12px; }
        .form-group { margin-bottom: 16px; }
        .form-label { display: block; margin-bottom: 6px; font-weight: 500; font-size: 14px; color: var(--gray-700); }
        .form-input {
            width: 100%;
            padding: 10px 14px;
            border: 1px solid var(--gray-300);
            border-radius: 8px;
            font-size: 14px;
        }
        .form-input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1); }
        .btn {
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            border: none;
            text-decoration: none;
        }
        .btn-primary { background: var(--primary); color: white; }
        .btn-primary:hover { background: var(--primary-dark); }
        .alert { padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
        .alert-error { background: #FEE2E2; color: #991B1B; }
    </style>
</head>
<body>
<div class="auth-container">
    <div class="auth-card">
        <div class="auth-logo">
            <svg viewBox="0 0 512 512">
                <defs>
                    <linearGradient id="grad3" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" style="stop-color:#8B5CF6" />
                        <stop offset="100%" style="stop-color:#6366F1" />
                    </linearGradient>
                </defs>
                <rect width="512" height="512" rx="96" fill="white"/>
                <rect x="120" y="80" width="220" height="300" rx="24" fill="url(#grad3)"/>
                <line x1="160" y1="150" x2="300" y2="150" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <line x1="160" y1="200" x2="300" y2="200" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <line x1="160" y1="250" x2="260" y2="250" stroke="white" stroke-width="12" stroke-linecap="round" opacity="0.5"/>
                <circle cx="310" cy="350" r="70" fill="#10B981"/>
                <path d="M275 350 L300 375 L355 315" fill="none" stroke="white" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <h1>Jottask</h1>
        </div>

        <p style="text-align: center; color: var(--gray-500); margin-bottom: 24px;">
            Start your 14-day free trial
        </p>

        {% if referral_code %}
        <div style="background: #ECFDF5; border: 1px solid #10B981; padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; text-align: center;">
            <span style="color: #059669; font-weight: 500;">🎁 You were referred! You'll both get $5 credit when you subscribe.</span>
        </div>
        {% endif %}

        {% if error %}
        <div class="alert alert-error">{{ error }}</div>
        {% endif %}

        <form method="POST">
            <input type="hidden" name="referral_code" value="{{ referral_code or '' }}">

            <div class="form-group">
                <label class="form-label">Full Name</label>
                <input type="text" name="full_name" class="form-input" required>
            </div>

            <div class="form-group">
                <label class="form-label">Email</label>
                <input type="email" name="email" class="form-input" required>
            </div>

            <div class="form-group">
                <label class="form-label">Password</label>
                <input type="password" name="password" class="form-input" required minlength="8">
            </div>

            <div class="form-group">
                <label class="form-label">Timezone</label>
                <select name="timezone" class="form-input">
                    <option value="Australia/Brisbane">Australia/Brisbane (AEST)</option>
                    <option value="Australia/Sydney">Australia/Sydney (AEST/AEDT)</option>
                    <option value="Australia/Melbourne">Australia/Melbourne (AEST/AEDT)</option>
                    <option value="Australia/Perth">Australia/Perth (AWST)</option>
                    <option value="Pacific/Auckland">New Zealand (NZST)</option>
                    <option value="America/New_York">US Eastern</option>
                    <option value="America/Los_Angeles">US Pacific</option>
                    <option value="Europe/London">UK (GMT/BST)</option>
                </select>
            </div>

            <button type="submit" class="btn btn-primary" style="width: 100%; margin-top: 8px;">
                Create Account
            </button>
        </form>

        <p style="text-align: center; margin-top: 24px; color: var(--gray-500);">
            Already have an account? <a href="{{ url_for('login') }}" style="color: var(--primary);">Sign in</a>
        </p>
    </div>
</div>
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
    from templates import LANDING_TEMPLATE
    return render_template_string(LANDING_TEMPLATE)


@app.route('/pricing')
def pricing_page():
    from templates import PRICING_TEMPLATE
    from billing import PLANS

    current_plan = 'starter'
    subscription_status = 'none'

    if 'user_id' in session:
        user = supabase.table('users').select('subscription_tier, subscription_status').eq('id', session['user_id']).single().execute()
        if user.data:
            current_plan = user.data.get('subscription_tier', 'starter')
            subscription_status = user.data.get('subscription_status', 'none')

    return render_template_string(
        PRICING_TEMPLATE,
        title='Pricing',
        plans=PLANS,
        current_plan=current_plan,
        subscription_status=subscription_status,
        **{'base': BASE_TEMPLATE}
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
                session['user_id'] = auth_response.user.id
                session['user_email'] = auth_response.user.email

                # Get user profile
                user = supabase.table('users').select('*').eq('id', auth_response.user.id).single().execute()
                if user.data:
                    session['user_name'] = user.data.get('full_name', email.split('@')[0])
                    session['timezone'] = user.data.get('timezone', 'Australia/Brisbane')
                else:
                    session['user_name'] = email.split('@')[0]
                    session['timezone'] = 'Australia/Brisbane'

                return redirect(url_for('dashboard'))
            else:
                return render_template_string(LOGIN_TEMPLATE, error='Invalid credentials')

        except Exception as e:
            error_msg = 'Invalid email or password'
            return render_template_string(LOGIN_TEMPLATE, error=error_msg)

    return render_template_string(LOGIN_TEMPLATE, error=None)


@app.route('/signup', methods=['GET', 'POST'])
@app.route('/r/<referral_code>', methods=['GET'])
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
                new_ref_code = hashlib.md5(f"{auth_response.user.id}jottask{datetime.now().timestamp()}".encode()).hexdigest()[:8].upper()

                user_data = {
                    'id': auth_response.user.id,
                    'email': email,
                    'full_name': full_name,
                    'timezone': timezone,
                    'subscription_status': 'trial',
                    'subscription_tier': 'free_trial',
                    'trial_ends_at': (datetime.now(pytz.UTC) + timedelta(days=14)).isoformat(),
                    'referral_code': new_ref_code,
                    'tasks_this_month': 0,
                    'tasks_month_reset': datetime.now().date().replace(day=1).isoformat()
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
                    <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
                    <hr>
                    <p><a href="https://www.jottask.app/admin">View Admin Dashboard</a></p>
                    """
                )

                return redirect(url_for('dashboard'))

        except Exception as e:
            error_msg = str(e)
            if 'already registered' in error_msg.lower():
                error_msg = 'Email already registered'
            return render_template_string(SIGNUP_TEMPLATE, error=error_msg, referral_code=ref_code)

    return render_template_string(SIGNUP_TEMPLATE, error=None, referral_code=ref_code)


@app.route('/logout')
def logout():
    try:
        supabase.auth.sign_out()
    except:
        pass
    session.clear()
    return redirect(url_for('login'))


@app.route('/debug-tasks')
@login_required
def debug_tasks():
    """Temporary debug endpoint to check task user_id mapping (requires login)"""
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
    <p><strong>Expected user_id:</strong> e515407e-dbd6-4331-a815-1878815c89bc</p>
    <p><strong>Match:</strong> {'YES' if str(user_id) == 'e515407e-dbd6-4331-a815-1878815c89bc' else 'NO - MISMATCH!'}</p>
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
def debug_db():
    """Public debug endpoint to check database state (REMOVE IN PRODUCTION)"""
    expected_user_id = 'e515407e-dbd6-4331-a815-1878815c89bc'

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

    # Get recent completed tasks (last 50)
    completed_tasks_result = supabase.table('tasks')\
        .select('*')\
        .eq('user_id', user_id)\
        .eq('status', 'completed')\
        .order('completed_at', desc=True)\
        .limit(50)\
        .execute()

    # Combine: pending/ongoing first, then recent completed
    pending_list = pending_tasks_result.data or []
    completed_list = completed_tasks_result.data or []
    all_tasks = pending_list + completed_list

    # Filter by search query if provided
    if search_query:
        search_lower = search_query.lower()
        all_tasks = [t for t in all_tasks if
            search_lower in (t.get('title') or '').lower() or
            search_lower in (t.get('description') or '').lower() or
            search_lower in (t.get('client_name') or '').lower()
        ]
        pending_list = [t for t in pending_list if
            search_lower in (t.get('title') or '').lower() or
            search_lower in (t.get('description') or '').lower() or
            search_lower in (t.get('client_name') or '').lower()
        ]

    # Calculate stats (from unfiltered data)
    all_pending = pending_tasks_result.data or []
    stats = {
        'pending': len(all_pending),
        'due_today': len([t for t in all_pending if t.get('due_date') == today]),
        'overdue': len([t for t in all_pending if t.get('due_date') and t['due_date'] < today]),
        'completed_this_week': len([t for t in (completed_tasks_result.data or []) if t.get('completed_at')])
    }

    return render_template(
        'dashboard.html',
        title='Dashboard',
        tasks=all_tasks,
        stats=stats,
        today=today,
        search_query=search_query
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

    return redirect(url_for('dashboard'))


@app.route('/tasks/<task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
    from templates import TASK_EDIT_TEMPLATE
    user_id = session['user_id']

    # Verify ownership
    task = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user_id).single().execute()
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

    return render_template_string(
        TASK_EDIT_TEMPLATE,
        title='Edit Task',
        task=task.data,
        checklist=checklist.data or [],
        **{'base': BASE_TEMPLATE}
    )


@app.route('/tasks/<task_id>')
@login_required
def task_detail(task_id):
    from templates import TASK_DETAIL_TEMPLATE
    user_id = session['user_id']

    # Get task
    task = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user_id).single().execute()
    if not task.data:
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

    return render_template_string(
        TASK_DETAIL_TEMPLATE,
        title=task.data['title'],
        task=task.data,
        checklist=checklist.data or [],
        notes=notes.data or [],
        **{'base': BASE_TEMPLATE}
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
    print(f"[SL] Token check: got='{token}', expected='{SHOPPING_LIST_TOKEN}', email='{SHOPPING_LIST_USER_EMAIL}'")
    if not SHOPPING_LIST_TOKEN or token != SHOPPING_LIST_TOKEN:
        print(f"[SL] Token mismatch")
        return None
    user_id = _get_shopping_list_user_id()
    print(f"[SL] User lookup result: {user_id}")
    return user_id


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

    return render_template(
        'settings.html',
        title='Settings',
        user=user.data,
        subscription=sub,
        referral_count=referral_count,
        converted_count=converted_count,
        referral_link=f"https://www.jottask.app/r/{sub.get('referral_code', '')}",
        message=request.args.get('message')
    )


@app.route('/settings/profile', methods=['POST'])
@login_required
def update_profile():
    user_id = session['user_id']

    # Parse alternate emails from comma-separated string
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


@app.route('/billing')
@login_required
def billing():
    # Placeholder for Stripe billing portal
    return redirect(url_for('settings'))


# ============================================
# ACTION ROUTE (Email Button Handler)
# ============================================

@app.route('/action')
def handle_action():
    """Handle action button clicks from emails - redirects to appropriate pages"""
    action = request.args.get('action')
    project_id = request.args.get('project_id')
    task_id = request.args.get('task_id')

    print(f"🎯 ACTION ROUTE HIT: action={action}, task_id={task_id}, project_id={project_id}")

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

            elif action == 'delay_1hour':
                current_time = task_data.get('due_time', '09:00:00')
                try:
                    parts = current_time.split(':')
                    hour = int(parts[0]) + 1
                    if hour >= 24:
                        hour = 23
                    new_time = f"{hour:02d}:{parts[1]}:00"
                except:
                    new_time = '10:00:00'

                supabase.table('tasks').update({
                    'due_time': new_time,
                    'reminder_sent_at': None
                }).eq('id', task_id).execute()

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
                    new_date = (datetime.now() + timedelta(days=1)).date().isoformat()

                supabase.table('tasks').update({
                    'due_date': new_date,
                    'reminder_sent_at': None
                }).eq('id', task_id).execute()

                return render_template_string("""
                <html><head><title>Task Delayed</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #eff6ff;">
                    <h2 style="color: #6366F1;">📅 Task Delayed +1 Day</h2>
                    <p><strong>{{ title }}</strong></p>
                    <p>New date: {{ new_date }}</p>
                    <a href="https://www.jottask.app/dashboard" style="color: #6366F1;">Open Dashboard</a>
                </body></html>
                """, title=task_title, new_date=new_date)

            elif action == 'delay_custom' or action == 'reschedule':
                # Show reschedule form with full edit capability
                current_date = task_data.get('due_date', datetime.now().date().isoformat())
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
                'reminder_sent_at': None
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
            'reminder_sent_at': None,
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


@app.route('/api/tasks/<task_id>/delay', methods=['POST'])
@login_required
def api_delay_task(task_id):
    user_id = session['user_id']
    data = request.get_json()
    hours = data.get('hours', 0)
    days = data.get('days', 0)

    # Verify ownership and get task
    task = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user_id).single().execute()
    if not task.data:
        return jsonify({'error': 'Not found'}), 404

    tz = get_user_timezone()
    now = datetime.now(tz)
    new_dt = now + timedelta(hours=hours, days=days)

    supabase.table('tasks').update({
        'due_date': new_dt.date().isoformat(),
        'due_time': new_dt.strftime('%H:%M:%S'),
        'status': 'pending'
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
        # Delay task by 1 hour
        current_time = task_data.get('due_time', '09:00:00')
        try:
            parts = current_time.split(':')
            hour = int(parts[0]) + 1
            if hour >= 24:
                hour = 23
            new_time = f"{hour:02d}:{parts[1]}:00"
        except:
            new_time = '10:00:00'

        supabase.table('tasks').update({
            'due_time': new_time,
            'reminder_sent_at': None  # Allow new reminder
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
            new_date = (datetime.now() + timedelta(days=1)).date().isoformat()

        supabase.table('tasks').update({
            'due_date': new_date,
            'reminder_sent_at': None  # Allow new reminder
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
            <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
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
# ADMIN DASHBOARD
# ============================================

def admin_required(f):
    """Decorator to require admin access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        # Check if user is admin (your user ID)
        admin_id = 'e515407e-dbd6-4331-a815-1878815c89bc'
        if session['user_id'] != admin_id:
            return "Access denied", 403
        return f(*args, **kwargs)
    return decorated_function


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


# ============================================
# MAIN
# ============================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
