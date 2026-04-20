"""
Jottask Authentication Module
Handles user signup, login, logout using Supabase Auth
"""

import os
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
from supabase import create_client, Client

# Initialize Supabase client (service role — used for all DB queries)
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def _auth_client() -> Client:
    """Return a fresh Supabase client for auth-only operations.

    sign_in_with_password / sign_up / sign_out mutate the client's internal
    JWT.  If we use the global service-role client for those calls, its auth
    headers get overwritten with a short-lived user token that expires in ~1h,
    causing PGRST303 JWT-expired errors on every subsequent DB query.

    Using a throwaway client keeps the global client's headers clean.
    """
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def login_required(f):
    """Decorator to require authentication for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            # Return 401 JSON for API/AJAX requests instead of redirect
            # (fetch() silently follows redirects, making the caller think it succeeded)
            if request.path.startswith('/api/') or request.headers.get('Content-Type') == 'application/json':
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def _ensure_role_in_session():
    """Load role into session if not already cached."""
    if 'user_role' not in session and 'user_id' in session:
        try:
            result = supabase.table('users').select('role, organization_id').eq('id', session['user_id']).single().execute()
            if result.data:
                session['user_role'] = result.data.get('role', 'user')
                session['organization_id'] = result.data.get('organization_id')
        except:
            session['user_role'] = 'user'


def role_required(*allowed_roles):
    """Decorator: require one of the specified roles."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login', next=request.url))
            _ensure_role_in_session()
            if session.get('user_role') not in allowed_roles:
                return "Access denied", 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def admin_required(f):
    """Require global_admin role."""
    return role_required('global_admin')(f)


def company_admin_required(f):
    """Require company_admin or global_admin role."""
    return role_required('global_admin', 'company_admin')(f)


def get_current_user():
    """Get the currently logged in user from session"""
    if 'user_id' not in session:
        return None

    try:
        result = supabase.table('users').select('*').eq('id', session['user_id']).single().execute()
        return result.data
    except:
        return None


def get_current_user_id():
    """Get the current user's ID"""
    return session.get('user_id')


def signup_user(email, password, full_name=None, timezone='Australia/Brisbane'):
    """
    Sign up a new user with Supabase Auth
    Returns: (success: bool, user_or_error: dict/str)
    """
    try:
        # Create auth user
        auth_response = _auth_client().auth.sign_up({
            'email': email,
            'password': password
        })

        if auth_response.user:
            # Create user profile in our users table
            user_data = {
                'id': auth_response.user.id,
                'email': email.lower(),
                'full_name': full_name or email.split('@')[0],
                'timezone': timezone,
                'subscription_status': 'trial',
                'trial_ends_at': 'now() + interval \'14 days\''
            }

            supabase.table('users').insert(user_data).execute()

            return True, auth_response.user
        else:
            return False, "Signup failed"

    except Exception as e:
        error_msg = str(e)
        if 'already registered' in error_msg.lower():
            return False, "Email already registered"
        return False, error_msg


def login_user(email, password):
    """
    Log in a user
    Returns: (success: bool, user_or_error: dict/str)
    """
    try:
        auth_response = _auth_client().auth.sign_in_with_password({
            'email': email,
            'password': password
        })

        if auth_response.user:
            # Set session (persistent — PERMANENT_SESSION_LIFETIME set in dashboard.py)
            session.permanent = True
            session['user_id'] = auth_response.user.id
            session['user_email'] = auth_response.user.email
            session['access_token'] = auth_response.session.access_token

            # Get user profile
            user = supabase.table('users').select('*').eq('id', auth_response.user.id).single().execute()

            if user.data:
                session['user_name'] = user.data.get('full_name', email.split('@')[0])
                session['timezone'] = user.data.get('timezone', 'Australia/Brisbane')
                session['user_role'] = user.data.get('role', 'user')
                session['organization_id'] = user.data.get('organization_id')

            return True, auth_response.user
        else:
            return False, "Invalid credentials"

    except Exception as e:
        error_msg = str(e)
        if 'invalid' in error_msg.lower():
            return False, "Invalid email or password"
        return False, error_msg


def logout_user():
    """Log out the current user"""
    try:
        _auth_client().auth.sign_out()
    except:
        pass

    session.clear()
    return True


def reset_password(email):
    """Send password reset email"""
    try:
        _auth_client().auth.reset_password_email(email)
        return True, "Password reset email sent"
    except Exception as e:
        return False, str(e)


def update_user_profile(user_id, **kwargs):
    """Update user profile fields"""
    allowed_fields = ['full_name', 'timezone', 'company_name', 'phone']
    update_data = {k: v for k, v in kwargs.items() if k in allowed_fields}

    if not update_data:
        return False

    try:
        supabase.table('users').update(update_data).eq('id', user_id).execute()
        return True
    except:
        return False
