"""
Jottask SaaS Email Processor
Central inbox email processing - matches senders to user accounts
"""

import os
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import pytz
import time
import json
from supabase import create_client, Client
from anthropic import Anthropic

# Initialize clients
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

# Central Jottask inbox credentials
JOTTASK_EMAIL = os.getenv('JOTTASK_EMAIL', 'jottask@flowquote.ai')
JOTTASK_PASSWORD = os.getenv('JOTTASK_EMAIL_PASSWORD')
IMAP_SERVER = os.getenv('IMAP_SERVER', 'mail.privateemail.com')

# Email sending via Resend
RESEND_API_KEY = os.getenv('RESEND_API_KEY')
FROM_EMAIL = os.getenv('FROM_EMAIL', 'jottask@flowquote.ai')


def send_email_direct(to_email, subject, body_html):
    """Send email directly via Resend API (no web service middleman)"""
    import urllib.request
    import urllib.error

    if not RESEND_API_KEY:
        print("  ❌ RESEND_API_KEY not configured")
        return False

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
            if response.getcode() in [200, 201]:
                print(f"  ✅ Email sent to {to_email}")
                return True
            else:
                print(f"  ❌ Resend returned status {response.getcode()}")
                return False

    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8', errors='ignore')
        print(f"  ❌ Resend HTTP error ({e.code}): {error_msg}")
        return False
    except Exception as e:
        print(f"  ❌ Failed to send email: {type(e).__name__}: {e}")
        return False

# Secondary inbox (robcrm.ai@gmail.com) - optional
ROBCRM_EMAIL = os.getenv('ROBCRM_EMAIL', 'robcrm.ai@gmail.com')
ROBCRM_PASSWORD = os.getenv('ROBCRM_EMAIL_PASSWORD')
ROBCRM_IMAP_SERVER = 'imap.gmail.com'


def get_user_by_email(sender_email):
    """Find a user by their primary email or alternate emails"""
    sender_lower = sender_email.lower()

    # First try primary email
    result = supabase.table('users')\
        .select('id, email, full_name, timezone, subscription_tier, subscription_status, alternate_emails')\
        .eq('email', sender_lower)\
        .execute()

    if result.data and len(result.data) > 0:
        return result.data[0]

    # Then check alternate_emails array
    result = supabase.table('users')\
        .select('id, email, full_name, timezone, subscription_tier, subscription_status, alternate_emails')\
        .contains('alternate_emails', [sender_lower])\
        .execute()

    if result.data and len(result.data) > 0:
        print(f"    ✓ Matched alternate email for user: {result.data[0]['email']}")
        return result.data[0]

    return None


def connect_to_jottask_inbox():
    """Connect to the central Jottask IMAP inbox"""
    try:
        imap = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
        imap.login(JOTTASK_EMAIL, JOTTASK_PASSWORD)
        print(f"✅ Connected to {JOTTASK_EMAIL}")
        return imap
    except Exception as e:
        print(f"❌ Failed to connect to Jottask inbox: {e}")
        return None


def connect_to_robcrm_inbox():
    """Connect to the secondary RobCRM Gmail inbox"""
    if not ROBCRM_PASSWORD:
        return None
    try:
        imap = imaplib.IMAP4_SSL(ROBCRM_IMAP_SERVER, 993)
        imap.login(ROBCRM_EMAIL, ROBCRM_PASSWORD)
        print(f"✅ Connected to {ROBCRM_EMAIL}")
        return imap
    except Exception as e:
        print(f"⚠️ Could not connect to RobCRM inbox: {e}")
        return None


def decode_email_subject(subject):
    """Decode email subject handling various encodings"""
    if not subject:
        return "No Subject"

    decoded_parts = decode_header(subject)
    decoded_subject = ""

    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            try:
                decoded_subject += part.decode(encoding or 'utf-8', errors='replace')
            except:
                decoded_subject += part.decode('utf-8', errors='replace')
        else:
            decoded_subject += part

    return decoded_subject.strip()


def get_email_body(msg):
    """Extract email body text"""
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode('utf-8', errors='replace')
                        break
                except:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode('utf-8', errors='replace')
        except:
            pass

    return body[:5000]  # Limit body size


def analyze_email_with_ai(subject, body, from_email, user_timezone):
    """Use Claude to analyze email and extract task information"""
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)

    prompt = f"""Analyze this email and determine if it contains an actionable task.

From: {from_email}
Subject: {subject}
Body:
{body[:3000]}

Current date/time: {now.strftime('%Y-%m-%d %H:%M')} ({user_timezone})

If this email contains a task or follow-up needed, respond with JSON:
{{
    "is_task": true,
    "title": "brief task title",
    "description": "task details",
    "due_date": "YYYY-MM-DD or null if no date specified",
    "due_time": "HH:MM or null if no time specified",
    "priority": "low|medium|high|urgent",
    "client_name": "name if mentioned",
    "client_email": "{from_email}",
    "project_name": "project if mentioned"
}}

IMPORTANT: If the email does NOT specify a specific date or time for the task, return null for due_date and/or due_time. Do not guess or assume a date/time.

If this is NOT an actionable task (newsletter, spam, notification, etc), respond with:
{{
    "is_task": false,
    "reason": "brief reason"
}}

Respond with only valid JSON, no other text."""

    try:
        response = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text.strip()

        # Clean up response if needed
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]

        return json.loads(response_text)

    except Exception as e:
        print(f"❌ AI analysis failed: {e}")
        return {"is_task": False, "reason": "AI analysis failed"}


def check_if_email_processed(email_id, user_id):
    """Check if email has already been processed for this user"""
    result = supabase.table('processed_emails')\
        .select('id')\
        .eq('email_id', f"{user_id}_{email_id}")\
        .execute()
    return len(result.data) > 0


def mark_email_processed(email_id, user_id):
    """Mark email as processed"""
    try:
        supabase.table('processed_emails').insert({
            'email_id': f"{user_id}_{email_id}"
        }).execute()
    except:
        pass  # Ignore if already exists


def create_task_for_user(user_id, task_data):
    """Create a task for a specific user, checking for duplicates first"""
    try:
        title = task_data.get('title', 'Email Task')
        due_date = task_data.get('due_date')

        # Check for duplicate task (same title, user, and due date)
        existing = supabase.table('tasks')\
            .select('id, title')\
            .eq('user_id', user_id)\
            .eq('title', title)\
            .execute()

        if existing.data:
            print(f"⏭️ Duplicate task skipped: {title}")
            return existing.data[0]  # Return existing task

        task = {
            'user_id': user_id,
            'title': title,
            'description': task_data.get('description'),
            'due_date': due_date,
            'due_time': task_data.get('due_time', '09:00') + ':00',
            'priority': task_data.get('priority', 'medium'),
            'status': 'pending',
            'client_name': task_data.get('client_name'),
            'client_email': task_data.get('client_email'),
            'project_name': task_data.get('project_name')
        }

        result = supabase.table('tasks').insert(task).execute()

        if result.data:
            print(f"✅ Task created: {task['title']}")
            return result.data[0]

    except Exception as e:
        print(f"❌ Failed to create task: {e}")

    return None


# ============================================
# PROJECT EMAIL PROCESSING
# ============================================

def is_project_email(subject):
    """Check if subject starts with 'Project:'"""
    return subject.lower().strip().startswith('project:')


def analyze_project_email_with_ai(subject, body, from_email):
    """Use Claude to analyze project email and extract project name and items"""
    prompt = f"""Analyze this email about a project and extract the project name and checklist items.

From: {from_email}
Subject: {subject}
Body:
{body[:3000]}

The subject format is typically: "Project: Project Name - item1, item2, item3"
Or the body may contain a list of items.

Respond with JSON:
{{
    "project_name": "extracted project name",
    "items": ["item 1", "item 2", "item 3"],
    "description": "optional project description if mentioned"
}}

If you can't extract meaningful data, respond with:
{{
    "project_name": null,
    "items": [],
    "error": "reason"
}}

Respond with only valid JSON, no other text."""

    try:
        response = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text.strip()

        # Clean up response if needed
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]

        return json.loads(response_text)

    except Exception as e:
        print(f"❌ AI project analysis failed: {e}")
        return {"project_name": None, "items": [], "error": str(e)}


def find_or_create_project(user_id, project_name, description=None):
    """Find existing project by name or create a new one"""
    # Look for existing active project with same name
    existing = supabase.table('saas_projects')\
        .select('id, name')\
        .eq('user_id', user_id)\
        .eq('status', 'active')\
        .ilike('name', project_name)\
        .execute()

    if existing.data and len(existing.data) > 0:
        print(f"  📁 Found existing project: {existing.data[0]['name']}")
        return existing.data[0]

    # Create new project
    result = supabase.table('saas_projects').insert({
        'user_id': user_id,
        'name': project_name,
        'description': description,
        'color': '#6366F1',
        'status': 'active'
    }).execute()

    if result.data:
        print(f"  📁 Created new project: {project_name}")
        return result.data[0]

    return None


def add_items_to_project(project_id, items, source_subject=None):
    """Add items to a project, skipping duplicates"""
    # Get existing items
    existing = supabase.table('saas_project_items')\
        .select('item_text')\
        .eq('project_id', project_id)\
        .execute()

    existing_texts = set(item['item_text'].lower().strip() for item in (existing.data or []))

    # Get max display order
    max_order_result = supabase.table('saas_project_items')\
        .select('display_order')\
        .eq('project_id', project_id)\
        .order('display_order', desc=True)\
        .limit(1)\
        .execute()

    current_order = max_order_result.data[0]['display_order'] if max_order_result.data else 0

    added_count = 0
    for item_text in items:
        item_text = item_text.strip()
        if not item_text:
            continue

        # Skip duplicates
        if item_text.lower().strip() in existing_texts:
            print(f"    ⏭️ Skipping duplicate: {item_text[:30]}...")
            continue

        current_order += 1
        supabase.table('saas_project_items').insert({
            'project_id': project_id,
            'item_text': item_text,
            'display_order': current_order,
            'source': 'email',
            'source_email_subject': source_subject
        }).execute()

        added_count += 1
        print(f"    ✅ Added item: {item_text[:40]}...")

    return added_count


def send_project_confirmation_email(user_email, project_name, items_added, user_name=None):
    """Send confirmation email for project updates"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    SMTP_SERVER = os.getenv('SMTP_SERVER', 'mail.privateemail.com')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
    SMTP_USER = os.getenv('JOTTASK_EMAIL', 'jottask@flowquote.ai')
    SMTP_PASSWORD = os.getenv('JOTTASK_EMAIL_PASSWORD')

    if not SMTP_PASSWORD:
        print("  ⚠️ SMTP password not configured, skipping confirmation email")
        return

    greeting = f"Hi {user_name}," if user_name else "Hi,"

    html_content = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%); padding: 24px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 24px;">Project Updated</h1>
        </div>
        <div style="background: #f9fafb; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
            <p style="color: #374151;">{greeting}</p>
            <p style="color: #374151;">Your project <strong>{project_name}</strong> has been updated with {items_added} new item(s).</p>
            <p style="color: #6b7280; font-size: 14px;">View your project in the Jottask dashboard to see all items and track progress.</p>
            <a href="https://jottask.flowquote.ai/projects" style="display: inline-block; background: #6366F1; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; margin-top: 16px;">View Projects</a>
        </div>
        <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 24px;">
            Jottask - AI-Powered Task Management
        </p>
    </body>
    </html>
    """

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Project Updated: {project_name}"
        msg['From'] = f"Jottask <{SMTP_USER}>"
        msg['To'] = user_email

        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"  📧 Confirmation email sent to {user_email}")

    except Exception as e:
        print(f"  ⚠️ Failed to send confirmation email: {e}")


def send_task_confirmation_email(user_email, task_title, due_date, due_time, task_id, user_name=None, user_id=None):
    """Send confirmation email for task creation - direct to Resend API"""
    WEB_SERVICE_URL = os.getenv('WEB_SERVICE_URL', 'https://www.jottask.app')

    print(f"  📧 Attempting task confirmation email:")
    print(f"      To: {user_email}")
    print(f"      Task: {task_title}")
    print(f"      Task ID: {task_id}")
    print(f"      Due: {due_date} {due_time}")

    # Use query-param format for action URLs (no login required)
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

    # Send directly via Resend API (bypasses web service)
    result = send_email_direct(user_email, f"✅ Task Created: {task_title}", html_content)

    if result:
        print(f"  ✅ Task confirmation email SENT successfully to {user_email}")
    else:
        print(f"  ❌ Task confirmation email FAILED for {user_email}")

    return result


def process_project_email(user_id, user_email, subject, body, user_name=None):
    """Process a project email - extract project and items, add to database"""
    print(f"  📁 Processing project email...")

    # Use AI to extract project info
    analysis = analyze_project_email_with_ai(subject, body, user_email)

    project_name = analysis.get('project_name')
    items = analysis.get('items', [])
    description = analysis.get('description')

    if not project_name:
        # Try to extract from subject directly
        # Format: "Project: Name - item1, item2"
        subject_clean = subject.strip()
        if subject_clean.lower().startswith('project:'):
            rest = subject_clean[8:].strip()
            if ' - ' in rest:
                parts = rest.split(' - ', 1)
                project_name = parts[0].strip()
                if len(parts) > 1:
                    items = [item.strip() for item in parts[1].split(',') if item.strip()]
            else:
                project_name = rest

    if not project_name:
        print(f"    ⚠️ Could not extract project name from email")
        return None

    # Find or create project
    project = find_or_create_project(user_id, project_name, description)
    if not project:
        print(f"    ❌ Failed to find or create project")
        return None

    # Add items
    items_added = 0
    if items:
        items_added = add_items_to_project(project['id'], items, subject)

    print(f"    ✅ Project '{project_name}' updated with {items_added} items")

    # Send confirmation email
    if items_added > 0:
        send_project_confirmation_email(user_email, project_name, items_added, user_name)

    return project


def is_missed_call_email(subject, body):
    """Detect if email is a missed call follow-up"""
    text = (subject + ' ' + body).lower()
    missed_indicators = [
        'missed you', 'sorry i missed', 'tried to call', 'couldn\'t reach',
        'unable to reach', 'no answer', 'missed call', 'tried calling',
        'called and missed', 'give me a call', 'call me back'
    ]
    return any(indicator in text for indicator in missed_indicators)


def is_awaiting_docs_email(subject, body):
    """Detect if email is requesting bills/photos from a client"""
    text = (subject + ' ' + body).lower()

    # Must have at least one bill-related indicator
    bill_indicators = ['power bill', 'electricity bill', 'bill please', 'send me a bill',
                       'copy of your bill', 'latest bill', 'energy bill']
    has_bill = any(indicator in text for indicator in bill_indicators)

    # Must have at least one photo-related indicator
    photo_indicators = ['photo', 'photos', 'pictures', 'pics', 'images', 'site photo',
                        'power board', 'meter box', 'switchboard', 'inverter', 'battery location']
    has_photo = any(indicator in text for indicator in photo_indicators)

    # Return true if both bill AND photo mentioned, OR specific combined phrases
    combined_indicators = ['bills and site photos', 'bill and photos', 'photos and bill',
                          'power bill please', 'send me some photos']
    has_combined = any(indicator in text for indicator in combined_indicators)

    return (has_bill and has_photo) or has_combined


def process_awaiting_docs_email(user_id, user_email, subject, body, to_header, user_timezone, user_name=None):
    """Process a request for docs email - create follow-up task 4 hours from now"""
    print(f"    📄 Processing awaiting docs follow-up...")

    # Extract contact name from the To field (the client)
    contact_name = extract_name_from_email(subject, to_header, body)

    # Get user's timezone for task scheduling
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)

    # Set due time to 4 hours from now
    due_datetime = now + timedelta(hours=4)
    due_date = due_datetime.date().isoformat()
    due_time = due_datetime.strftime('%H:%M') + ':00'

    # Create task title
    task_title = f"{contact_name} - aw bills and site photos"

    # Create the task
    task_data = {
        'user_id': user_id,
        'title': task_title,
        'description': f"Follow up on documents request - original email subject: {subject}",
        'due_date': due_date,
        'due_time': due_time,
        'priority': 'medium',
        'status': 'pending',
        'client_name': contact_name
    }

    try:
        result = supabase.table('tasks').insert(task_data).execute()
        if result.data:
            task = result.data[0]
            print(f"    ✅ Awaiting docs task created: {task_title} (due {due_date} {due_time[:5]})")

            # Send confirmation email
            send_task_confirmation_email(
                user_email=user_email,
                task_title=task_title,
                due_date=due_date,
                due_time=due_time,
                task_id=task['id'],
                user_name=user_name,
                user_id=user_id
            )

            return task
    except Exception as e:
        print(f"    ❌ Failed to create awaiting docs task: {e}")

    return None


def is_customer_outreach_email(to_header, from_email, user):
    """Detect if this is an outreach email CC'd to jottask (user emailing a customer)"""
    from_lower = from_email.lower()

    # Check if the email is FROM the user (primary or alternate email)
    user_emails = [user['email'].lower()]
    if user.get('alternate_emails'):
        user_emails.extend([e.lower() for e in user['alternate_emails']])

    print(f"    🔍 Customer outreach check: from={from_lower}, user_emails={user_emails}, to_header={to_header}")

    if from_lower not in user_emails:
        print(f"    ❌ Not customer outreach: from_email not in user's emails")
        return False

    # Check if TO header contains an external recipient (not jottask)
    if not to_header:
        print(f"    ❌ Not customer outreach: no TO header")
        return False

    to_email = to_header.split('<')[-1].replace('>', '').strip().lower() if '<' in to_header else to_header.lower()

    # Make sure it's going to someone external (not jottask itself)
    if 'jottask' in to_email or 'flowquote' in to_email:
        print(f"    ❌ Not customer outreach: TO contains jottask/flowquote")
        return False

    print(f"    ✅ IS customer outreach email")
    return True


def process_customer_followup_email(user_id, user_email, subject, body, to_header, user_timezone, user_name=None):
    """Process a CC'd customer email - create follow-up task for 1 day later at same time"""
    print(f"    📬 Processing customer follow-up...")

    # Extract contact name from the To field or subject
    contact_name = extract_name_from_email(subject, to_header, body)

    # Get user's timezone for task scheduling
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)

    # Set due time to 1 day later at the same time
    due_datetime = now + timedelta(days=1)
    due_date = due_datetime.date().isoformat()
    due_time = now.strftime('%H:%M') + ':00'  # Same time as email was sent

    # Create task title: "[Name] - no reply try again - Lead"
    task_title = f"{contact_name} - no reply try again - Lead"

    # Create the task
    task_data = {
        'user_id': user_id,
        'title': task_title,
        'description': f"Follow up if no reply - original email subject: {subject}",
        'due_date': due_date,
        'due_time': due_time,
        'priority': 'medium',
        'status': 'pending',
        'client_name': contact_name
    }

    try:
        result = supabase.table('tasks').insert(task_data).execute()
        if result.data:
            task = result.data[0]
            print(f"    ✅ Follow-up task created: {task_title} (due {due_date} {due_time[:5]})")

            # Send confirmation email
            send_task_confirmation_email(
                user_email=user_email,
                task_title=task_title,
                due_date=due_date,
                due_time=due_time,
                task_id=task['id'],
                user_name=user_name,
                user_id=user_id
            )

            return task
    except Exception as e:
        print(f"    ❌ Failed to create follow-up task: {e}")

    return None


def extract_name_from_email(subject, to_header, body):
    """Extract contact name from subject, To header, or body"""
    # Try subject first - format: "Name - sorry I missed you"
    if ' - ' in subject:
        name = subject.split(' - ')[0].strip()
        if len(name) > 1 and len(name) < 50:
            return name

    # Try "Hi Name" pattern from body
    import re
    hi_match = re.search(r'(?:^|\n)\s*(?:Hi|Hello|Hey|Dear)\s+([A-Z][a-z]+)', body)
    if hi_match:
        return hi_match.group(1)

    # Try To header - extract name before email
    if to_header and '<' in to_header:
        name_part = to_header.split('<')[0].strip().strip('"').strip("'")
        if name_part and len(name_part) > 1:
            return name_part

    # Try extracting from To email address
    if to_header:
        to_email = to_header.split('<')[-1].replace('>', '').strip() if '<' in to_header else to_header
        # Get part before @ and capitalize
        name_from_email = to_email.split('@')[0].replace('.', ' ').replace('_', ' ').title()
        if name_from_email and len(name_from_email) > 1:
            return name_from_email

    return "Contact"


def process_missed_call_email(user_id, user_email, subject, body, to_header, user_timezone, user_name=None):
    """Process a missed call CC email - create follow-up task for tomorrow"""
    print(f"    📞 Processing missed call follow-up...")

    # Extract contact name
    contact_name = extract_name_from_email(subject, to_header, body)

    # Get user's timezone for task scheduling
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)
    tomorrow = (now + timedelta(days=1)).date().isoformat()

    # Default to 9 AM tomorrow
    due_time = "09:00:00"

    # Create task title
    task_title = f"{contact_name}, try again - missed call - Lead"

    # Create the task
    task_data = {
        'user_id': user_id,
        'title': task_title,
        'description': f"Follow up call - original email subject: {subject}",
        'due_date': tomorrow,
        'due_time': due_time,
        'priority': 'medium',
        'status': 'pending',
        'client_name': contact_name
    }

    try:
        result = supabase.table('tasks').insert(task_data).execute()
        if result.data:
            task = result.data[0]
            print(f"    ✅ Missed call follow-up created: {task_title}")

            # Send confirmation email
            send_task_confirmation_email(
                user_email=user_email,
                task_title=task_title,
                due_date=tomorrow,
                due_time=due_time,
                task_id=task['id'],
                user_name=user_name,
                user_id=user_id
            )

            return task
    except Exception as e:
        print(f"    ❌ Failed to create missed call task: {e}")

    return None


def process_central_inbox():
    """Process emails from the central Jottask inbox"""
    print(f"\n📧 Processing central inbox: {JOTTASK_EMAIL}")

    if not JOTTASK_PASSWORD:
        print("⚠️ No password configured for Jottask inbox")
        return

    # Connect to IMAP
    imap = connect_to_jottask_inbox()
    if not imap:
        return

    try:
        # Select inbox
        imap.select('INBOX')

        # Search for recent unread emails (last 24 hours)
        date_since = (datetime.now() - timedelta(days=1)).strftime('%d-%b-%Y')
        _, messages = imap.search(None, f'(SINCE {date_since} UNSEEN)')

        email_ids = messages[0].split()
        print(f"📬 Found {len(email_ids)} unread emails")

        # Process newest first, limit to 20
        for email_id in reversed(email_ids[:20]):
            email_id_str = email_id.decode()

            # Fetch email
            _, msg_data = imap.fetch(email_id, '(RFC822)')
            email_body = msg_data[0][1]
            msg = email.message_from_bytes(email_body)

            # Extract sender details
            from_header = msg.get('From', '')
            from_email = from_header.split('<')[-1].replace('>', '').strip() if '<' in from_header else from_header
            from_email = from_email.lower()

            subject = decode_email_subject(msg.get('Subject', ''))
            print(f"  📩 Email from: {from_email} - {subject[:40]}...")

            # Skip emails from the Jottask address itself
            if from_email == JOTTASK_EMAIL.lower():
                print(f"    ⏭️ Skipping (from Jottask address)")
                continue

            # Find user by sender email
            user = get_user_by_email(from_email)

            if not user:
                print(f"    ⚠️ No user found for: {from_email}")
                # Mark as read to avoid reprocessing
                imap.store(email_id, '+FLAGS', '\\Seen')
                continue

            user_id = user['id']
            user_timezone = user.get('timezone', 'Australia/Brisbane')

            # Check if already processed
            if check_if_email_processed(email_id_str, user_id):
                print(f"    ⏭️ Already processed")
                continue

            # Get body
            body = get_email_body(msg)

            # Get To header for CC detection
            to_header = msg.get('To', '')

            # Check if this is a missed call follow-up (CC'd to jottask)
            if is_missed_call_email(subject, body):
                user_profile = supabase.table('users').select('full_name').eq('id', user_id).single().execute()
                user_name = user_profile.data.get('full_name') if user_profile.data else None

                task = process_missed_call_email(
                    user_id=user_id,
                    user_email=user['email'],
                    subject=subject,
                    body=body,
                    to_header=to_header,
                    user_timezone=user_timezone,
                    user_name=user_name
                )
                if task:
                    mark_email_processed(email_id_str, user_id)
                    imap.store(email_id, '+FLAGS', '\\Seen')
                    continue

            # Check if this is requesting bills/photos from a client (CC'd to jottask)
            if is_awaiting_docs_email(subject, body):
                user_profile = supabase.table('users').select('full_name').eq('id', user_id).single().execute()
                user_name = user_profile.data.get('full_name') if user_profile.data else None

                task = process_awaiting_docs_email(
                    user_id=user_id,
                    user_email=user['email'],
                    subject=subject,
                    body=body,
                    to_header=to_header,
                    user_timezone=user_timezone,
                    user_name=user_name
                )
                if task:
                    mark_email_processed(email_id_str, user_id)
                    imap.store(email_id, '+FLAGS', '\\Seen')
                    continue

            # Check if this is a customer outreach email (user CC'd jottask on email to customer)
            # Creates a "no reply try again" follow-up task for 1 day later
            if is_customer_outreach_email(to_header, from_email, user):
                user_profile = supabase.table('users').select('full_name').eq('id', user_id).single().execute()
                user_name = user_profile.data.get('full_name') if user_profile.data else None

                task = process_customer_followup_email(
                    user_id=user_id,
                    user_email=user['email'],
                    subject=subject,
                    body=body,
                    to_header=to_header,
                    user_timezone=user_timezone,
                    user_name=user_name
                )
                if task:
                    mark_email_processed(email_id_str, user_id)
                    imap.store(email_id, '+FLAGS', '\\Seen')
                    continue

            # Check if this is a project email
            if is_project_email(subject):
                # Get user's name for personalization
                user_profile = supabase.table('users').select('full_name').eq('id', user_id).single().execute()
                user_name = user_profile.data.get('full_name') if user_profile.data else None

                project = process_project_email(user_id, user['email'], subject, body, user_name)
                if project:
                    print(f"    ✅ Project processed for {user['email']}: {project.get('name', 'N/A')}")
                else:
                    print(f"    ⚠️ Could not process project email")
            else:
                # Regular task email processing
                analysis = analyze_email_with_ai(subject, body, from_email, user_timezone)

                if analysis.get('is_task'):
                    # If no date/time specified, default to 4 hours from now
                    if not analysis.get('due_date') or not analysis.get('due_time'):
                        tz = pytz.timezone(user_timezone)
                        now = datetime.now(tz)
                        due_datetime = now + timedelta(hours=4)
                        if not analysis.get('due_date'):
                            analysis['due_date'] = due_datetime.date().isoformat()
                        if not analysis.get('due_time'):
                            analysis['due_time'] = due_datetime.strftime('%H:%M')
                        print(f"    ⏰ No date/time specified - defaulting to 4 hours from now: {analysis['due_date']} {analysis['due_time']}")

                    # Create task for this user
                    task = create_task_for_user(user_id, analysis)

                    if task:
                        print(f"    ✅ Task created for {user['email']}: {task.get('title', 'N/A')}")

                        # Send confirmation email
                        send_task_confirmation_email(
                            user_email=user['email'],
                            task_title=task.get('title', 'New Task'),
                            due_date=task.get('due_date', ''),
                            due_time=task.get('due_time', ''),
                            task_id=task.get('id'),
                            user_name=user.get('full_name'),
                            user_id=user_id
                        )

                        # Add email as note to task
                        try:
                            supabase.table('task_notes').insert({
                                'task_id': task['id'],
                                'content': f"Created from email:\n\n{body[:1000]}",
                                'source': 'email',
                                'source_email_subject': subject,
                                'source_email_from': from_email,
                                'created_by': 'system'
                            }).execute()
                        except:
                            pass
                else:
                    print(f"    ℹ️ Not a task: {analysis.get('reason', 'unknown')}")

            # Mark as processed
            mark_email_processed(email_id_str, user_id)

            # Mark email as read
            imap.store(email_id, '+FLAGS', '\\Seen')

    except Exception as e:
        print(f"❌ Error processing inbox: {e}")

    finally:
        try:
            imap.logout()
        except:
            pass


def process_robcrm_inbox():
    """Process emails from the secondary RobCRM Gmail inbox"""
    if not ROBCRM_PASSWORD:
        return  # Skip if not configured

    print(f"\n📧 Processing secondary inbox: {ROBCRM_EMAIL}")

    imap = connect_to_robcrm_inbox()
    if not imap:
        return

    try:
        imap.select('INBOX')
        _, messages = imap.search(None, 'UNSEEN')

        if not messages[0]:
            print("📭 No new emails in RobCRM inbox")
            imap.logout()
            return

        email_ids = messages[0].split()
        print(f"📬 Found {len(email_ids)} new emails in RobCRM inbox")

        for email_id in email_ids:
            try:
                _, msg_data = imap.fetch(email_id, '(RFC822)')
                email_message = email.message_from_bytes(msg_data[0][1])

                from_header = email_message.get('From', '')
                subject = decode_email_subject(email_message.get('Subject', ''))

                # Extract sender email
                if '<' in from_header:
                    from_email = from_header.split('<')[1].split('>')[0].lower()
                else:
                    from_email = from_header.lower()

                # Skip system emails
                if from_email in [ROBCRM_EMAIL.lower(), JOTTASK_EMAIL.lower()]:
                    continue

                print(f"\n📨 Processing from RobCRM: {subject[:50]}...")

                # Get email body
                body = ""
                if email_message.is_multipart():
                    for part in email_message.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                            break
                else:
                    body = email_message.get_payload(decode=True).decode('utf-8', errors='ignore')

                # Find user by email
                user = get_user_by_email(from_email)
                if not user:
                    print(f"⚠️ Unknown sender: {from_email} - skipping")
                    continue

                # Check if project email
                if is_project_email(subject):
                    process_project_email(user, subject, body, from_email)
                else:
                    process_task_email(user, subject, body, from_email)

            except Exception as e:
                print(f"❌ Error processing email: {e}")
                continue

        imap.logout()
        print("✅ RobCRM inbox processing complete")

    except Exception as e:
        print(f"❌ Error processing RobCRM inbox: {e}")
        try:
            imap.logout()
        except:
            pass


def get_action_token(task_id, user_id, action):
    """Get a token for email action links"""
    import requests

    WEB_SERVICE_URL = os.getenv('WEB_SERVICE_URL', 'https://www.jottask.app')
    INTERNAL_API_KEY = os.getenv('INTERNAL_API_KEY', 'jottask-internal-2026')

    try:
        response = requests.post(
            f"{WEB_SERVICE_URL}/api/internal/generate-token",
            json={'task_id': task_id, 'user_id': user_id, 'action': action},
            headers={'X-Internal-Key': INTERNAL_API_KEY},
            timeout=10
        )
        if response.status_code == 200:
            return response.json().get('token')
    except:
        pass
    return None


def send_task_reminder_email(user, task):
    """Send reminder email for a task that's due soon - direct to Resend API"""
    WEB_SERVICE_URL = os.getenv('WEB_SERVICE_URL', 'https://www.jottask.app')

    user_email = user['email']
    user_name = user.get('full_name', '')
    task_title = task.get('title', 'Task')
    task_id = task.get('id')
    due_time = task.get('due_time', '')[:5] if task.get('due_time') else ''
    client_name = task.get('client_name', '')

    # Use query-param format for action URLs (no login required, no token generation needed)
    action_base = f"{WEB_SERVICE_URL}/action"
    complete_url = f"{action_base}?action=complete&task_id={task_id}"
    reschedule_url = f"{action_base}?action=delay_custom&task_id={task_id}"
    delay_1h_url = f"{action_base}?action=delay_1hour&task_id={task_id}"
    delay_1d_url = f"{action_base}?action=delay_1day&task_id={task_id}"

    greeting = f"Hi {user_name}," if user_name else "Hi,"

    html_content = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #EF4444 0%, #F97316 100%); padding: 24px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 24px;">⏰ Task Reminder</h1>
        </div>
        <div style="background: #f9fafb; padding: 24px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 12px 12px;">
            <p style="color: #374151;">{greeting}</p>
            <p style="color: #374151;">Your task is due now:</p>
            <div style="background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin: 16px 0;">
                <h3 style="margin: 0 0 8px 0; color: #111827;">{task_title}</h3>
                <p style="margin: 0; color: #EF4444; font-size: 14px; font-weight: 600;">Due: {due_time} AEST</p>
                {f'<p style="margin: 8px 0 0 0; color: #6b7280; font-size: 14px;">Client: {client_name}</p>' if client_name else ''}
            </div>
            <div style="margin-top: 16px; text-align: center;">
                <a href="{complete_url}" style="display: inline-block; background: #10B981; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">✅ Complete</a>
                <a href="{delay_1h_url}" style="display: inline-block; background: #6B7280; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">⏰ +1 Hour</a>
                <a href="{delay_1d_url}" style="display: inline-block; background: #6B7280; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">📅 +1 Day</a>
                <a href="{reschedule_url}" style="display: inline-block; background: #6366F1; color: white; padding: 12px 20px; border-radius: 8px; text-decoration: none; margin: 4px; font-weight: 600;">🗓️ Change Time</a>
            </div>
        </div>
    </body>
    </html>
    """

    # Send directly via Resend API (bypasses web service)
    return send_email_direct(user_email, f"⏰ Reminder: {task_title}", html_content)


def check_and_send_reminders():
    """Check for tasks due soon and send reminders"""
    print(f"\n🔔 Checking for tasks due soon...")

    try:
        # Get all pending tasks with due times (check multiple timezones)
        result = supabase.table('tasks')\
            .select('*, users!tasks_user_id_fkey(id, email, full_name, timezone)')\
            .eq('status', 'pending')\
            .not_.is_('due_time', 'null')\
            .execute()

        tasks = result.data or []

        if not tasks:
            print("    No pending tasks with due times")
            return

        sent_count = 0

        for task in tasks:
            try:
                user = task.get('users')
                if not user:
                    continue

                # Get user's timezone (default to Brisbane)
                user_tz = pytz.timezone(user.get('timezone', 'Australia/Brisbane'))
                now = datetime.now(user_tz)
                today_str = now.date().isoformat()

                # Skip if task is not due today in user's timezone
                if task.get('due_date') != today_str:
                    continue

                # Parse due time
                due_time_str = task['due_time']
                parts = due_time_str.split(':')
                hour, minute = int(parts[0]), int(parts[1])

                task_due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                time_diff = (task_due - now).total_seconds() / 60

                # Send reminder if within 5-20 minute window
                if -5 <= time_diff <= 20:
                    # Check if reminder already sent today
                    if task.get('reminder_sent_at'):
                        try:
                            sent_at = datetime.fromisoformat(task['reminder_sent_at'].replace('Z', '+00:00'))
                            if sent_at.astimezone(user_tz).date() == now.date():
                                continue  # Already sent today
                        except:
                            pass

                    print(f"    📧 Sending reminder: {task['title'][:40]}...")

                    if send_task_reminder_email(user, task):
                        # Mark reminder as sent
                        supabase.table('tasks').update({
                            'reminder_sent_at': now.isoformat()
                        }).eq('id', task['id']).execute()
                        sent_count += 1

                    time.sleep(0.5)  # Rate limit

            except Exception as e:
                print(f"    ⚠️ Error with task {task.get('id')}: {e}")
                continue

        if sent_count > 0:
            print(f"    ✅ Sent {sent_count} reminder(s)")
        else:
            print("    No reminders needed right now")

    except Exception as e:
        print(f"❌ Reminder check error: {e}")


def run_email_processor():
    """Main processor loop - runs continuously"""
    print("🚀 Starting Jottask Central Inbox Processor")
    print(f"📧 Monitoring: {JOTTASK_EMAIL}")
    if ROBCRM_PASSWORD:
        print(f"📧 Also monitoring: {ROBCRM_EMAIL}")
    print("🔔 Task reminders enabled")
    print("=" * 50)

    last_reminder_check = 0

    while True:
        try:
            print(f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            # Process the central Jottask inbox
            process_central_inbox()

            # Also process RobCRM inbox if configured
            process_robcrm_inbox()

            # Check for reminders every minute
            current_time = time.time()
            if current_time - last_reminder_check >= 60:
                check_and_send_reminders()
                last_reminder_check = current_time

            print(f"\n😴 Sleeping for 1 minute...")
            time.sleep(60)  # Check every minute for better reminder timing

        except KeyboardInterrupt:
            print("\n👋 Shutting down email processor...")
            break
        except Exception as e:
            print(f"❌ Processor error: {e}")
            time.sleep(60)  # Wait 1 minute on error


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    run_email_processor()
