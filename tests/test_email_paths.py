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
