"""
Jottask SaaS Scheduler
Daily summary emails, task reminders, and scheduled tasks
"""

import os
import time
from datetime import datetime, timedelta
import pytz
from supabase import create_client, Client
from email_utils import send_email

# Lazy Supabase init — avoids crash if env vars aren't loaded at import time
_supabase = None

def _get_supabase():
    global _supabase
    if _supabase is None:
        url = os.getenv('SUPABASE_URL')
        key = os.getenv('SUPABASE_KEY')
        _supabase = create_client(url, key)
    return _supabase


def _retry_supabase(fn, attempts=3, delay=2, label='supabase call'):
    """Retry a Supabase call up to `attempts` times with `delay` seconds
    between tries. Targets transient 502/JSON/gateway failures that have
    killed the daily-summary loop mid-run in the past. Re-raises on final
    failure so the caller can decide what to do.
    """
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if i < attempts - 1:
                print(f"   ⚠️ {label} attempt {i+1} failed ({e}); retry in {delay}s")
                time.sleep(delay)
            else:
                print(f"   ❌ {label} failed after {attempts} attempts: {e}")
    raise last_exc


def get_users_needing_summary():
    """Get users who need their daily summary sent now"""
    users_to_notify = []

    # Get all users with daily summary enabled
    result = _get_supabase().table('users').select(
        'id, email, full_name, timezone, daily_summary_enabled, daily_summary_time, last_summary_sent_at, alternate_emails'
    ).eq('daily_summary_enabled', True).execute()

    for user in (result.data or []):
        user_tz = pytz.timezone(user.get('timezone', 'Australia/Brisbane'))
        now_user = datetime.now(user_tz)

        # Get configured summary time (default 8 AM)
        summary_time_str = user.get('daily_summary_time', '08:00:00')
        if summary_time_str:
            try:
                summary_hour = int(summary_time_str.split(':')[0])
                summary_minute = int(summary_time_str.split(':')[1])
            except:
                summary_hour = 8
                summary_minute = 0
        else:
            summary_hour = 8
            summary_minute = 0

        # Check if it's time to send (within 5 minute window)
        if now_user.hour == summary_hour and now_user.minute < 5:
            # Check if we already sent today
            last_sent = user.get('last_summary_sent_at')
            if last_sent:
                try:
                    last_sent_dt = datetime.fromisoformat(last_sent.replace('Z', '+00:00'))
                    last_sent_local = last_sent_dt.astimezone(user_tz)
                    if last_sent_local.date() == now_user.date():
                        # Already sent today
                        continue
                except:
                    pass

            users_to_notify.append(user)

    return users_to_notify


def get_user_tasks_summary(user_id, user_timezone):
    """Get task summary for a user covering the actionable window.

    Includes every pending task due today or earlier (overdue), today, or in
    the next 7 days — across all categories, DSW Solar leads included.
    """
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)
    today_iso = now.date().isoformat()
    week_ahead_iso = (now.date() + timedelta(days=7)).isoformat()

    tasks = _retry_supabase(
        lambda: _get_supabase().table('tasks')
            .select('id, title, due_date, due_time, priority, status, client_name, category')
            .eq('user_id', user_id)
            .eq('status', 'pending')
            .not_.is_('due_date', 'null')
            .lte('due_date', week_ahead_iso)
            .order('due_date')
            .order('due_time')
            .limit(500)
            .execute(),
        label=f'tasks.select for user {user_id[:8]}',
    )

    all_tasks = tasks.data or []

    overdue, due_today, upcoming = [], [], []
    for task in all_tasks:
        d = str(task.get('due_date') or '')[:10]
        if not d:
            continue
        if d < today_iso:
            overdue.append(task)
        elif d == today_iso:
            due_today.append(task)
        else:
            upcoming.append(task)

    return {
        'overdue': overdue,
        'due_today': due_today,
        'upcoming': upcoming,
        'total_pending': len(all_tasks),
    }


def _group_tasks_by_category(tasks):
    """Bucket a list of tasks by category, preserving input order within each bucket."""
    groups = {}
    for t in tasks:
        cat = t.get('category') or 'Uncategorized'
        groups.setdefault(cat, []).append(t)
    # DSW Solar first (it's Rob's hottest pipeline), then alphabetical
    ordered = {}
    if 'DSW Solar' in groups:
        ordered['DSW Solar'] = groups.pop('DSW Solar')
    for k in sorted(groups):
        ordered[k] = groups[k]
    return ordered


def get_user_projects_summary(user_id):
    """Get project summary for a user"""
    # Get active projects
    projects = _retry_supabase(
        lambda: _get_supabase().table('saas_projects')
            .select('id, name, color, status')
            .eq('user_id', user_id)
            .eq('status', 'active')
            .order('created_at', desc=True)
            .limit(10)
            .execute(),
        label=f'saas_projects.select for user {user_id[:8]}',
    )

    projects_with_progress = []
    total_items_remaining = 0

    for project in (projects.data or []):
        # Get items for this project
        items = _retry_supabase(
            lambda p=project: _get_supabase().table('saas_project_items')
                .select('id, is_completed')
                .eq('project_id', p['id'])
                .execute(),
            label=f'saas_project_items.select project {project["id"][:8]}',
        )

        items_list = items.data or []
        total = len(items_list)
        completed = len([i for i in items_list if i['is_completed']])
        remaining = total - completed
        progress = int((completed / total * 100)) if total else 0

        total_items_remaining += remaining

        projects_with_progress.append({
            'name': project['name'],
            'color': project.get('color', '#6366F1'),
            'total': total,
            'completed': completed,
            'remaining': remaining,
            'progress': progress
        })

    return {
        'projects': projects_with_progress,
        'total_items_remaining': total_items_remaining,
        'active_count': len(projects_with_progress)
    }


def generate_summary_email_html(user_name, user_timezone, tasks_summary, projects_summary):
    """Generate the HTML content for the daily summary email"""
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)
    date_str = now.strftime('%A, %B %d, %Y')

    greeting = f"Good morning, {user_name}!" if user_name else "Good morning!"

    def _render_section(heading, heading_color, bg_color, title_color, meta_color, tasks):
        if not tasks:
            return ''
        html = (
            '<div style="margin-bottom:24px;">'
            f'<h3 style="color:{heading_color};font-size:14px;margin-bottom:12px;">'
            f'{heading} ({len(tasks)})</h3>'
        )
        for cat, items in _group_tasks_by_category(tasks).items():
            html += (
                f'<div style="font-size:11px;font-weight:700;letter-spacing:0.5px;'
                f'color:#6B7280;margin:10px 0 6px;">{cat.upper()} — {len(items)}</div>'
            )
            for task in items:
                due_time = (task.get('due_time') or '')[:5]
                meta_bits = [f"Due: {task.get('due_date') or 'N/A'}"]
                if due_time: meta_bits.append(due_time)
                if task.get('client_name'): meta_bits.append(task['client_name'])
                html += (
                    f'<div style="padding:10px 12px;background:{bg_color};'
                    f'border-radius:8px;margin-bottom:6px;">'
                    f'<strong style="color:{title_color};">{task["title"]}</strong>'
                    f'<div style="font-size:12px;color:{meta_color};">'
                    f'{" · ".join(meta_bits)}</div></div>'
                )
        html += '</div>'
        return html

    tasks_html = ''
    tasks_html += _render_section('OVERDUE',   '#EF4444', '#FEE2E2', '#991B1B', '#B91C1C',
                                   tasks_summary['overdue'])
    tasks_html += _render_section('DUE TODAY', '#6366F1', '#EEF2FF', '#4338CA', '#6366F1',
                                   tasks_summary['due_today'])
    tasks_html += _render_section('NEXT 7 DAYS', '#6B7280', '#F3F4F6', '#374151', '#6B7280',
                                   tasks_summary['upcoming'])

    if not tasks_html:
        tasks_html = (
            '<div style="text-align:center;padding:24px;color:#6B7280;">'
            "<p>No tasks overdue, due today, or in the next 7 days. You're all caught up!</p>"
            '</div>'
        )

    # Build projects section
    projects_html = ""
    if projects_summary['projects']:
        projects_html = """
        <div style="margin-top: 32px; padding-top: 24px; border-top: 1px solid #E5E7EB;">
            <h2 style="font-size: 18px; margin-bottom: 16px; color: #374151;">Active Projects</h2>
        """
        for project in projects_summary['projects']:
            projects_html += f"""
            <div style="margin-bottom: 16px;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                    <span style="font-weight: 500; color: #374151;">
                        <span style="display: inline-block; width: 12px; height: 12px; border-radius: 3px; background: {project['color']}; margin-right: 8px;"></span>
                        {project['name']}
                    </span>
                    <span style="color: #6B7280; font-size: 14px;">{project['completed']}/{project['total']} ({project['progress']}%)</span>
                </div>
                <div style="height: 8px; background: #E5E7EB; border-radius: 4px; overflow: hidden;">
                    <div style="height: 100%; background: #10B981; width: {project['progress']}%;"></div>
                </div>
                <div style="font-size: 12px; color: #9CA3AF; margin-top: 4px;">{project['remaining']} items remaining</div>
            </div>
            """
        projects_html += "</div>"

    # Stats summary
    stats_html = f"""
    <div style="display: flex; gap: 16px; margin-bottom: 24px;">
        <div style="flex: 1; background: #F3F4F6; padding: 16px; border-radius: 8px; text-align: center;">
            <div style="font-size: 28px; font-weight: 700; color: #374151;">{tasks_summary['total_pending']}</div>
            <div style="font-size: 12px; color: #6B7280;">Pending Tasks</div>
        </div>
        <div style="flex: 1; background: #F3F4F6; padding: 16px; border-radius: 8px; text-align: center;">
            <div style="font-size: 28px; font-weight: 700; color: #EF4444;">{len(tasks_summary['overdue'])}</div>
            <div style="font-size: 12px; color: #6B7280;">Overdue</div>
        </div>
        <div style="flex: 1; background: #F3F4F6; padding: 16px; border-radius: 8px; text-align: center;">
            <div style="font-size: 28px; font-weight: 700; color: #6366F1;">{projects_summary['active_count']}</div>
            <div style="font-size: 12px; color: #6B7280;">Active Projects</div>
        </div>
    </div>
    """

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #F9FAFB;">
        <div style="background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%); padding: 32px; border-radius: 16px 16px 0 0;">
            <h1 style="color: white; margin: 0 0 8px 0; font-size: 28px;">Daily Summary</h1>
            <p style="color: rgba(255,255,255,0.9); margin: 0; font-size: 16px;">{date_str}</p>
        </div>

        <div style="background: white; padding: 32px; border-radius: 0 0 16px 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <p style="color: #374151; font-size: 16px; margin-bottom: 24px;">{greeting}</p>

            {stats_html}

            <h2 style="font-size: 18px; margin-bottom: 16px; color: #374151;">Your Tasks</h2>

            {tasks_html}

            {projects_html}

            <div style="margin-top: 32px; text-align: center;">
                <a href="https://www.jottask.app/dashboard" style="display: inline-block; background: #6366F1; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600;">Open Dashboard</a>
            </div>
        </div>

        <p style="color: #9CA3AF; font-size: 12px; text-align: center; margin-top: 24px;">
            Jottask - AI-Powered Task Management<br>
            <a href="https://jottask.flowquote.ai/settings" style="color: #6B7280;">Manage notification preferences</a>
        </p>
    </body>
    </html>
    """

    return html


ROB_USER_ID = 'e515407e-dbd6-4331-a815-1878815c89bc'
ROB_OUTBOUND_EMAIL = 'rob.l@directsolarwholesaler.com.au'


def _resolve_recipient(user):
    """Rob's outbound always goes to his DSW inbox, not whatever users.email holds."""
    if user and user.get('id') == ROB_USER_ID:
        return ROB_OUTBOUND_EMAIL
    return user['email'] if user else None


def send_daily_summary(user):
    """Send daily summary email to a user"""
    user_id = user['id']
    user_email = _resolve_recipient(user)
    user_name = user.get('full_name')
    user_timezone = user.get('timezone', 'Australia/Brisbane')

    print(f"  📧 Sending daily summary to {user_email}...")

    # Get summaries
    tasks_summary = get_user_tasks_summary(user_id, user_timezone)
    projects_summary = get_user_projects_summary(user_id)

    # Skip if nothing to report
    if tasks_summary['total_pending'] == 0 and projects_summary['active_count'] == 0:
        print(f"    ⏭️ No tasks or projects, skipping email")
        # Still update last_summary_sent_at
        _retry_supabase(
            lambda: _get_supabase().table('users').update({
                'last_summary_sent_at': datetime.now(pytz.UTC).isoformat()
            }).eq('id', user_id).execute(),
            label=f'users.update (skip) for {user_id[:8]}',
        )
        return

    # Generate email
    html_content = generate_summary_email_html(
        user_name,
        user_timezone,
        tasks_summary,
        projects_summary
    )

    # Send email to primary email only
    subject = f"Your Daily Summary - {datetime.now(pytz.timezone(user_timezone)).strftime('%b %d')}"
    success, error = send_email(user_email, subject, html_content, category='summary', user_id=user_id)

    if success:
        # Update last_summary_sent_at
        _retry_supabase(
            lambda: _get_supabase().table('users').update({
                'last_summary_sent_at': datetime.now(pytz.UTC).isoformat()
            }).eq('id', user_id).execute(),
            label=f'users.update (sent) for {user_id[:8]}',
        )


ACTION_URL = os.getenv('TASK_ACTION_URL', 'https://www.jottask.app/action')


def generate_reminder_email_html(task, due_time_str, user_name, is_overdue=False):
    """Generate HTML for a task reminder email"""
    title = task.get('title', 'Untitled Task')
    client_name = task.get('client_name', '')
    priority = task.get('priority', 'medium')
    task_id = task['id']

    priority_colors = {
        'urgent': '#DC2626',
        'high': '#F59E0B',
        'medium': '#6366F1',
        'low': '#6B7280',
    }
    color = priority_colors.get(priority, '#6366F1')
    if is_overdue:
        color = '#DC2626'  # Red for overdue

    client_line = f'<div style="font-size: 14px; color: #6B7280;">Client: {client_name}</div>' if client_name else ''

    if is_overdue:
        heading = 'Overdue Task'
        # Handle "today"/"yesterday" for date-only tasks vs time strings
        if due_time_str in ('today', 'yesterday'):
            subtext = f'Was due {due_time_str}'
        else:
            subtext = f'Was due at {due_time_str}'
    else:
        heading = 'Task Reminder'
        if due_time_str in ('today', 'yesterday'):
            subtext = f'Due {due_time_str}'
        else:
            subtext = f'Due at {due_time_str}'

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #F9FAFB;">
        <div style="background: linear-gradient(135deg, {color} 0%, {color}CC 100%); padding: 24px 32px; border-radius: 16px 16px 0 0;">
            <h1 style="color: white; margin: 0 0 4px 0; font-size: 22px;">{heading}</h1>
            <p style="color: rgba(255,255,255,0.9); margin: 0; font-size: 14px;">{subtext}</p>
        </div>

        <div style="background: white; padding: 32px; border-radius: 0 0 16px 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <a href="https://www.jottask.app/tasks/{task_id}/edit" style="color: #111827; text-decoration: none;"><h2 style="color: #111827; margin: 0 0 8px 0; font-size: 20px;">{title}</h2></a>
            {client_line}

            <div style="margin-top: 24px; display: flex; gap: 10px; flex-wrap: wrap;">
                <a href="{ACTION_URL}?action=complete&task_id={task_id}"
                   style="display: inline-block; padding: 12px 24px; background: #10B981; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    Complete
                </a>
                <a href="{ACTION_URL}?action=delay_1hour&task_id={task_id}"
                   style="display: inline-block; padding: 12px 24px; background: #6B7280; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    +1 Hour
                </a>
                <a href="{ACTION_URL}?action=delay_1day&task_id={task_id}"
                   style="display: inline-block; padding: 12px 24px; background: #6B7280; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    +1 Day
                </a>
                <a href="{ACTION_URL}?action=delay_next_day_8am&task_id={task_id}"
                   style="display: inline-block; padding: 12px 24px; background: #0EA5E9; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    🌅 Tmrw 8am
                </a>
                <a href="{ACTION_URL}?action=delay_next_day_9am&task_id={task_id}"
                   style="display: inline-block; padding: 12px 24px; background: #0EA5E9; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    ☀️ Tmrw 9am
                </a>
                <a href="{ACTION_URL}?action=delay_next_monday_9am&task_id={task_id}"
                   style="display: inline-block; padding: 12px 24px; background: #F59E0B; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    📆 Mon 9am
                </a>
                <a href="{ACTION_URL}?action=delay_custom&task_id={task_id}"
                   style="display: inline-block; padding: 12px 24px; background: #7C3AED; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px;">
                    Reschedule
                </a>
            </div>

            <div style="margin-top: 24px; text-align: center;">
                <a href="https://www.jottask.app/dashboard" style="color: #6366F1; font-size: 13px;">Open Dashboard</a>
            </div>
        </div>

        <p style="color: #9CA3AF; font-size: 12px; text-align: center; margin-top: 24px;">
            Jottask - AI-Powered Task Management
        </p>
    </body>
    </html>
    """
    return html


def check_and_send_reminders():
    """Bulletproof reminder system. Design principles:

    1. ONE simple query: get ALL pending tasks. No complex filters that can miss tasks.
       With <1000 tasks per user this is fast and eliminates query bugs.
    2. Send first, mark after. Never set reminder_sent_at before confirming email sent.
       Worst case = duplicate reminder (acceptable). Never = missed reminder (unacceptable).
    3. Every exception is caught per-task. One bad task never kills the loop.
    4. Two-tier throttle:
       - 4h floor on every task (catches reschedule bounces, short windows).
         Reschedule endpoints stamp reminder_sent_at=now so this catches the
         click and suppresses a near-term re-fire against the new due_time.
       - 24h ceiling on overdue tasks — once an overdue re-reminder fires,
         back off for a full day. Stops the "pinged every 4h forever" effect
         on tasks Rob hasn't completed yet.

    When a task needs a reminder:
    - Has due_time → remind when within reminder_window minutes, or overdue
    - No due_time → remind after morning_hour on due_date, or anytime if overdue
    - Re-remind overdue tasks once per day (morning) until completed
    """
    print(f"\n🔔 Checking task reminders...")

    try:
        # ── Step 1: Load all users ──
        users_result = _get_supabase().table('users').select(
            'id, email, full_name, timezone, reminder_minutes_before, daily_summary_time'
        ).execute()
        users = {u['id']: u for u in (users_result.data or [])}

        if not users:
            print("   No users found")
            return 0

        # ── Step 2: ONE query — get ALL pending tasks due within 14 days ──
        # No reminder_sent_at filter. No complex date logic. Just get everything.
        aest = pytz.timezone('Australia/Brisbane')
        now_aest = datetime.now(aest)
        fourteen_days_ago = (now_aest - timedelta(days=14)).strftime('%Y-%m-%d')
        tomorrow_str = (now_aest + timedelta(days=1)).strftime('%Y-%m-%d')

        # DSW Solar tasks still at lead_status='new_lead' are owned by
        # check_and_send_dsw_reminders (24h + 3d cadence). Everything else
        # — including DSW Solar tasks that have progressed past new_lead
        # and therefore have a real due_time worth reminding on — goes
        # through this loop.
        all_tasks_result = _get_supabase().table('tasks')\
            .select('id, title, description, due_date, due_time, priority, status, client_name, user_id, reminder_sent_at, category, lead_status')\
            .eq('status', 'pending')\
            .gte('due_date', fourteen_days_ago)\
            .lte('due_date', tomorrow_str)\
            .order('due_date')\
            .order('due_time')\
            .execute()

        all_tasks = all_tasks_result.data or []
        print(f"   {len(all_tasks)} pending tasks (due {fourteen_days_ago}..{tomorrow_str}), {len(users)} user(s)")

        sent_count = 0
        skipped_future = 0
        skipped_throttle = 0
        skipped_overdue_throttle = 0
        already_reminded_today = 0
        # 4h floor: never re-fire a reminder within 4 hours of the last one.
        # Catches both the "immediately after reschedule" case (we stamp
        # reminder_sent_at=now on button click) and generic spam prevention.
        four_hours_ago = datetime.now(pytz.UTC) - timedelta(hours=4)
        # 24h ceiling for overdue re-reminders — once the task is overdue and
        # we've pinged about it, back off for a day before pinging again.
        twenty_four_hours_ago = datetime.now(pytz.UTC) - timedelta(hours=24)

        # ── Step 3: Check each task ──
        for task in all_tasks:
            try:
                task_id = task['id']
                user_id = task.get('user_id')
                if not user_id or user_id not in users:
                    continue

                # Don't double-fire on DSW leads still at new_lead — those are
                # handled by check_and_send_dsw_reminders (24h + 3d cadence).
                if task.get('category') == 'DSW Solar' and \
                   (task.get('lead_status') or 'new_lead') == 'new_lead':
                    continue

                user = users[user_id]

                # Parse user timezone safely
                try:
                    user_tz = pytz.timezone(user.get('timezone') or 'Australia/Brisbane')
                except Exception:
                    user_tz = pytz.timezone('Australia/Brisbane')
                now = datetime.now(user_tz)

                # ── Throttle check: was this task reminded in the last 4h? ──
                last_reminded = task.get('reminder_sent_at')
                if last_reminded:
                    try:
                        last_dt = datetime.fromisoformat(last_reminded.replace('Z', '+00:00'))
                        if last_dt > four_hours_ago:
                            skipped_throttle += 1
                            continue  # Reminded less than 4 hours ago — skip
                    except Exception:
                        pass  # Can't parse timestamp — proceed with reminder

                # ── Determine if this task needs a reminder RIGHT NOW ──
                task_due_date = str(task.get('due_date', ''))[:10]
                if not task_due_date:
                    continue

                reminder_window = user.get('reminder_minutes_before') or 30
                needs_reminder = False
                is_overdue = False
                display_time = ''

                if task.get('due_time'):
                    # ── Task WITH specific time ──
                    due_time_str = str(task['due_time'])
                    parts = due_time_str.split(':')
                    try:
                        hour = int(parts[0])
                        minute = int(parts[1]) if len(parts) > 1 else 0
                    except (ValueError, IndexError):
                        hour, minute = 9, 0  # Fallback — don't skip, use 9am

                    try:
                        due_dt = datetime.strptime(task_due_date, '%Y-%m-%d').date()
                        task_due = user_tz.localize(datetime(due_dt.year, due_dt.month, due_dt.day, hour, minute, 0))
                    except Exception:
                        continue

                    minutes_until = (task_due - now).total_seconds() / 60
                    display_time = task_due.strftime('%I:%M %p')

                    if minutes_until <= reminder_window:
                        # Within reminder window OR overdue
                        needs_reminder = True
                        is_overdue = minutes_until < 0
                    else:
                        skipped_future += 1

                else:
                    # ── Task with date only (no time) ──
                    local_today = now.date().isoformat()

                    # Morning reminder hour
                    morning_hour = 8
                    try:
                        st = user.get('daily_summary_time')
                        if st:
                            morning_hour = int(str(st).split(':')[0])
                    except Exception:
                        pass

                    if task_due_date > local_today:
                        skipped_future += 1  # Future — skip
                    elif task_due_date == local_today:
                        if now.hour >= morning_hour:
                            needs_reminder = True
                            is_overdue = False
                            display_time = 'today'
                        else:
                            skipped_future += 1  # Before morning hour
                    else:
                        needs_reminder = True
                        is_overdue = True
                        days_overdue = (now.date() - datetime.strptime(task_due_date, '%Y-%m-%d').date()).days
                        display_time = 'yesterday' if days_overdue == 1 else f'{days_overdue}d overdue'

                if not needs_reminder:
                    continue

                # ── 24h ceiling on overdue re-reminders ──
                # Overdue tasks used to ping every 4h forever once they fell
                # past due. Cap at one overdue re-reminder per 24h.
                if last_reminded and is_overdue:
                    try:
                        last_dt = datetime.fromisoformat(last_reminded.replace('Z', '+00:00'))
                        if last_dt > twenty_four_hours_ago:
                            skipped_overdue_throttle += 1
                            continue
                    except Exception:
                        pass

                # ── Check if already reminded today (first-time only, not re-reminders) ──
                if last_reminded and not is_overdue:
                    # Already got a first reminder and not overdue — don't spam
                    already_reminded_today += 1
                    continue

                # ── Build and send the email ──
                recipient = _resolve_recipient(user)
                if is_overdue:
                    subject = f"Overdue: {task['title'][:50]} - was due {display_time}"
                    print(f"   📨 OVERDUE: '{task['title'][:45]}' -> {recipient}")
                else:
                    subject = f"Reminder: {task['title'][:50]} - due {display_time}"
                    print(f"   📨 Reminder: '{task['title'][:45]}' -> {recipient}")

                # ── SEND FIRST, then mark ──
                # If send fails → reminder_sent_at stays old → next tick retries. Good.
                # If send succeeds but DB update fails → duplicate next tick. Acceptable.
                if task.get('category') == 'DSW Solar':
                    # Refresh from PipeReply and render with the full DSW lead template
                    # so Rob sees current CRM data, current lead-status badge, and the
                    # full action/status button set — not the plain reminder layout.
                    from dsw_lead_poller import send_dsw_reminder_for_task
                    tag = 'overdue' if is_overdue else display_time
                    try:
                        success, error = send_dsw_reminder_for_task(task, tag)
                    except Exception as e:
                        success, error = False, str(e)
                else:
                    html_content = generate_reminder_email_html(
                        task, display_time, user.get('full_name', ''), is_overdue=is_overdue
                    )
                    success, error = send_email(recipient, subject, html_content,
                                               category='reminder', user_id=user_id, task_id=task_id)

                if success:
                    # Mark as reminded AFTER confirmed send
                    try:
                        _get_supabase().table('tasks').update({
                            'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
                        }).eq('id', task_id).execute()
                    except Exception as db_err:
                        # DB update failed — worst case is a duplicate reminder next tick
                        print(f"   ⚠️ Sent OK but DB update failed for {task_id[:8]}: {db_err}")
                    sent_count += 1
                    time.sleep(0.3)
                else:
                    print(f"   ❌ Send failed for '{task['title'][:30]}': {error}")
                    # Don't touch reminder_sent_at — next tick will retry

            except Exception as task_err:
                # Per-task exception — log and continue, NEVER kill the loop
                print(f"   ❌ Error processing task {task.get('id', '?')[:8]}: {task_err}")
                continue

        # ── Summary ──
        print(f"   Reminders: {len(all_tasks)} checked, {sent_count} sent, "
              f"{skipped_future} future, {skipped_throttle} throttled (<4h), "
              f"{skipped_overdue_throttle} overdue-throttled (<24h), "
              f"{already_reminded_today} already reminded")
        return sent_count

    except Exception as e:
        print(f"   ❌ REMINDER SYSTEM ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 0


def check_and_send_dsw_reminders():
    """Reminders for DSW Solar leads.

    Two reminder stages per lead, both gated on lead_status still being 'new_lead':
      - 24h: sent once task is 24h+ old and no prior reminder
      - 3d:  sent once task is 72h+ old (skips 24h stage if discovered late)

    Progression is tracked with tasks.reminder_sent_at:
      NULL                               -> no reminders yet
      (reminder_sent_at - created_at) < 72h -> 24h reminder sent, 3d pending
      (reminder_sent_at - created_at) >= 72h -> 3d reminder sent, done
    """
    print("\n🔔 Checking DSW lead reminders...")
    sent_24h = sent_3d = skipped_status = skipped_age = already_done = 0

    try:
        now_utc = datetime.now(pytz.UTC)
        result = _get_supabase().table('tasks')\
            .select('id, title, description, client_name, lead_status, created_at, reminder_sent_at, status')\
            .eq('status', 'pending')\
            .eq('category', 'DSW Solar')\
            .order('created_at')\
            .execute()
        tasks = result.data or []

        from dsw_lead_poller import send_dsw_reminder_for_task

        for task in tasks:
            try:
                if (task.get('lead_status') or 'new_lead') != 'new_lead':
                    skipped_status += 1
                    continue

                created_raw = task.get('created_at')
                if not created_raw:
                    continue
                created_at = datetime.fromisoformat(created_raw.replace('Z', '+00:00'))
                age = now_utc - created_at

                rem_raw = task.get('reminder_sent_at')
                rem_at = datetime.fromisoformat(rem_raw.replace('Z', '+00:00')) if rem_raw else None

                if rem_at and (rem_at - created_at) >= timedelta(hours=72):
                    already_done += 1
                    continue

                tag = None
                if rem_at is None:
                    if age >= timedelta(hours=72):
                        tag = '3d'
                    elif age >= timedelta(hours=24):
                        tag = '24h'
                else:
                    if age >= timedelta(hours=72):
                        tag = '3d'

                if tag is None:
                    skipped_age += 1
                    continue

                print(f"   📨 DSW {tag} reminder: {task.get('client_name') or task.get('title','')[:40]}")
                send_dsw_reminder_for_task(task, tag)
                _get_supabase().table('tasks').update({
                    'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
                }).eq('id', task['id']).execute()

                if tag == '24h':
                    sent_24h += 1
                else:
                    sent_3d += 1
                time.sleep(0.3)

            except Exception as task_err:
                print(f"   ❌ DSW reminder error for {task.get('id','?')[:8]}: {task_err}")
                continue

        print(f"   DSW reminders: {sent_24h} 24h + {sent_3d} 3d sent, "
              f"{skipped_status} status-changed, {skipped_age} too-new, {already_done} done")
        return sent_24h + sent_3d

    except Exception as e:
        print(f"   ❌ DSW REMINDER ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 0


# ── Squad: Tuesday WhatsApp game-reminder ───────────────────────────────────
# Once per Tuesday 8 AM AEST, look ahead to that Saturday. If there's a game,
# email Rob a copy/paste-ready WhatsApp message for the team group. Idempotent
# via a module-level last-sent-date marker — fine for a single-process worker.

_squad_tuesday_last_date = None


def send_squad_tuesday_whatsapp():
    """Tuesday 8 AM AEST: email a WhatsApp-ready Saturday game message to Rob.

    Returns 'sent' | 'skipped' | 'failed'. Looks up every squad in the DB,
    finds Saturday's game (is_cancelled=false), and sends one email per
    squad. fruit_player_id (if set, post-028) is rendered as the duty family.
    """
    global _squad_tuesday_last_date
    aest = pytz.timezone('Australia/Brisbane')
    now = datetime.now(aest)

    # Gate: Tuesday + 8 AM hour
    if now.weekday() != 1 or now.hour != 8:
        return 'skipped'
    if _squad_tuesday_last_date == now.date():
        return 'skipped'  # already sent this morning

    # That Saturday is 4 days after Tuesday
    saturday = (now + timedelta(days=4)).date().isoformat()

    sb = _get_supabase()
    try:
        squads = (sb.table('squads').select('id, name').execute().data) or []
    except Exception as e:
        print(f"[SquadTue] squad lookup failed: {e}")
        return 'failed'
    if not squads:
        _squad_tuesday_last_date = now.date()
        return 'skipped'

    sent_any = False
    for squad in squads:
        squad_id = squad['id']
        squad_name = squad.get('name') or 'Devils'
        try:
            ev_q = sb.table('squad_events').select('*')\
                .eq('squad_id', squad_id).eq('event_date', saturday)\
                .eq('event_type', 'game').eq('is_cancelled', False)\
                .order('event_time')
            events = (ev_q.execute().data) or []
        except Exception as e:
            print(f"[SquadTue] event lookup failed for squad {squad_id[:8]}: {e}")
            continue
        if not events:
            continue

        # Resolve fruit player names in one batch
        fruit_ids = [e.get('fruit_player_id') for e in events if e.get('fruit_player_id')]
        name_by_id = {}
        if fruit_ids:
            try:
                fp = sb.table('squad_players').select('id, player_name')\
                    .in_('id', list(set(fruit_ids))).execute()
                name_by_id = {p['id']: p['player_name'] for p in (fp.data or [])}
            except Exception:
                pass

        for ev in events:
            try:
                # Format date: e.g. "Saturday 2 May at 9:30 AM"
                dt = datetime.fromisoformat(saturday)
                date_str = dt.strftime(f'%A {dt.day} %B').replace(' 0', ' ')
                time_raw = (ev.get('event_time') or '')[:5]
                if time_raw:
                    try:
                        h, m = time_raw.split(':')
                        h12 = ((int(h) - 1) % 12) + 1
                        ampm = 'AM' if int(h) < 12 else 'PM'
                        time_str = f"{h12}:{m} {ampm}"
                    except Exception:
                        time_str = time_raw
                else:
                    time_str = 'TBC'
                opponent = ev.get('opponent') or 'TBD'
                home_or_away = 'Home' if ev.get('is_home') else ('Away' if ev.get('is_home') is False else 'TBC')
                venue = ev.get('venue') or 'Venue TBC'
                # Try to extract field number from venue ("Runcorn Fields #3" or "Runcorn, Field 3")
                import re as _re
                fmatch = _re.search(r'(?:field|fld|f)\s*#?\s*(\d+)', venue, _re.IGNORECASE)
                field_part = f", Field {fmatch.group(1)}" if fmatch else ''
                # Strip the field bit out of the venue display so it doesn't double up
                venue_clean = _re.sub(r'(?:field|fld|f)\s*#?\s*\d+', '', venue, flags=_re.IGNORECASE).strip(' ,#-') or venue

                fruit_name = name_by_id.get(ev.get('fruit_player_id'))
                fruit_line = (
                    f"\n🍊 Fruit duty: {fruit_name} family\n" if fruit_name
                    else "\n🍊 Fruit duty: (not assigned)\n"
                )

                whatsapp = (
                    f"⚽ {squad_name} Game This Saturday!\n\n"
                    f"📅 {date_str} at {time_str}\n"
                    f"🆚 vs {opponent} ({home_or_away})\n"
                    f"📍 {venue_clean}{field_part}\n"
                    f"{fruit_line}"
                    f"\nGood luck {squad_name}! 🟢⚫"
                )

                html = (
                    f'<div style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;'
                    f'max-width:560px;margin:0 auto;padding:20px;">'
                    f'<h2 style="color:#15803d;margin:0 0 8px;">Tuesday WhatsApp prep — {squad_name}</h2>'
                    f'<p style="color:#374151;font-size:14px;">Copy &amp; paste this into the team group:</p>'
                    f'<pre style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;'
                    f'padding:16px;font-family:-apple-system,sans-serif;font-size:15px;line-height:1.6;'
                    f'white-space:pre-wrap;color:#1a2e1a;">{whatsapp}</pre>'
                    f'<p style="color:#9ca3af;font-size:12px;margin-top:16px;">'
                    f'Auto-generated Tuesday 8 AM AEST · '
                    f'<a href="https://www.jottask.app/squad/" style="color:#15803d;">Open Squad</a></p>'
                    f'</div>'
                )

                ok, err = send_email(
                    'rob@cloudcleanenergy.com.au',
                    f'⚽ {squad_name} Saturday — WhatsApp copy ({date_str})',
                    html, category='squad_tuesday',
                )
                if ok:
                    sent_any = True
                    print(f"[SquadTue] sent for {squad_name} vs {opponent}")
                else:
                    print(f"[SquadTue] send failed for {squad_name}: {err}")
            except Exception as e:
                print(f"[SquadTue] format/send error: {e}")

    _squad_tuesday_last_date = now.date()
    return 'sent' if sent_any else 'skipped'


def run_scheduler():
    """Main scheduler loop"""
    print("🚀 Starting Jottask Scheduler (Summaries + Reminders)")
    print(f"📧 Sending via Resend API")
    print(f"🔔 Task reminders: every 1 minute check")
    print(f"📊 Daily summaries: at user-configured time")
    print("=" * 50)

    while True:
        try:
            now = datetime.now(pytz.UTC)
            now_aest = datetime.now(pytz.timezone('Australia/Brisbane'))
            print(f"\n⏰ {now_aest.strftime('%Y-%m-%d %H:%M:%S')} AEST ({now.strftime('%H:%M')} UTC) - Scheduler tick")

            # Check task reminders every tick (every 1 minute)
            check_and_send_reminders()
            check_and_send_dsw_reminders()


            # Poll Squad inbox every tick (Gmail IMAP, ~15s per run)
            try:
                from squad_email_processor import poll_squad_inbox
                poll_squad_inbox()
            except Exception as squad_err:
                print(f"⚠️  Squad poller error (non-fatal): {squad_err}")

            # Check daily summaries
            users = get_users_needing_summary()

            if users:
                print(f"📬 Found {len(users)} user(s) needing daily summary")
                for user in users:
                    send_daily_summary(user)

            # Sleep for 1 minute before checking again
            time.sleep(60)

        except KeyboardInterrupt:
            print("\n👋 Shutting down scheduler...")
            break
        except Exception as e:
            print(f"❌ Scheduler error: {e}")
            time.sleep(60)  # Wait 1 minute on error


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    run_scheduler()
