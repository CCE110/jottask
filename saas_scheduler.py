"""
Jottask SaaS Scheduler
Daily summary emails, task reminders, and scheduled tasks
"""

import os
import time
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pytz
from supabase import create_client, Client

# Initialize Supabase
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Email configuration
SMTP_SERVER = os.getenv('SMTP_SERVER', 'mail.privateemail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USER = os.getenv('JOTTASK_EMAIL', 'jottask@flowquote.ai')
SMTP_PASSWORD = os.getenv('JOTTASK_EMAIL_PASSWORD')


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
                <a href="https://jottask.flowquote.ai/dashboard" style="display: inline-block; background: #6366F1; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600;">Open Dashboard</a>
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
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Your Daily Summary - {datetime.now(pytz.timezone(user_timezone)).strftime('%b %d')}"
        msg['From'] = f"Jottask <{SMTP_USER}>"
        msg['To'] = user_email

        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"    ✅ Summary sent to {user_email}")

        # Update last_summary_sent_at
        supabase.table('users').update({
            'last_summary_sent_at': datetime.now(pytz.UTC).isoformat()
        }).eq('id', user_id).execute()

    except Exception as e:
        print(f"    ❌ Failed to send summary: {e}")


ACTION_URL = os.getenv('TASK_ACTION_URL', 'https://www.jottask.app/action')


def generate_reminder_email_html(task, due_time_str, user_name):
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

    client_line = f'<div style="font-size: 14px; color: #6B7280;">Client: {client_name}</div>' if client_name else ''

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #F9FAFB;">
        <div style="background: linear-gradient(135deg, {color} 0%, {color}CC 100%); padding: 24px 32px; border-radius: 16px 16px 0 0;">
            <h1 style="color: white; margin: 0 0 4px 0; font-size: 22px;">Task Reminder</h1>
            <p style="color: rgba(255,255,255,0.9); margin: 0; font-size: 14px;">Due at {due_time_str}</p>
        </div>

        <div style="background: white; padding: 32px; border-radius: 0 0 16px 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h2 style="color: #111827; margin: 0 0 8px 0; font-size: 20px;">{title}</h2>
            {client_line}

            <p style="color: #6b7280; font-size: 13px; margin-top: 16px;">You'll receive a reminder 5-20 minutes before this task is due.</p>

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
                <a href="https://jottask.flowquote.ai/dashboard" style="color: #6366F1; font-size: 13px;">Open Dashboard</a>
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
    """Check for tasks due soon and send reminder emails"""
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

        # Get all pending tasks due today (across all users) that have a due_time
        # We check per-user timezone, so get all pending tasks with due_time
        all_tasks = supabase.table('tasks')\
            .select('id, title, due_date, due_time, priority, status, client_name, user_id, reminder_sent_at')\
            .eq('status', 'pending')\
            .not_.is_('due_time', 'null')\
            .execute()

        tasks = all_tasks.data or []
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

                # Only process tasks due today in the user's timezone
                if task.get('due_date') != today_str:
                    continue

                # Parse due time
                due_time_str = task['due_time']
                parts = due_time_str.split(':')
                hour, minute = int(parts[0]), int(parts[1])
                second = int(float(parts[2])) if len(parts) > 2 else 0

                task_due = now.replace(
                    hour=hour,
                    minute=minute,
                    second=second,
                    microsecond=0
                )

                # Calculate time difference in minutes
                time_diff = (task_due - now).total_seconds() / 60

                # Send reminder if within -5 to +20 minute window
                if -5 <= time_diff <= 20:
                    # Check if reminder already sent today
                    if task.get('reminder_sent_at'):
                        try:
                            sent_at = datetime.fromisoformat(
                                task['reminder_sent_at'].replace('Z', '+00:00')
                            )
                            sent_at_local = sent_at.astimezone(user_tz)
                            if sent_at_local.date() == now.date():
                                continue  # Already sent today
                        except:
                            pass

                    # Format due time for display
                    display_time = task_due.strftime('%I:%M %p')

                    print(f"   Sending reminder: {task['title'][:40]} -> {user['email']}")

                    # Generate and send email
                    html_content = generate_reminder_email_html(
                        task, display_time, user.get('full_name', '')
                    )

                    msg = MIMEMultipart('alternative')
                    msg['Subject'] = f"Reminder: {task['title'][:50]} - due {display_time}"
                    msg['From'] = f"Jottask <{SMTP_USER}>"
                    msg['To'] = user['email']
                    msg.attach(MIMEText(html_content, 'html'))

                    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                        server.starttls()
                        server.login(SMTP_USER, SMTP_PASSWORD)
                        server.send_message(msg)

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
            print("   No tasks in reminder window right now")

    except Exception as e:
        print(f"Reminder error: {e}")
        import traceback
        traceback.print_exc()


def run_scheduler():
    """Main scheduler loop"""
    print("🚀 Starting Jottask Scheduler (Summaries + Reminders)")
    print(f"📧 Sending from: {SMTP_USER}")
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
