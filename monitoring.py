"""
Jottask System Monitoring
Tracks emails, heartbeats, errors. Alerts admin when things break.
"""

import os
import traceback
from datetime import datetime, timedelta
import pytz

from supabase import create_client, Client

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

_supabase = None

def _get_supabase():
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def log_event(event_type, message, status='info', category=None, error_detail=None, metadata=None, user_id=None):
    """Fire-and-forget event logger. Never crashes the caller."""
    try:
        row = {
            'event_type': event_type,
            'message': message,
            'status': status,
            'category': category,
            'error_detail': error_detail,
            'metadata': metadata or {},
            'user_id': user_id,
        }
        _get_supabase().table('system_events').insert(row).execute()
    except Exception as e:
        print(f"[monitoring] Failed to log event: {e}")


def log_email_send(success, to_email, subject, category=None, user_id=None, task_id=None, error=None):
    """Log an email send attempt."""
    meta = {'to_email': to_email, 'subject': subject[:100]}
    if task_id:
        meta['task_id'] = task_id

    if success:
        log_event(
            'email_sent',
            f"Email sent to {to_email}: {subject[:60]}",
            status='success',
            category=category,
            metadata=meta,
            user_id=user_id,
        )
    else:
        log_event(
            'email_failed',
            f"Failed to send to {to_email}: {error}",
            status='error',
            category=category,
            error_detail=error,
            metadata=meta,
            user_id=user_id,
        )


def log_heartbeat(tick, emails_processed=0, reminders_sent=0, summaries_sent=0, errors=0):
    """Record a worker heartbeat with stats."""
    meta = {
        'tick': tick,
        'emails_processed': emails_processed,
        'reminders_sent': reminders_sent,
        'summaries_sent': summaries_sent,
        'errors': errors,
    }
    log_event('heartbeat', f"Worker tick #{tick}", status='info', category='system', metadata=meta)


def log_error(context, exception, category='system', user_id=None):
    """Capture an exception with traceback."""
    tb = traceback.format_exc()
    log_event(
        'error',
        f"Error in {context}: {str(exception)[:200]}",
        status='error',
        category=category,
        error_detail=tb if tb != 'NoneType: None\n' else str(exception),
        user_id=user_id,
    )


def get_system_health():
    """Query health data for dashboard/API. Returns dict."""
    try:
        sb = _get_supabase()
        now = datetime.now(pytz.UTC)
        since_24h = (now - timedelta(hours=24)).isoformat()

        # Last heartbeat
        hb = sb.table('system_events')\
            .select('created_at, metadata')\
            .eq('event_type', 'heartbeat')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()

        last_heartbeat = None
        heartbeat_age_minutes = None
        if hb.data:
            last_heartbeat = hb.data[0]['created_at']
            hb_dt = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
            heartbeat_age_minutes = (now - hb_dt).total_seconds() / 60

        # Emails sent/failed in last 24h
        sent = sb.table('system_events')\
            .select('id', count='exact')\
            .eq('event_type', 'email_sent')\
            .gte('created_at', since_24h)\
            .execute()

        failed = sb.table('system_events')\
            .select('id', count='exact')\
            .eq('event_type', 'email_failed')\
            .gte('created_at', since_24h)\
            .execute()

        # Errors in last 24h
        errors = sb.table('system_events')\
            .select('id', count='exact')\
            .eq('event_type', 'error')\
            .gte('created_at', since_24h)\
            .execute()

        # Determine overall status
        if heartbeat_age_minutes is None or heartbeat_age_minutes > 15:
            worker_status = 'down'
        elif heartbeat_age_minutes > 5:
            worker_status = 'delayed'
        else:
            worker_status = 'healthy'

        return {
            'worker_status': worker_status,
            'last_heartbeat': last_heartbeat,
            'heartbeat_age_minutes': round(heartbeat_age_minutes, 1) if heartbeat_age_minutes else None,
            'emails_sent_24h': sent.count or 0,
            'emails_failed_24h': failed.count or 0,
            'errors_24h': errors.count or 0,
        }
    except Exception as e:
        print(f"[monitoring] get_system_health error: {e}")
        return {
            'worker_status': 'unknown',
            'last_heartbeat': None,
            'heartbeat_age_minutes': None,
            'emails_sent_24h': 0,
            'emails_failed_24h': 0,
            'errors_24h': 0,
        }


def send_self_alert(subject, detail):
    """Email admin when the system is broken. Throttled: max 3/day, min 30 min apart."""
    try:
        from email_utils import send_email as _send

        sb = _get_supabase()
        admin_id = 'e515407e-dbd6-4331-a815-1878815c89bc'
        now = datetime.now(pytz.UTC)

        # Check throttle
        user = sb.table('users').select('last_system_alert_at, system_alert_count_today').eq('id', admin_id).execute()
        if user.data:
            u = user.data[0]
            last_alert = u.get('last_system_alert_at')
            count_today = u.get('system_alert_count_today') or 0

            if last_alert:
                last_dt = datetime.fromisoformat(last_alert.replace('Z', '+00:00'))
                if (now - last_dt).total_seconds() < 1800:  # 30 min
                    print(f"[monitoring] Alert throttled (too recent)")
                    return
                # Reset count if new day
                if last_dt.date() != now.date():
                    count_today = 0

            if count_today >= 3:
                print(f"[monitoring] Alert throttled (3/day limit)")
                return

        # Send alert
        admin_email = os.getenv('ADMIN_EMAIL', 'admin@flowquote.ai')
        html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: #DC2626; color: white; padding: 16px 24px; border-radius: 12px 12px 0 0;">
                <h2 style="margin: 0;">Jottask System Alert</h2>
            </div>
            <div style="background: white; padding: 24px; border: 1px solid #E5E7EB; border-radius: 0 0 12px 12px;">
                <p style="font-weight: 600; font-size: 16px;">{subject}</p>
                <pre style="background: #F3F4F6; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 13px;">{detail[:2000]}</pre>
                <p style="color: #6B7280; font-size: 13px; margin-top: 16px;">
                    Time: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}<br>
                    <a href="https://www.jottask.app/health">Check system health</a>
                </p>
            </div>
        </div>
        """
        success, err = _send(admin_email, f"[ALERT] {subject}", html)

        # Update throttle counters
        sb.table('users').update({
            'last_system_alert_at': now.isoformat(),
            'system_alert_count_today': (count_today if user.data else 0) + 1,
        }).eq('id', admin_id).execute()

        # Log the alert itself
        log_event('alert_sent', f"Self-alert: {subject}", status='warning', category='system')

    except Exception as e:
        print(f"[monitoring] Failed to send self-alert: {e}")


def cleanup_old_events(days=30):
    """Delete events older than N days."""
    try:
        cutoff = (datetime.now(pytz.UTC) - timedelta(days=days)).isoformat()
        _get_supabase().table('system_events')\
            .delete()\
            .lt('created_at', cutoff)\
            .execute()
        print(f"[monitoring] Cleaned up events older than {days} days")
    except Exception as e:
        print(f"[monitoring] Cleanup failed: {e}")
