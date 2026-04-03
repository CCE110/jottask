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
    """Get task summary for a user"""
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)
    today = now.date().isoformat()

    # Get all pending tasks
    tasks = _get_supabase().table('tasks')\
        .select('id, title, due_date, due_time, priority, status, client_name')\
        .eq('user_id', user_id)\
        .eq('status', 'pending')\
        .neq('category', 'DSW Solar')\
        .order('due_date')\
        .order('due_time')\
        .limit(50)\
        .execute()

    all_tasks = tasks.data or []

    # Categorize tasks
    overdue = []
    due_today = []
    upcoming = []

    for task in all_tasks:
        due_date = task.get('due_date')
        if not due_date:
            upcoming.append(task)
            continue

        if due_date < today:
            overdue.append(task)
        elif due_date == today:
            due_today.append(task)
        else:
            upcoming.append(task)

    return {
        'overdue': overdue,
        'due_today': due_today,
        'upcoming': upcoming[:10],  # Limit upcoming to 10
        'total_pending': len(all_tasks)
    }


def get_user_projects_summary(user_id):
    """Get project summary for a user"""
    # Get active projects
    projects = _get_supabase().table('saas_projects')\
        .select('id, name, color, status')\
        .eq('user_id', user_id)\
        .eq('status', 'active')\
        .order('created_at', desc=True)\
        .limit(10)\
        .execute()

    projects_with_progress = []
    total_items_remaining = 0

    for project in (projects.data or []):
        # Get items for this project
        items = _get_supabase().table('saas_project_items')\
            .select('id, is_completed')\
            .eq('project_id', project['id'])\
            .execute()

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

    # Build tasks section
    tasks_html = ""

    if tasks_summary['overdue']:
        tasks_html += """
        <div style="margin-bottom: 24px;">
            <h3 style="color: #EF4444; font-size: 14px; margin-bottom: 12px;">OVERDUE</h3>
        """
        for task in tasks_summary['overdue'][:5]:
            tasks_html += f"""
            <div style="padding: 12px; background: #FEE2E2; border-radius: 8px; margin-bottom: 8px;">
                <strong style="color: #991B1B;">{task['title']}</strong>
                <div style="font-size: 12px; color: #B91C1C;">Due: {task['due_date']}</div>
            </div>
            """
        tasks_html += "</div>"

    if tasks_summary['due_today']:
        tasks_html += """
        <div style="margin-bottom: 24px;">
            <h3 style="color: #6366F1; font-size: 14px; margin-bottom: 12px;">DUE TODAY</h3>
        """
        for task in tasks_summary['due_today'][:10]:
            time_str = task['due_time'][:5] if task.get('due_time') else ''
            tasks_html += f"""
            <div style="padding: 12px; background: #EEF2FF; border-radius: 8px; margin-bottom: 8px;">
                <strong style="color: #4338CA;">{task['title']}</strong>
                <div style="font-size: 12px; color: #6366F1;">{time_str}</div>
            </div>
            """
        tasks_html += "</div>"

    if tasks_summary['upcoming']:
        tasks_html += """
        <div style="margin-bottom: 24px;">
            <h3 style="color: #6B7280; font-size: 14px; margin-bottom: 12px;">COMING UP</h3>
        """
        for task in tasks_summary['upcoming'][:5]:
            tasks_html += f"""
            <div style="padding: 12px; background: #F3F4F6; border-radius: 8px; margin-bottom: 8px;">
                <strong style="color: #374151;">{task['title']}</strong>
                <div style="font-size: 12px; color: #6B7280;">Due: {task['due_date']}</div>
            </div>
            """
        tasks_html += "</div>"

    if not tasks_summary['overdue'] and not tasks_summary['due_today'] and not tasks_summary['upcoming']:
        tasks_html = """
        <div style="text-align: center; padding: 24px; color: #6B7280;">
            <p>No pending tasks. You're all caught up!</p>
        </div>
        """

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


def send_daily_summary(user):
    """Send daily summary email to a user"""
    user_id = user['id']
    user_email = user['email']
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
        _get_supabase().table('users').update({
            'last_summary_sent_at': datetime.now(pytz.UTC).isoformat()
        }).eq('id', user_id).execute()
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
        _get_supabase().table('users').update({
            'last_summary_sent_at': datetime.now(pytz.UTC).isoformat()
        }).eq('id', user_id).execute()


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
    4. Throttle: max 1 reminder per task per hour (not 24h — that's too long for overdue).

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

        all_tasks_result = _get_supabase().table('tasks')\
            .select('id, title, due_date, due_time, priority, status, client_name, user_id, reminder_sent_at')\
            .eq('status', 'pending')\
        .neq('category', 'DSW Solar')\
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
        already_reminded_today = 0
        eight_hours_ago = datetime.now(pytz.UTC) - timedelta(hours=8)

        # ── Step 3: Check each task ──
        for task in all_tasks:
            try:
                task_id = task['id']
                user_id = task.get('user_id')
                if not user_id or user_id not in users:
                    continue

                user = users[user_id]

                # Parse user timezone safely
                try:
                    user_tz = pytz.timezone(user.get('timezone') or 'Australia/Brisbane')
                except Exception:
                    user_tz = pytz.timezone('Australia/Brisbane')
                now = datetime.now(user_tz)

                # ── Throttle check: was this task reminded in the last hour? ──
                last_reminded = task.get('reminder_sent_at')
                if last_reminded:
                    try:
                        last_dt = datetime.fromisoformat(last_reminded.replace('Z', '+00:00'))
                        if last_dt > eight_hours_ago:
                            skipped_throttle += 1
                            continue  # Reminded less than 8 hours ago — skip
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

                # ── Check if already reminded today (first-time only, not re-reminders) ──
                if last_reminded and not is_overdue:
                    # Already got a first reminder and not overdue — don't spam
                    already_reminded_today += 1
                    continue

                # ── Build and send the email ──
                if is_overdue:
                    subject = f"Overdue: {task['title'][:50]} - was due {display_time}"
                    print(f"   📨 OVERDUE: '{task['title'][:45]}' -> {user['email']}")
                else:
                    subject = f"Reminder: {task['title'][:50]} - due {display_time}"
                    print(f"   📨 Reminder: '{task['title'][:45]}' -> {user['email']}")

                html_content = generate_reminder_email_html(
                    task, display_time, user.get('full_name', ''), is_overdue=is_overdue
                )

                # ── SEND FIRST, then mark ──
                # If send fails → reminder_sent_at stays old → next tick retries. Good.
                # If send succeeds but DB update fails → duplicate next tick. Acceptable.
                success, error = send_email(user['email'], subject, html_content,
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
              f"{skipped_future} future, {skipped_throttle} throttled (<1h), "
              f"{already_reminded_today} already reminded")
        return sent_count

    except Exception as e:
        print(f"   ❌ REMINDER SYSTEM ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 0


def check_and_send_dsw_reminders():
    """Resend DSW Solar lead emails for overdue tasks that haven't been reminded yet.

    Queries pending DSW Solar tasks with no reminder_sent_at and due_date+due_time in the past.
    For each: looks up the Pipereply contact by client_name and calls dsw_lead_poller.process()
    with the existing task_id and lead_status so no duplicate tasks/contacts are created.
    """
    print(f"\n🔵 Checking DSW Solar reminders...")
    try:
        aest = pytz.timezone('Australia/Brisbane')
        now_aest = datetime.now(aest)
        today_str = now_aest.strftime('%Y-%m-%d')
        now_time_str = now_aest.strftime('%H:%M')

        # Get all pending DSW Solar tasks (filter overdue in Python to avoid null-check syntax issues)
        result = _get_supabase().table('tasks')\
            .select('id, title, client_name, due_date, due_time, lead_status, reminder_sent_at')\
            .eq('status', 'pending')\
            .eq('category', 'DSW Solar')\
            .execute()

        candidates = result.data or []
        overdue = []
        for task in candidates:
            if task.get('reminder_sent_at'):
                continue  # Already reminded
            due_date = task.get('due_date', '')
            due_time = (task.get('due_time') or '')[:5]
            if not due_date:
                continue
            if due_date < today_str:
                overdue.append(task)
            elif due_date == today_str and due_time and due_time <= now_time_str:
                overdue.append(task)

        if not overdue:
            print(f"   No overdue DSW Solar tasks needing reminders")
            return 0

        print(f"   {len(overdue)} overdue DSW Solar task(s) to remind")

        pipereply_token = os.getenv('PIPEREPLY_TOKEN')
        pipereply_location = os.getenv('PIPEREPLY_LOCATION_ID')
        if not pipereply_token or not pipereply_location:
            print("   PIPEREPLY_TOKEN or PIPEREPLY_LOCATION_ID not set — skipping DSW reminders")
            return 0

        import requests as preq
        ph = {'Authorization': f'Bearer {pipereply_token}', 'Content-Type': 'application/json', 'Version': '2021-07-28'}

        # Lazy-import dsw_lead_poller to avoid circular import issues at module load
        import importlib.util as ilu
        _spec = ilu.spec_from_file_location('dsw_lead_poller',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dsw_lead_poller.py'))
        dsw = ilu.module_from_spec(_spec)
        _spec.loader.exec_module(dsw)

        sent = 0
        for task in overdue:
            try:
                name = task.get('client_name') or ''
                task_id = task['id']
                lead_status = task.get('lead_status') or 'new_lead'

                if not name:
                    print(f"   Skipping {task_id[:8]}: no client_name")
                    continue

                # Find contact in Pipereply by name
                r = preq.get('https://services.leadconnectorhq.com/contacts/',
                    headers=ph,
                    params={'locationId': pipereply_location, 'query': name, 'limit': 1},
                    timeout=10)

                if not r.ok:
                    print(f"   Pipereply lookup failed for '{name}': HTTP {r.status_code}")
                    continue

                contacts = r.json().get('contacts', [])
                if not contacts:
                    print(f"   No Pipereply contact found for: '{name}'")
                    continue

                print(f"   Resending lead email: {name} (status: {lead_status})")
                dsw.process(contacts[0], task_id=task_id, lead_status=lead_status)

                # Mark reminder_sent_at after process() completes
                _get_supabase().table('tasks').update({
                    'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
                }).eq('id', task_id).execute()

                sent += 1
                time.sleep(0.5)

            except Exception as task_err:
                print(f"   Error processing DSW task {task.get('id', '?')[:8]}: {task_err}")
                continue

        print(f"   DSW reminders: {sent} sent")
        return sent

    except Exception as e:
        print(f"   DSW reminder error: {e}")
        import traceback
        traceback.print_exc()
        return 0


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

            # Check DSW Solar lead reminders (resend lead email for overdue tasks)
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
