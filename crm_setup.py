"""
Jottask CRM Connection Setup
Allows users to connect their CRM accounts for automatic syncing
Supports API key auth (PipeReply) and OAuth 2.0 (HubSpot, Zoho)
"""

import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import pytz
import requests
from flask import Blueprint, render_template, request, redirect, url_for, session
from supabase import create_client, Client
from auth import login_required
from crm_manager import CRMManager
from opensolar_connector import OpenSolarConnector

crm_setup_bp = Blueprint('crm_setup', __name__, url_prefix='/crm')

crm_mgr = CRMManager()

# In-memory cache for OpenSolar connectors (keyed by user_id)
_opensolar_connectors = {}


def get_opensolar_for_user(user_id: str) -> Optional[OpenSolarConnector]:
    """Get an authenticated OpenSolar connector for a user.
    Used by email processor, scheduler, and API routes.
    """
    # Check cache first
    cached = _opensolar_connectors.get(user_id)
    if cached and cached.token:
        return cached

    # Load from database
    connections = crm_mgr.get_user_connections(user_id)
    conn = next((c for c in connections if c['provider'] == 'opensolar' and c.get('is_active')), None)
    if not conn:
        return None

    email = conn.get('api_base_url', '')  # email stored in api_base_url
    password = conn.get('api_key', '')
    org_id = (conn.get('settings') or {}).get('org_id', '')
    token = conn.get('access_token', '')

    connector = OpenSolarConnector(email=email, password=password, token=token, org_id=org_id)

    # Re-authenticate if no token
    if not token:
        result = connector.authenticate()
        if not result.success:
            print(f"OpenSolar: Failed to authenticate for user {user_id}: {result.message}")
            return None

    _opensolar_connectors[user_id] = connector
    return connector

# OAuth request timeout
OAUTH_TIMEOUT = 15


@crm_setup_bp.route('/')
@login_required
def crm_setup():
    """Show CRM connections and available providers"""
    user_id = session['user_id']
    connections = crm_mgr.get_user_connections(user_id)

    return render_template(
        'crm_setup.html',
        connections=connections,
        message=request.args.get('message'),
        error=request.args.get('error'),
    )


# ========================================
# PipeReply (API Key)
# ========================================

@crm_setup_bp.route('/add/pipereply', methods=['POST'])
@login_required
def add_pipereply():
    """Test and save a PipeReply/GHL CRM connection using Private Integration Token"""
    user_id = session['user_id']
    api_key = request.form.get('api_key', '').strip()
    location_id = request.form.get('location_id', '').strip()

    if not api_key:
        return redirect(url_for('crm_setup.crm_setup', error='Please enter your Private Integration Token'))

    settings = {}
    if location_id:
        settings['location_id'] = location_id

    # Test the connection first
    result = crm_mgr.test_connection_for_user(
        provider='pipereply',
        api_key=api_key,
        settings=settings,
    )

    if not result.success:
        return redirect(url_for('crm_setup.crm_setup', error=f'Connection failed: {result.message}'))

    # Save the connection
    crm_mgr.save_connection(
        user_id=user_id,
        provider='pipereply',
        api_key=api_key,
        display_name='PipeReply CRM',
        connection_status='connected',
        is_active=True,
    )

    # Save location_id in settings if provided
    if location_id:
        connections = crm_mgr.get_user_connections(user_id)
        conn = next((c for c in connections if c['provider'] == 'pipereply'), None)
        if conn:
            crm_mgr.supabase.table('crm_connections') \
                .update({'settings': settings}) \
                .eq('id', conn['id']) \
                .execute()

    return redirect(url_for('crm_setup.crm_setup', message='PipeReply connected successfully!'))


@crm_setup_bp.route('/add/opensolar', methods=['POST'])
@login_required
def add_opensolar():
    """Test and save an OpenSolar connection using email/password"""
    user_id = session['user_id']
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()

    if not email or not password:
        return redirect(url_for('crm_setup.crm_setup', error='Please enter your OpenSolar email and password'))

    # Test the connection
    connector = OpenSolarConnector(email=email, password=password)
    result = connector.test_connection()

    if not result.success:
        return redirect(url_for('crm_setup.crm_setup', error=f'OpenSolar connection failed: {result.message}'))

    # Save the connection (store email in display_name, password in api_key, org_id in settings)
    settings = {'org_id': connector.org_id}
    crm_mgr.save_connection(
        user_id=user_id,
        provider='opensolar',
        api_key=password,  # Stored encrypted by Supabase RLS
        display_name=f'OpenSolar ({email})',
        connection_status='connected',
        is_active=True,
    )

    # Update with email and settings
    connections = crm_mgr.get_user_connections(user_id)
    conn = next((c for c in connections if c['provider'] == 'opensolar'), None)
    if conn:
        crm_mgr.supabase.table('crm_connections') \
            .update({
                'settings': settings,
                'access_token': connector.token,  # Cache the current token
                'api_base_url': email,  # Store email in api_base_url field
            }) \
            .eq('id', conn['id']) \
            .execute()

    # Cache the connector
    _opensolar_connectors[user_id] = connector

    return redirect(url_for('crm_setup.crm_setup', message='OpenSolar connected successfully!'))


# ========================================
# HubSpot OAuth 2.0
# ========================================

@crm_setup_bp.route('/oauth/hubspot/start')
@login_required
def hubspot_oauth_start():
    """Redirect user to HubSpot authorization page"""
    client_id = os.getenv('HUBSPOT_CLIENT_ID', '')
    if not client_id:
        return redirect(url_for('crm_setup.crm_setup', error='HubSpot integration not configured — contact support'))

    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    session['hubspot_oauth_state'] = state

    app_url = os.getenv('APP_URL', 'https://www.jottask.app').rstrip('/')
    redirect_uri = f'{app_url}/crm/oauth/hubspot/callback'

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': 'crm.objects.contacts.read crm.objects.contacts.write crm.objects.deals.read crm.objects.deals.write',
        'state': state,
    }
    auth_url = f'https://app.hubspot.com/oauth/authorize?{urlencode(params)}'
    return redirect(auth_url)


@crm_setup_bp.route('/oauth/hubspot/callback')
@login_required
def hubspot_oauth_callback():
    """Exchange HubSpot authorization code for tokens"""
    # Verify state
    state = request.args.get('state', '')
    expected_state = session.pop('hubspot_oauth_state', '')
    if not state or state != expected_state:
        return redirect(url_for('crm_setup.crm_setup', error='Invalid OAuth state — please try again'))

    error = request.args.get('error', '')
    if error:
        error_desc = request.args.get('error_description', error)
        return redirect(url_for('crm_setup.crm_setup', error=f'HubSpot authorization failed: {error_desc}'))

    code = request.args.get('code', '')
    if not code:
        return redirect(url_for('crm_setup.crm_setup', error='No authorization code received from HubSpot'))

    client_id = os.getenv('HUBSPOT_CLIENT_ID', '')
    client_secret = os.getenv('HUBSPOT_CLIENT_SECRET', '')
    app_url = os.getenv('APP_URL', 'https://www.jottask.app').rstrip('/')
    redirect_uri = f'{app_url}/crm/oauth/hubspot/callback'

    # Exchange code for tokens
    try:
        resp = requests.post(
            'https://api.hubapi.com/oauth/v1/token',
            data={
                'grant_type': 'authorization_code',
                'client_id': client_id,
                'client_secret': client_secret,
                'redirect_uri': redirect_uri,
                'code': code,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=OAUTH_TIMEOUT,
        )
    except Exception as e:
        return redirect(url_for('crm_setup.crm_setup', error=f'Failed to exchange HubSpot code: {str(e)}'))

    if resp.status_code != 200:
        return redirect(url_for('crm_setup.crm_setup', error=f'HubSpot token exchange failed: {resp.text[:200]}'))

    data = resp.json()
    access_token = data.get('access_token', '')
    refresh_token = data.get('refresh_token', '')
    expires_in = data.get('expires_in', 21600)
    token_expires_at = (datetime.now(pytz.UTC) + timedelta(seconds=expires_in)).isoformat()

    # Test the connection with the new token
    result = crm_mgr.test_connection_for_user(
        provider='hubspot',
        access_token=access_token,
    )

    if not result.success:
        return redirect(url_for('crm_setup.crm_setup', error=f'HubSpot connected but test failed: {result.message}'))

    # Save the OAuth connection
    user_id = session['user_id']
    crm_mgr.save_oauth_connection(
        user_id=user_id,
        provider='hubspot',
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=token_expires_at,
        display_name='HubSpot CRM',
    )

    return redirect(url_for('crm_setup.crm_setup', message='HubSpot connected successfully!'))


# ========================================
# Zoho OAuth 2.0
# ========================================

@crm_setup_bp.route('/oauth/zoho/start')
@login_required
def zoho_oauth_start():
    """Redirect user to Zoho authorization page"""
    client_id = os.getenv('ZOHO_CLIENT_ID', '')
    if not client_id:
        return redirect(url_for('crm_setup.crm_setup', error='Zoho integration not configured — contact support'))

    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    session['zoho_oauth_state'] = state

    app_url = os.getenv('APP_URL', 'https://www.jottask.app').rstrip('/')
    redirect_uri = f'{app_url}/crm/oauth/zoho/callback'

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': 'ZohoCRM.modules.ALL,ZohoCRM.settings.ALL',
        'response_type': 'code',
        'access_type': 'offline',  # Required to get refresh_token
        'prompt': 'consent',
        'state': state,
    }
    # Use Australian Zoho accounts domain by default
    accounts_domain = os.getenv('ZOHO_ACCOUNTS_DOMAIN', 'https://accounts.zoho.com.au')
    auth_url = f'{accounts_domain}/oauth/v2/auth?{urlencode(params)}'
    return redirect(auth_url)


@crm_setup_bp.route('/oauth/zoho/callback')
@login_required
def zoho_oauth_callback():
    """Exchange Zoho authorization code for tokens"""
    # Verify state
    state = request.args.get('state', '')
    expected_state = session.pop('zoho_oauth_state', '')
    if not state or state != expected_state:
        return redirect(url_for('crm_setup.crm_setup', error='Invalid OAuth state — please try again'))

    error = request.args.get('error', '')
    if error:
        return redirect(url_for('crm_setup.crm_setup', error=f'Zoho authorization failed: {error}'))

    code = request.args.get('code', '')
    if not code:
        return redirect(url_for('crm_setup.crm_setup', error='No authorization code received from Zoho'))

    client_id = os.getenv('ZOHO_CLIENT_ID', '')
    client_secret = os.getenv('ZOHO_CLIENT_SECRET', '')
    accounts_domain = os.getenv('ZOHO_ACCOUNTS_DOMAIN', 'https://accounts.zoho.com.au')
    app_url = os.getenv('APP_URL', 'https://www.jottask.app').rstrip('/')
    redirect_uri = f'{app_url}/crm/oauth/zoho/callback'

    # Exchange code for tokens
    try:
        resp = requests.post(
            f'{accounts_domain}/oauth/v2/token',
            params={
                'grant_type': 'authorization_code',
                'client_id': client_id,
                'client_secret': client_secret,
                'redirect_uri': redirect_uri,
                'code': code,
            },
            timeout=OAUTH_TIMEOUT,
        )
    except Exception as e:
        return redirect(url_for('crm_setup.crm_setup', error=f'Failed to exchange Zoho code: {str(e)}'))

    if resp.status_code != 200:
        return redirect(url_for('crm_setup.crm_setup', error=f'Zoho token exchange failed: {resp.text[:200]}'))

    data = resp.json()
    if 'error' in data:
        return redirect(url_for('crm_setup.crm_setup', error=f"Zoho error: {data['error']}"))

    access_token = data.get('access_token', '')
    refresh_token = data.get('refresh_token', '')
    expires_in = data.get('expires_in', 3600)
    token_expires_at = (datetime.now(pytz.UTC) + timedelta(seconds=expires_in)).isoformat()

    # Determine the API domain from the response or location header
    api_domain = data.get('api_domain', 'https://www.zohoapis.com.au')

    # Test the connection with the new token
    result = crm_mgr.test_connection_for_user(
        provider='zoho',
        access_token=access_token,
        settings={'zoho_domain': api_domain, 'zoho_accounts_domain': accounts_domain},
    )

    if not result.success:
        return redirect(url_for('crm_setup.crm_setup', error=f'Zoho connected but test failed: {result.message}'))

    # Save the OAuth connection
    user_id = session['user_id']
    crm_mgr.save_oauth_connection(
        user_id=user_id,
        provider='zoho',
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=token_expires_at,
        display_name='Zoho CRM',
        settings={'zoho_domain': api_domain, 'zoho_accounts_domain': accounts_domain},
    )

    return redirect(url_for('crm_setup.crm_setup', message='Zoho CRM connected successfully!'))


# ========================================
# Connection Management
# ========================================

@crm_setup_bp.route('/<connection_id>/delete', methods=['POST'])
@login_required
def delete_connection(connection_id):
    """Remove a CRM connection"""
    user_id = session['user_id']
    crm_mgr.delete_connection(connection_id, user_id)
    return redirect(url_for('crm_setup.crm_setup', message='Connection removed'))


@crm_setup_bp.route('/<connection_id>/test', methods=['POST'])
@login_required
def test_connection(connection_id):
    """Re-test an existing CRM connection (with token refresh for OAuth)"""
    user_id = session['user_id']

    # Get the connection
    connections = crm_mgr.get_user_connections(user_id)
    conn = next((c for c in connections if c['id'] == connection_id), None)

    if not conn:
        return redirect(url_for('crm_setup.crm_setup', error='Connection not found'))

    # Refresh token if needed (for OAuth connections)
    conn = crm_mgr.refresh_token_if_needed(conn)

    # OpenSolar uses credential-based auth, not the CRM connector pattern
    if conn.get('provider') == 'opensolar':
        email = conn.get('api_base_url', '')  # email stored in api_base_url
        password = conn.get('api_key', '')
        os_connector = OpenSolarConnector(email=email, password=password)
        os_result = os_connector.test_connection()
        if os_result.success:
            # Update cached token
            crm_mgr.supabase.table('crm_connections') \
                .update({'access_token': os_connector.token}) \
                .eq('id', connection_id) \
                .execute()
            crm_mgr.update_connection_status(connection_id, 'connected')
            return redirect(url_for('crm_setup.crm_setup', message=f'Connection working: {os_result.message}'))
        else:
            crm_mgr.update_connection_status(connection_id, 'error', os_result.message)
            return redirect(url_for('crm_setup.crm_setup', error=f'Connection failed: {os_result.message}'))

    result = crm_mgr.test_connection_for_user(
        provider=conn['provider'],
        api_key=conn.get('api_key', ''),
        api_base_url=conn.get('api_base_url', ''),
        access_token=conn.get('access_token', ''),
        settings=conn.get('settings') or {},
    )

    if result.success:
        crm_mgr.update_connection_status(connection_id, 'connected')
        return redirect(url_for('crm_setup.crm_setup', message=f'Connection working: {result.message}'))
    else:
        crm_mgr.update_connection_status(connection_id, 'error', result.message)
        return redirect(url_for('crm_setup.crm_setup', error=f'Connection failed: {result.message}'))
