#!/usr/bin/env python3
"""
Install Order Automation — OpenSolar "Customer Accepted" → WhatsApp Draft

Phase 1: Parses OpenSolar acceptance notification emails, looks up CRM/task
context by address, and sends a pre-formatted WhatsApp draft email to the user.
Equipment list is a placeholder until OpenSolar scraping/API is added.
"""

import re
import os
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
import pytz
import resend


# =========================================================================
# DATA MODELS
# =========================================================================

@dataclass
class OpenSolarNotification:
    """Parsed data from an OpenSolar 'Customer Accepted' email"""
    project_id: str
    address: str
    project_link: str
    raw_subject: str
    raw_body: str


@dataclass
class CRMContext:
    """Context gathered from existing tasks/CRM for this address"""
    customer_name: Optional[str] = None
    client_email: Optional[str] = None
    task_titles: List[str] = field(default_factory=list)
    notes_summary: Optional[str] = None


# =========================================================================
# PARSING — extract project details from OpenSolar email (no AI needed)
# =========================================================================

# OpenSolar senders
OPENSOLAR_SENDERS = [
    'noreply@opensolar.com',
    'no-reply@opensolar.com',
    'notifications@opensolar.com',
]

ACCEPTED_SUBJECT_PATTERN = re.compile(
    r'customer\s+accepted\s+online\s+proposal',
    re.IGNORECASE
)

# Project link pattern: https://app.opensolar.com/projects/NNNNNNN
PROJECT_LINK_RE = re.compile(
    r'https?://app\.opensolar\.com/projects/(\d+)'
)

# Address pattern — OpenSolar emails typically include the project address
# on its own line or after "Project:" / "Address:" label
ADDRESS_LINE_RE = re.compile(
    r'(?:Project|Address|Location)\s*[:\-]\s*(.+)',
    re.IGNORECASE
)

# Fallback: grab a line that looks like a street address (number + street name)
STREET_ADDRESS_RE = re.compile(
    r'(\d+\s+[\w\s]+(?:St|Street|Rd|Road|Ave|Avenue|Dr|Drive|Ct|Court|Cres|Crescent|Pl|Place|Blvd|Way|Ln|Lane|Tce|Terrace|Pde|Parade|Cct|Circuit)[\w\s,\-]*)',
    re.IGNORECASE
)


def is_opensolar_accepted(sender_raw: str, subject: str) -> bool:
    """Check if this email is an OpenSolar 'Customer Accepted' notification"""
    sender_lower = sender_raw.lower()
    sender_match = any(addr in sender_lower for addr in OPENSOLAR_SENDERS)
    subject_match = bool(ACCEPTED_SUBJECT_PATTERN.search(subject))
    return sender_match and subject_match


def parse_opensolar_email(subject: str, body: str) -> Optional[OpenSolarNotification]:
    """Extract project ID, address, and link from an OpenSolar acceptance email.
    Returns None if the email can't be parsed."""

    # Extract project link and ID
    link_match = PROJECT_LINK_RE.search(body)
    if not link_match:
        # Also check subject (some forwarded formats put the link there)
        link_match = PROJECT_LINK_RE.search(subject)
    if not link_match:
        return None

    project_id = link_match.group(1)
    project_link = link_match.group(0)

    # Extract address
    address = _extract_address(body)

    return OpenSolarNotification(
        project_id=project_id,
        address=address or f'Project {project_id}',
        project_link=project_link,
        raw_subject=subject,
        raw_body=body,
    )


def _extract_address(body: str) -> Optional[str]:
    """Try multiple patterns to extract the project address from email body"""
    # Try labelled address first
    match = ADDRESS_LINE_RE.search(body)
    if match:
        addr = match.group(1).strip()
        # Clean trailing whitespace/newlines
        addr = addr.split('\n')[0].strip()
        if len(addr) > 5:
            return addr

    # Fallback: street address pattern
    match = STREET_ADDRESS_RE.search(body)
    if match:
        addr = match.group(1).strip()
        addr = addr.split('\n')[0].strip()
        if len(addr) > 5:
            return addr

    return None


# =========================================================================
# CRM/TASK LOOKUP — find customer context by address keywords
# =========================================================================

def lookup_crm_by_address(user_id: str, address: str, tm) -> CRMContext:
    """Search existing tasks for a matching address/suburb to find customer context.

    Args:
        user_id: The user's UUID
        address: Project address from OpenSolar email
        tm: TaskManager instance (for Supabase access)

    Returns:
        CRMContext with whatever we could find
    """
    ctx = CRMContext()

    if not address or not tm:
        return ctx

    # Extract search keywords from address (suburb, street name)
    keywords = _address_keywords(address)
    if not keywords:
        return ctx

    for keyword in keywords:
        if len(keyword) < 4:
            continue
        try:
            result = tm.supabase.table('tasks') \
                .select('id, title, client_name, client_email, description') \
                .eq('user_id', user_id) \
                .ilike('title', f'%{keyword}%') \
                .order('created_at', desc=True) \
                .limit(5) \
                .execute()

            if result.data:
                for task in result.data:
                    if task.get('client_name') and not ctx.customer_name:
                        ctx.customer_name = task['client_name']
                    if task.get('client_email') and not ctx.client_email:
                        ctx.client_email = task['client_email']
                    title = task.get('title', '')
                    if title and title not in ctx.task_titles:
                        ctx.task_titles.append(title)

                if ctx.customer_name:
                    break  # Found what we need
        except Exception as e:
            print(f"  [OPENSOLAR] CRM lookup error for '{keyword}': {e}")
            continue

    # If no title match, try description/address fields
    if not ctx.customer_name:
        for keyword in keywords:
            if len(keyword) < 4:
                continue
            try:
                result = tm.supabase.table('tasks') \
                    .select('id, title, client_name, client_email') \
                    .eq('user_id', user_id) \
                    .ilike('description', f'%{keyword}%') \
                    .order('created_at', desc=True) \
                    .limit(3) \
                    .execute()

                if result.data:
                    for task in result.data:
                        if task.get('client_name') and not ctx.customer_name:
                            ctx.customer_name = task['client_name']
                        if task.get('client_email') and not ctx.client_email:
                            ctx.client_email = task['client_email']

                    if ctx.customer_name:
                        break
            except Exception:
                continue

    return ctx


def _address_keywords(address: str) -> List[str]:
    """Extract meaningful search keywords from an address.
    Returns suburb and street name as separate keywords."""
    if not address:
        return []

    keywords = []

    # Split by common delimiters
    parts = re.split(r'[,\-]', address)

    for part in parts:
        part = part.strip()
        # Remove street numbers
        cleaned = re.sub(r'^\d+\s*', '', part).strip()
        # Remove street type suffixes for a broader match
        name_only = re.sub(
            r'\b(St|Street|Rd|Road|Ave|Avenue|Dr|Drive|Ct|Court|Cres|Crescent|Pl|Place|Blvd|Way|Ln|Lane|Tce|Terrace|Pde|Parade|Cct|Circuit)\b',
            '', cleaned, flags=re.IGNORECASE
        ).strip()

        if name_only and len(name_only) >= 4:
            keywords.append(name_only)
        if cleaned and cleaned != name_only and len(cleaned) >= 4:
            keywords.append(cleaned)

    return keywords


# =========================================================================
# FORMAT — build WhatsApp draft and notification email
# =========================================================================

def format_install_order_draft(
    notification: OpenSolarNotification,
    crm_context: CRMContext,
    equipment: Optional[List[str]] = None,
) -> str:
    """Format a WhatsApp-ready install order draft.

    Args:
        notification: Parsed OpenSolar email data
        crm_context: Customer context from CRM/task lookup
        equipment: List of equipment strings (None in Phase 1)

    Returns:
        WhatsApp-formatted plain text string
    """
    customer_name = crm_context.customer_name or '[CUSTOMER NAME]'

    # Equipment section
    if equipment:
        equipment_lines = '\n'.join(equipment)
    else:
        equipment_lines = '[EQUIPMENT — open OpenSolar to copy equipment list]'

    # Build the WhatsApp message
    lines = [
        f'*Install Order*',
        f'',
        f'*Customer:* {customer_name}',
        f'*Project:* {notification.address}',
        f'*OpenSolar:* {notification.project_link}',
        f'',
        f'*Equipment:*',
        equipment_lines,
        f'',
    ]

    # Add CRM context if we found related tasks
    if crm_context.task_titles:
        lines.append('*Notes from pipeline:*')
        for title in crm_context.task_titles[:3]:
            lines.append(f'- {title}')
        lines.append('')

    return '\n'.join(lines)


def build_install_order_email(
    notification: OpenSolarNotification,
    whatsapp_draft: str,
    crm_context: CRMContext,
    user_name: str = 'User',
) -> str:
    """Build HTML email with the WhatsApp draft and OpenSolar button.

    Args:
        notification: Parsed OpenSolar data
        whatsapp_draft: Pre-formatted WhatsApp text
        crm_context: CRM context for display
        user_name: Recipient's name

    Returns:
        HTML email string
    """
    customer_display = crm_context.customer_name or 'Unknown Customer'
    has_placeholders = '[CUSTOMER NAME]' in whatsapp_draft or '[EQUIPMENT' in whatsapp_draft

    placeholder_banner = ''
    if has_placeholders:
        placeholder_banner = """
        <div style="background: #fef3c7; border: 1px solid #f59e0b; border-radius: 6px; padding: 12px 16px; margin-bottom: 16px; font-size: 14px; color: #92400e;">
            This draft has placeholders — open OpenSolar to fill in the customer name and equipment list.
        </div>
        """

    # Escape the draft for HTML display but preserve line breaks
    draft_html = whatsapp_draft.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    draft_html = draft_html.replace('\n', '<br>')
    # Render *bold* markers as actual bold
    draft_html = re.sub(r'\*([^*]+)\*', r'<strong>\1</strong>', draft_html)

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1e3a5f; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
            <h2 style="margin: 0; font-size: 18px;">Install Order — {notification.address}</h2>
        </div>

        <div style="padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 8px 8px;">
            <div style="font-size: 14px; color: #666; margin-bottom: 16px;">
                <strong>Customer:</strong> {customer_display}<br>
                <strong>Project ID:</strong> {notification.project_id}<br>
                <strong>Source:</strong> OpenSolar — Customer Accepted Online Proposal
            </div>

            {placeholder_banner}

            <div style="margin-bottom: 16px;">
                <div style="font-size: 13px; font-weight: 600; color: #333; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px;">
                    WhatsApp Draft — copy and send to install team:
                </div>
                <div style="background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 16px; font-family: monospace; font-size: 13px; line-height: 1.6; white-space: pre-wrap; color: #1a1a1a;">
                    {draft_html}
                </div>
            </div>

            <div style="text-align: center; margin: 20px 0;">
                <a href="{notification.project_link}"
                   style="display: inline-block; padding: 12px 28px; background: #f97316; color: white; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 15px;">
                    Open in OpenSolar
                </a>
            </div>

            <div style="font-size: 12px; color: #999; margin-top: 20px; text-align: center;">
                {user_name}'s AI Task Manager &bull; jottask.app
            </div>
        </div>
    </div>
    """


def send_install_order_email(
    recipient_email: str,
    notification: OpenSolarNotification,
    whatsapp_draft: str,
    crm_context: CRMContext,
    user_name: str = 'User',
):
    """Send the install order notification email via Resend.

    Args:
        recipient_email: Where to send the notification
        notification: Parsed OpenSolar data
        whatsapp_draft: Formatted WhatsApp text
        crm_context: CRM context
        user_name: User's display name
    """
    resend.api_key = os.getenv('RESEND_API_KEY')
    from_email = os.getenv('FROM_EMAIL', 'admin@flowquote.ai')

    html = build_install_order_email(notification, whatsapp_draft, crm_context, user_name)

    customer_display = crm_context.customer_name or notification.address
    subject = f"Install Order: {customer_display} — {notification.address}"

    try:
        params = {
            "from": from_email,
            "to": [recipient_email],
            "subject": subject,
            "html": html,
        }
        response = resend.Emails.send(params)
        print(f"  [OPENSOLAR] Install order email sent to {recipient_email} (Resend: {response})")
        return True
    except Exception as e:
        print(f"  [OPENSOLAR] Error sending install order email: {e}")
        return False
