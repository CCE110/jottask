"""Tests for the shared email_utils module."""

from unittest.mock import patch, MagicMock


def test_send_email_success(monkeypatch):
    """send_email should return (True, None) on success."""
    monkeypatch.setenv('RESEND_API_KEY', 'test-key')

    mock_emails = MagicMock()
    with patch('email_utils.resend') as mock_resend:
        mock_resend.Emails = mock_emails
        mock_emails.send.return_value = {'id': 'test-123'}

        from email_utils import send_email
        success, error = send_email('user@example.com', 'Test Subject', '<p>Hello</p>')

    assert success is True
    assert error is None
    mock_emails.send.assert_called_once()

    # Verify the call args
    call_args = mock_emails.send.call_args[0][0]
    assert call_args['to'] == ['user@example.com']
    assert call_args['subject'] == 'Test Subject'


def test_send_email_no_api_key(monkeypatch):
    """send_email should fail gracefully when no API key is set."""
    monkeypatch.setattr('email_utils.RESEND_API_KEY', None)

    from email_utils import send_email
    success, error = send_email('user@example.com', 'Test', '<p>Hi</p>')

    assert success is False
    assert 'not configured' in error


def test_send_email_handles_exception(monkeypatch):
    """send_email should catch exceptions and return (False, error_msg)."""
    monkeypatch.setenv('RESEND_API_KEY', 'test-key')

    with patch('email_utils.resend') as mock_resend:
        mock_resend.Emails.send.side_effect = Exception('API timeout')

        from email_utils import send_email
        success, error = send_email('user@example.com', 'Test', '<p>Hi</p>')

    assert success is False
    assert 'API timeout' in error
