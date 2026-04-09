#!/usr/bin/env python3
"""
Pre-deploy smoke test — verifies all email paths are wired correctly.
Runs in <2 seconds, no network calls, no env vars needed (uses defaults).

Usage:
    python tests/smoke_test.py
    # or via pytest:
    python -m pytest tests/smoke_test.py -v
"""

import os
import sys
import inspect

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Set minimal env vars so modules can import without crashing
os.environ.setdefault('SUPABASE_URL', 'https://test.supabase.co')
os.environ.setdefault('SUPABASE_KEY', 'test-key')
os.environ.setdefault('RESEND_API_KEY', 'test-key')
os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret')
os.environ.setdefault('STRIPE_SECRET_KEY', 'sk_test_fake')
os.environ.setdefault('STRIPE_WEBHOOK_SECRET', 'whsec_test_fake')

# Mock supabase.create_client before any app imports
from unittest.mock import MagicMock, patch
mock_sb = MagicMock()
patcher = patch('supabase.create_client', return_value=mock_sb)
patcher.start()


def test_email_utils_importable():
    """email_utils.send_email must be importable and callable."""
    from email_utils import send_email
    assert callable(send_email), "send_email is not callable"


def test_saas_email_processor_importable():
    """saas_email_processor must import without error."""
    import saas_email_processor
    assert hasattr(saas_email_processor, 'AIEmailProcessor')


def test_saas_email_processor_no_direct_resend():
    """saas_email_processor must NOT import resend at module level."""
    import saas_email_processor
    source_file = inspect.getfile(saas_email_processor)
    with open(source_file, 'r') as f:
        source = f.read()
    # Check there's no top-level 'import resend' (allow inside strings/comments)
    lines = source.split('\n')
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped == 'import resend' or stripped.startswith('import resend '):
            assert False, f"saas_email_processor.py line {i}: still has 'import resend'"


def test_install_order_importable():
    """install_order must import without error."""
    import install_order
    assert hasattr(install_order, 'send_install_order_email')


def test_install_order_no_direct_resend():
    """install_order must NOT import resend at module level."""
    import install_order
    source_file = inspect.getfile(install_order)
    with open(source_file, 'r') as f:
        source = f.read()
    lines = source.split('\n')
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped == 'import resend' or stripped.startswith('import resend '):
            assert False, f"install_order.py line {i}: still has 'import resend'"


def test_scheduler_functions_importable():
    """Key scheduler functions must be importable."""
    from saas_scheduler import check_and_send_reminders, send_daily_summary
    assert callable(check_and_send_reminders)
    assert callable(send_daily_summary)


def test_monitoring_functions_importable():
    """Key monitoring functions must be importable."""
    from monitoring import check_reminder_health, log_email_send
    assert callable(check_reminder_health)
    assert callable(log_email_send)


# Allow running directly: python tests/smoke_test.py
if __name__ == '__main__':
    tests = [
        test_email_utils_importable,
        test_saas_email_processor_importable,
        test_saas_email_processor_no_direct_resend,
        test_install_order_importable,
        test_install_order_no_direct_resend,
        test_scheduler_functions_importable,
        test_monitoring_functions_importable,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1

    patcher.stop()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
