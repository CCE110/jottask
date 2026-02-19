"""Tests for authentication — login_required redirects."""


def test_dashboard_redirects_when_not_logged_in(client):
    """Unauthenticated users should be redirected to /login."""
    response = client.get('/dashboard', follow_redirects=False)
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_settings_redirects_when_not_logged_in(client):
    """Unauthenticated users should be redirected to /login."""
    response = client.get('/settings', follow_redirects=False)
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_login_page_renders(client):
    """GET /login should return 200 with the login form."""
    response = client.get('/login')
    assert response.status_code == 200
    assert b'Sign In' in response.data


def test_signup_page_renders(client):
    """GET /signup should return 200 with the signup form."""
    response = client.get('/signup')
    assert response.status_code == 200
    assert b'Create Account' in response.data


def test_landing_page_renders(client):
    """GET / should show the landing page for unauthenticated users."""
    response = client.get('/')
    assert response.status_code == 200
    assert b'Jottask' in response.data
