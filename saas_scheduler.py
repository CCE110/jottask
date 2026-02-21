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

# Initialize Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_users_needing_summary():
    """Get users who need their daily summary sent now"""
    users_to_notify = []

    # Get all users with daily summary enabled
    result = supabase.table('users').select(
        'id, email, full_name, timezone, daily_summary_enabled, daily_summary_time, last_summary_sent_at'
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
    tasks = supabase.table('tasks')\
        .select('id, title, due_date, due_time, priority, status, client_name')\
        .eq('user_id', user_id)\
        .eq('status', 'pending')\
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
    projects = supabase.table('saas_projects')\
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
        items = supabase.table('saas_project_items')\
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
        supabase.table('users').update({
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

    # Send email
    subject = f"Your Daily Summary - {datetime.now(pytz.timezone(user_timezone)).strftime('%b %d')}"
    success, error = send_email(user_email, subject, html_content)

    if success:
        # Update last_summary_sent_at
        supabase.table('users').update({
            'last_summary_sent_at': datetime.now(pytz.UTC).isoformat()
        }).eq('id', user_id).execute()
    else:
        print(f"    Failed to send summary to {user_email}: {error}")


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
        subtext = f'Was due at {due_time_str}'
    else:
        heading = 'Task Reminder'
        subtext = f'Due at {due_time_str}'

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #F9FAFB;">
        <div style="background: linear-gradient(135deg, {color} 0%, {color}CC 100%); padding: 24px 32px; border-radius: 16px 16px 0 0;">
            <h1 style="color: white; margin: 0 0 4px 0; font-size: 22px;">{heading}</h1>
            <p style="color: rgba(255,255,255,0.9); margin: 0; font-size: 14px;">{subtext}</p>
        </div>

        <div style="background: white; padding: 32px; border-radius: 0 0 16px 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h2 style="color: #111827; margin: 0 0 8px 0; font-size: 20px;">{title}</h2>
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
    """Check for tasks due soon and send reminder emails.

    Failsafe design — reminders are NEVER silently missed:
    1. Upcoming: send 30 min before due time (normal reminder)
    2. Catch-up: if due time passed today/yesterday and no reminder was sent,
       send an overdue reminder on the next scheduler tick
    3. Duplicate prevention: reminder_sent_at tracked per task per day
    """
    print(f"\n🔔 Checking task reminders...")

    try:
        # Get all users (we need their timezones and emails)
        users_result = supabase.table('users').select(
            'id, email, full_name, timezone'
        ).execute()
        users = {u['id']: u for u in (users_result.data or [])}

        if not users:
            print("   No users found")
            return

        print(f"   Found {len(users)} user(s)")

        # Get ALL pending tasks — filter due_time in Python to avoid
        # supabase client filter issues with null checks
        all_tasks = supabase.table('tasks')\
            .select('id, title, due_date, due_time, priority, status, client_name, user_id, reminder_sent_at')\
            .eq('status', 'pending')\
            .execute()

        raw_tasks = all_tasks.data or []
        # Filter to tasks that have a due_time set (in Python — more reliable)
        tasks = [t for t in raw_tasks if t.get('due_time')]

        print(f"   {len(raw_tasks)} pending tasks total, {len(tasks)} with due_time")
        if not tasks:
            print("   No pending tasks with due times")
            return

        sent_count = 0

        for task in tasks:
            try:
                user_id = task.get('user_id')
                if not user_id or user_id not in users:
                    continue

                user = users[user_id]
                user_tz = pytz.timezone(user.get('timezone', 'Australia/Brisbane'))
                now = datetime.now(user_tz)
                today_str = now.date().isoformat()
                yesterday_str = (now.date() - timedelta(days=1)).isoformat()

                task_due_date = task.get('due_date')
                if not task_due_date:
                    continue

                # Normalize due_date — handle both "2026-02-21" and "2026-02-21T00:00:00" formats
                task_due_date = str(task_due_date)[:10]

                # Only process tasks due today or yesterday (catch-up window)
                is_today = (task_due_date == today_str)
                is_yesterday = (task_due_date == yesterday_str)
                if not is_today and not is_yesterday:
                    continue

                # Parse due time — handle "HH:MM", "HH:MM:SS", "HH:MM:SS.ffffff"
                due_time_str = str(task['due_time'])
                parts = due_time_str.split(':')
                try:
                    hour, minute = int(parts[0]), int(parts[1])
                    second = int(float(parts[2])) if len(parts) > 2 else 0
                except (ValueError, IndexError):
                    print(f"   Skipping task {task['id']}: bad due_time format '{due_time_str}'")
                    continue

                # Build the full due datetime in user's timezone
                if is_today:
                    task_due = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
                else:
                    # Yesterday's task
                    yesterday_dt = now - timedelta(days=1)
                    task_due = yesterday_dt.replace(hour=hour, minute=minute, second=second, microsecond=0)

                # Log candidate task for debugging
                time_diff = (task_due - now).total_seconds() / 60
                print(f"   Candidate: '{task['title'][:40]}' due={task_due_date} {due_time_str} diff={time_diff:.0f}m sent_at={task.get('reminder_sent_at')}")

                # Check if reminder already sent for this task's due date
                if task.get('reminder_sent_at'):
                    try:
                        sent_at = datetime.fromisoformat(
                            str(task['reminder_sent_at']).replace('Z', '+00:00')
                        )
                        sent_at_local = sent_at.astimezone(user_tz)
                        if sent_at_local.date() >= task_due.date():
                            print(f"   -> Skipped (already sent {sent_at_local.date()})")
                            continue
                    except Exception as parse_err:
                        print(f"   -> Warning: could not parse reminder_sent_at: {parse_err}")

                # Decide whether to send
                is_overdue = False
                should_send = False

                if 0 <= time_diff <= 30:
                    # Normal reminder: task due within next 30 minutes
                    should_send = True
                elif time_diff < 0:
                    # Overdue catch-up: task due time has passed, no reminder sent
                    # Cap at 24 hours to avoid spamming ancient tasks
                    if time_diff >= -1440:
                        should_send = True
                        is_overdue = True

                if not should_send:
                    print(f"   -> Skipped (not in window: {time_diff:.0f}m)")
                    continue

                # Format due time for display
                display_time = task_due.strftime('%I:%M %p')

                if is_overdue:
                    overdue_mins = abs(int(time_diff))
                    if overdue_mins < 60:
                        late_label = f"{overdue_mins}m ago"
                    else:
                        late_label = f"{overdue_mins // 60}h {overdue_mins % 60}m ago"
                    print(f"   Sending OVERDUE reminder ({late_label}): {task['title'][:40]} -> {user['email']}")
                    subject = f"Overdue: {task['title'][:50]} - was due {display_time}"
                else:
                    print(f"   Sending reminder: {task['title'][:40]} -> {user['email']}")
                    subject = f"Reminder: {task['title'][:50]} - due {display_time}"

                # Generate and send email
                html_content = generate_reminder_email_html(
                    task, display_time, user.get('full_name', ''), is_overdue=is_overdue
                )

                success, error = send_email(user['email'], subject, html_content)

                if not success:
                    print(f"   Failed to send reminder: {error}")
                    continue

                # Mark reminder as sent
                supabase.table('tasks').update({
                    'reminder_sent_at': datetime.now(pytz.UTC).isoformat()
                }).eq('id', task['id']).execute()

                sent_count += 1
                time.sleep(0.5)  # Rate limit

            except Exception as e:
                print(f"   Error with task {task.get('id')}: {e}")
                continue

        if sent_count > 0:
            print(f"   Sent {sent_count} reminder(s)")
        else:
            print("   No reminders needed right now")

    except Exception as e:
        print(f"Reminder error: {e}")
        import traceback
        traceback.print_exc()


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
            print(f"\n⏰ {now.strftime('%Y-%m-%d %H:%M:%S')} UTC - Scheduler tick")

            # Check task reminders every tick (every 1 minute)
            check_and_send_reminders()

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
