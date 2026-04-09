"""
Shared test fixtures for Jottask tests.
All external services (Supabase, Resend) are mocked.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Set required env vars BEFORE any app imports
os.environ.setdefault('SUPABASE_URL', 'https://test.supabase.co')
os.environ.setdefault('SUPABASE_KEY', 'test-key')
os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret')
os.environ.setdefault('RESEND_API_KEY', 'test-resend-key')
os.environ.setdefault('STRIPE_SECRET_KEY', 'sk_test_fake')
os.environ.setdefault('STRIPE_WEBHOOK_SECRET', 'whsec_test_fake')


@pytest.fixture(autouse=True)
def mock_supabase(monkeypatch):
    """Mock the Supabase client globally so no real DB calls are made."""
    mock_client = MagicMock()

    def mock_create_client(url, key):
        return mock_client

    monkeypatch.setattr('supabase.create_client', mock_create_client)
    return mock_client


@pytest.fixture
def app(mock_supabase):
    """Create a Flask test app with mocked Supabase."""
    # Must import after monkeypatch has taken effect
    from dashboard import app as flask_app
    flask_app.config['TESTING'] = True
    flask_app.config['SECRET_KEY'] = 'test-secret'
    return flask_app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def authenticated_client(client, app):
    """Flask test client with an authenticated session."""
    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id-123'
        sess['user_email'] = 'test@example.com'
        sess['user_name'] = 'Test User'
        sess['timezone'] = 'Australia/Brisbane'
    return client
