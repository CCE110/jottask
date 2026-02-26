"""
Test that every email send path routes through email_utils.send_email().

Each test mocks send_email and triggers the code path that should call it,
verifying it gets called with the expected category. This catches regressions
like "someone removed the send_email call" or "the import broke".
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timedelta

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# 1. Worker: task creation sends confirmation email
# ---------------------------------------------------------------------------

@patch('email_utils.send_email', return_value=(True, None))
def test_worker_task_creation_sends_confirmation(mock_send, mock_supabase):
    """When the worker creates a task, it should send a confirmation email."""
    from saas_email_processor import AIEmailProcessor, UserContext

    processor = AIEmailProcessor()

    # Mock Supabase insert to return a fake task
    fake_task = {
        'id': 'task-111',
        'title': 'Call John Smith',
        'due_date': '2026-02-25',
        'due_time': '09:00:00',
    }
    mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[fake_task])
    # Mock find_existing_task_by_client to return None (no duplicate)
    processor.tm.find_existing_task_by_client = MagicMock(return_value=None)

    user_ctx = UserContext(
        user_id='user-1',
        email_address='rob@example.com',
        full_name='Rob',
        businesses={'DSW': 'biz-1'},
    )

    action = {
        'action_type': 'create_task',
        'title': 'Call John Smith',
        'business': 'DSW',
        'priority': 'high',
        'due_date': '2026-02-25',
        'due_time': '09:00',
    }

    processor._create_task(action, user_context=user_ctx)

    # Verify send_email was called with category='confirmation'
    assert mock_send.called, "send_email was never called for task confirmation"
    confirmation_calls = [c for c in mock_send.call_args_list if c.kwargs.get('category') == 'confirmation']
    assert len(confirmation_calls) >= 1, f"Expected confirmation email, got categories: {[c.kwargs.get('category') for c in mock_send.call_args_list]}"
    assert confirmation_calls[0][0][0] == 'rob@example.com'


# ---------------------------------------------------------------------------
# 2. Worker: approval email for Tier 2 actions
# ---------------------------------------------------------------------------

@patch('email_utils.send_email', return_value=(True, None))
def test_worker_approval_sends_email(mock_send, mock_supabase):
    """send_approval_email() should route through email_utils.send_email()."""
    from saas_email_processor import AIEmailProcessor, UserContext

    processor = AIEmailProcessor()

    user_ctx = UserContext(
        user_id='user-2',
        email_address='rob@example.com',
        full_name='Rob',
        businesses={'DSW': 'biz-1'},
    )

    actions = [{
        'action_type': 'update_crm',
        'title': 'Update CRM for John',
        'customer_name': 'John Smith',
        'crm_notes': 'Called, going with 10kW system',
    }]

    processor.send_approval_email(
        email_subject='Test Subject',
        email_sender='john@example.com',
        actions=actions,
        context='Test context',
        user_context=user_ctx,
    )

    assert mock_send.called, "send_email was never called for approval email"
    # Check category
    sent_category = mock_send.call_args.kwargs.get('category')
    assert sent_category == 'approval', f"Expected category='approval', got '{sent_category}'"
    # Check recipient
    assert mock_send.call_args[0][0] == 'rob@example.com'


# ---------------------------------------------------------------------------
# 3. Scheduler: reminder email
# ---------------------------------------------------------------------------

@patch('email_utils.send_email', return_value=(True, None))
def test_reminder_sends_email(mock_send, mock_supabase):
    """check_and_send_reminders() should send reminders via email_utils.send_email()."""
    import saas_scheduler
    # Reset the module-level _supabase so it uses our mock
    saas_scheduler._supabase = mock_supabase

    import pytz
    aest = pytz.timezone('Australia/Brisbane')
    now = datetime.now(aest)

    # Mock users query
    mock_supabase.table.return_value.select.return_value.execute.return_value = MagicMock(data=[{
        'id': 'user-3',
        'email': 'rob@example.com',
        'full_name': 'Rob',
        'timezone': 'Australia/Brisbane',
        'reminder_minutes_before': 30,
    }])

    # We need more granular mocking: users query vs tasks query
    # Use side_effect to return different data for different .table() calls
    user_data = [{
        'id': 'user-3',
        'email': 'rob@example.com',
        'full_name': 'Rob',
        'timezone': 'Australia/Brisbane',
        'reminder_minutes_before': 30,
    }]

    # Task due 10 minutes from now (within reminder window)
    due_time = (now + timedelta(minutes=10)).strftime('%H:%M:%S')
    task_data = [{
        'id': 'task-222',
        'title': 'Call Jane',
        'due_date': now.strftime('%Y-%m-%d'),
        'due_time': due_time,
        'priority': 'high',
        'status': 'pending',
        'client_name': 'Jane Doe',
        'user_id': 'user-3',
        'reminder_sent_at': None,
    }]

    # Track which table is being queried
    call_count = {'n': 0}
    original_table = mock_supabase.table

    def table_router(name):
        mock_chain = MagicMock()
        if name == 'users':
            mock_chain.select.return_value.execute.return_value = MagicMock(data=user_data)
        elif name == 'tasks':
            call_count['n'] += 1
            if call_count['n'] == 1:
                # First tasks call: the main query
                mock_chain.select.return_value.eq.return_value.is_.return_value.execute.return_value = MagicMock(data=task_data)
            else:
                # Update call
                mock_chain.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        return mock_chain

    mock_supabase.table = table_router

    result = saas_scheduler.check_and_send_reminders()

    assert mock_send.called, "send_email was never called for reminder"
    reminder_calls = [c for c in mock_send.call_args_list if c.kwargs.get('category') == 'reminder']
    assert len(reminder_calls) >= 1, f"Expected reminder email, got: {mock_send.call_args_list}"

    # Cleanup
    saas_scheduler._supabase = None


# ---------------------------------------------------------------------------
# 4. Scheduler: daily summary email
# ---------------------------------------------------------------------------

@patch('saas_scheduler.send_email', return_value=(True, None))
def test_daily_summary_sends_email(mock_send, mock_supabase):
    """send_daily_summary() should send via email_utils.send_email()."""
    import saas_scheduler
    saas_scheduler._supabase = mock_supabase

    # Mock the helper functions to return non-empty data (avoids early return)
    with patch.object(saas_scheduler, 'get_user_tasks_summary', return_value={
        'overdue': [{'id': 't1', 'title': 'Overdue task', 'due_date': '2026-02-24', 'due_time': '09:00', 'priority': 'high', 'client_name': 'Bob'}],
        'due_today': [],
        'upcoming': [],
        'total_pending': 1,
    }), patch.object(saas_scheduler, 'get_user_projects_summary', return_value={
        'projects': [],
        'total_items_remaining': 0,
        'active_count': 0,
    }):
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        user = {
            'id': 'user-4',
            'email': 'rob@example.com',
            'full_name': 'Rob',
            'timezone': 'Australia/Brisbane',
        }

        saas_scheduler.send_daily_summary(user)

    assert mock_send.called, "send_email was never called for daily summary"
    summary_calls = [c for c in mock_send.call_args_list if c.kwargs.get('category') == 'summary']
    assert len(summary_calls) >= 1, f"Expected summary email, got: {mock_send.call_args_list}"

    # Cleanup
    saas_scheduler._supabase = None


# ---------------------------------------------------------------------------
# 5. Install order email
# ---------------------------------------------------------------------------

@patch('email_utils.send_email', return_value=(True, None))
def test_install_order_sends_email(mock_send, mock_supabase):
    """send_install_order_email() should route through email_utils.send_email()."""
    from install_order import (
        send_install_order_email, OpenSolarNotification, CRMContext
    )

    notification = OpenSolarNotification(
        project_id='12345',
        address='42 Solar St, Brisbane',
        project_link='https://app.opensolar.com/projects/12345',
        raw_subject='Customer Accepted Online Proposal',
        raw_body='test body',
    )
    crm_context = CRMContext(customer_name='Jane Doe')

    result = send_install_order_email(
        recipient_email='rob@example.com',
        notification=notification,
        whatsapp_draft='*Install Order*\ntest',
        crm_context=crm_context,
        user_name='Rob',
    )

    assert result is True
    assert mock_send.called, "send_email was never called for install order"
    sent_category = mock_send.call_args.kwargs.get('category')
    assert sent_category == 'install_order', f"Expected category='install_order', got '{sent_category}'"


# ---------------------------------------------------------------------------
# 6. Delayed task resets reminder_sent_at
# ---------------------------------------------------------------------------

def test_delayed_task_resets_reminder(mock_supabase):
    """When a task is delayed (via action button), reminder_sent_at should be reset to None
    so the scheduler sends a new reminder for the new time."""
    # This tests the delay logic in dashboard.py's action endpoint
    # The key behavior: any delay/reschedule must set reminder_sent_at = None
    # We verify by checking the approval_routes or dashboard action handler

    # delay_task may live in approval_routes.py (which requires Flask app context)
    # or inline in dashboard.py. Either way, verify the code sets reminder_sent_at = None.
    dashboard_path = os.path.join(os.path.dirname(__file__), '..', 'dashboard.py')
    with open(dashboard_path, 'r') as f:
        source = f.read()

    # The delay/reschedule handler must reset reminder_sent_at so the scheduler
    # sends a fresh reminder for the new time
    assert 'reminder_sent_at' in source, (
        "dashboard.py should reference reminder_sent_at in delay/reschedule logic"
    )
    # Verify it's actually being set to None (not just read)
    assert "'reminder_sent_at': None" in source or '"reminder_sent_at": None' in source, (
        "dashboard.py should set reminder_sent_at to None when delaying a task"
    )


# ---------------------------------------------------------------------------
# 7. Canary email tests
# ---------------------------------------------------------------------------

@patch('monitoring.datetime')
@patch('email_utils.send_email', return_value=(True, None))
def test_canary_sends_at_7am(mock_send, mock_dt, mock_supabase):
    """Canary email should send during the 7 AM AEST window."""
    import monitoring
    monitoring._supabase = mock_supabase

    import pytz
    aest = pytz.timezone('Australia/Brisbane')
    fake_now_aest = aest.localize(datetime(2026, 2, 26, 7, 1, 0))
    fake_now_utc = fake_now_aest.astimezone(pytz.UTC)

    mock_dt.now = MagicMock(side_effect=lambda tz: fake_now_aest if str(tz) == 'Australia/Brisbane' else fake_now_utc)
    mock_dt.fromisoformat = datetime.fromisoformat

    # No existing canary today
    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(data=[])
    # Admin user
    mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{'id': 'admin-1', 'email': 'admin@example.com'}]
    )

    result = monitoring.check_and_send_canary()

    assert result == 'sent'
    assert mock_send.called
    assert mock_send.call_args.kwargs.get('category') == 'canary'

    monitoring._supabase = None


@patch('monitoring.datetime')
@patch('email_utils.send_email', return_value=(True, None))
def test_canary_skips_outside_window(mock_send, mock_dt, mock_supabase):
    """Canary should skip when outside the 7 AM / 5 PM windows."""
    import monitoring
    import pytz
    aest = pytz.timezone('Australia/Brisbane')
    fake_now = aest.localize(datetime(2026, 2, 26, 10, 0, 0))
    mock_dt.now = MagicMock(return_value=fake_now)

    result = monitoring.check_and_send_canary()

    assert result == 'skipped'
    assert not mock_send.called

    monitoring._supabase = None


@patch('monitoring.datetime')
@patch('email_utils.send_email', return_value=(True, None))
def test_canary_skips_if_already_sent(mock_send, mock_dt, mock_supabase):
    """Canary should skip if one was already sent in this window."""
    import monitoring
    monitoring._supabase = mock_supabase

    import pytz
    aest = pytz.timezone('Australia/Brisbane')
    fake_now_aest = aest.localize(datetime(2026, 2, 26, 7, 2, 0))
    fake_now_utc = fake_now_aest.astimezone(pytz.UTC)

    mock_dt.now = MagicMock(side_effect=lambda tz: fake_now_aest if str(tz) == 'Australia/Brisbane' else fake_now_utc)
    mock_dt.fromisoformat = datetime.fromisoformat

    # Existing canary found
    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.gte.return_value.execute.return_value = MagicMock(
        data=[{'id': 'evt-1'}]
    )

    result = monitoring.check_and_send_canary()

    assert result == 'skipped'
    assert not mock_send.called

    monitoring._supabase = None


@patch('monitoring.get_last_canary_status', return_value={'status': 'failed', 'last_canary': '2026-02-26T07:01:00+00:00', 'error': 'API key expired'})
@patch('monitoring.get_system_health', return_value={'worker_status': 'healthy', 'last_heartbeat': '2026-02-26T07:00:00Z', 'heartbeat_age_minutes': 2.0, 'emails_sent_24h': 5, 'emails_failed_24h': 0, 'errors_24h': 0})
def test_health_returns_503_on_canary_failure(mock_health, mock_canary, client):
    """/health should return 503 when canary status is 'failed'."""
    resp = client.get('/health')
    assert resp.status_code == 503
    data = resp.get_json()
    assert data['canary_status'] == 'failed'


# ---------------------------------------------------------------------------
# 8. Email processing outcome tracking
# ---------------------------------------------------------------------------

@patch('email_utils.send_email', return_value=(True, None))
def test_process_email_returns_task_created_outcome(mock_send, mock_supabase):
    """process_single_email_body should return ('task_created', ...) when AI returns actions."""
    from saas_email_processor import AIEmailProcessor, UserContext
    from email.message import EmailMessage

    processor = AIEmailProcessor()

    # Mock AI response with a create_task action
    ai_response = {
        'summary': 'New lead from John',
        'actions': [{
            'action_type': 'create_task',
            'title': 'John Smith- call back re solar quote',
            'description': 'Follow up on enquiry',
            'business': 'DSW',
            'priority': 'high',
            'due_date': '2026-02-27',
            'due_time': '09:00',
            'category': 'New Lead',
        }],
    }
    processor.analyze_with_claude = MagicMock(return_value=ai_response)

    # Mock task creation
    fake_task = {'id': 'task-333', 'title': 'John Smith- call back re solar quote',
                 'due_date': '2026-02-27', 'due_time': '09:00:00'}
    mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[fake_task])
    processor.tm.find_existing_task_by_client = MagicMock(return_value=None)

    user_ctx = UserContext(
        user_id='user-1', email_address='rob@example.com',
        full_name='Rob', businesses={'DSW': 'biz-1'},
    )

    # Build a simple email message
    msg = EmailMessage()
    msg['Subject'] = 'Fwd: Solar enquiry from John Smith'
    msg['From'] = 'john@example.com'
    msg.set_content('Hi, I want a quote for 10kW solar.')

    outcome, detail = processor.process_single_email_body(msg, user_context=user_ctx)

    assert outcome == 'task_created', f"Expected 'task_created', got '{outcome}'"
    assert 'John Smith' in detail


@patch('email_utils.send_email', return_value=(True, None))
def test_process_email_returns_no_action_outcome(mock_send, mock_supabase):
    """process_single_email_body should return ('no_action', ...) when AI returns empty actions."""
    from saas_email_processor import AIEmailProcessor, UserContext
    from email.message import EmailMessage

    processor = AIEmailProcessor()

    # Mock AI response with no actions
    ai_response = {'summary': 'Newsletter from SolarQuotes', 'actions': []}
    processor.analyze_with_claude = MagicMock(return_value=ai_response)

    user_ctx = UserContext(
        user_id='user-1', email_address='rob@example.com',
        full_name='Rob', businesses={'DSW': 'biz-1'},
    )

    msg = EmailMessage()
    msg['Subject'] = 'SolarQuotes Weekly Newsletter'
    msg['From'] = 'newsletter@solarquotes.com.au'
    msg.set_content('This week in solar news...')

    outcome, detail = processor.process_single_email_body(msg, user_context=user_ctx)

    assert outcome == 'no_action', f"Expected 'no_action', got '{outcome}'"
    assert 'Newsletter' in detail or 'no actionable' in detail.lower()


# ---------------------------------------------------------------------------
# 9. Email processing audit alerts on silent failures
# ---------------------------------------------------------------------------

@patch('monitoring.send_self_alert')
def test_audit_alerts_on_silent_failures(mock_alert, mock_supabase):
    """check_email_processing_health should alert when 3+ emails processed with no tasks created."""
    import monitoring
    monitoring._supabase = mock_supabase

    # Simulate 4 emails processed, all with 'no_action' outcome
    mock_supabase.table.return_value.select.return_value.gte.return_value.execute.return_value = MagicMock(data=[
        {'outcome': 'no_action', 'outcome_detail': 'AI found no actionable items', 'subject': 'Newsletter 1', 'sender_email': 'a@example.com', 'processed_at': '2026-02-26T06:00:00Z'},
        {'outcome': 'no_action', 'outcome_detail': 'AI found no actionable items', 'subject': 'Newsletter 2', 'sender_email': 'b@example.com', 'processed_at': '2026-02-26T06:01:00Z'},
        {'outcome': 'no_action', 'outcome_detail': 'AI found no actionable items', 'subject': 'Newsletter 3', 'sender_email': 'c@example.com', 'processed_at': '2026-02-26T06:02:00Z'},
        {'outcome': 'error', 'outcome_detail': 'Error: API timeout', 'subject': 'Lead from Jane', 'sender_email': 'd@example.com', 'processed_at': '2026-02-26T06:03:00Z'},
    ])

    result = monitoring.check_email_processing_health()

    assert result == 'warning', f"Expected 'warning', got '{result}'"
    assert mock_alert.called, "send_self_alert should have been called"
    alert_subject = mock_alert.call_args[0][0]
    assert 'no tasks created' in alert_subject.lower()

    monitoring._supabase = None
