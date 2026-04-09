"""
Jottask Email Connection Setup
Allows users to connect their email accounts for automatic task creation
"""

import os
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from supabase import create_client, Client
from auth import login_required

email_setup_bp = Blueprint('email_setup', __name__, url_prefix='/email')

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


@email_setup_bp.route('/')
@login_required
def email_setup():
    user_id = session['user_id']

    # Get existing connections
    connections = supabase.table('email_connections')\
        .select('*')\
        .eq('user_id', user_id)\
        .execute()

    return render_template(
        'email_setup.html',
        connections=connections.data or [],
        message=request.args.get('message'),
        error=request.args.get('error')
    )


@email_setup_bp.route('/add/gmail', methods=['POST'])
@login_required
def add_gmail():
    user_id = session['user_id']
    email_address = request.form.get('email_address', '').lower().strip()
    app_password = request.form.get('app_password', '').strip()

    if not email_address or not app_password:
        return redirect(url_for('email_setup.email_setup', error='Please fill in all fields'))

    # Test the connection
    import imaplib
    try:
        imap = imaplib.IMAP4_SSL('imap.gmail.com')
        imap.login(email_address, app_password.replace(' ', ''))
        imap.logout()
    except Exception as e:
        return redirect(url_for('email_setup.email_setup', error=f'Connection failed: Invalid credentials'))

    # Check if already exists
    existing = supabase.table('email_connections')\
        .select('id')\
        .eq('user_id', user_id)\
        .eq('email_address', email_address)\
        .execute()

    if existing.data:
        # Update existing
        supabase.table('email_connections').update({
            'imap_password': app_password.replace(' ', ''),
            'is_active': True
        }).eq('id', existing.data[0]['id']).execute()
    else:
        # Create new
        supabase.table('email_connections').insert({
            'user_id': user_id,
            'provider': 'gmail',
            'email_address': email_address,
            'imap_password': app_password.replace(' ', ''),
            'is_active': True
        }).execute()

    return redirect(url_for('email_setup.email_setup', message='Gmail connected successfully!'))


@email_setup_bp.route('/<connection_id>/delete', methods=['POST'])
@login_required
def delete_connection(connection_id):
    user_id = session['user_id']

    supabase.table('email_connections')\
        .delete()\
        .eq('id', connection_id)\
        .eq('user_id', user_id)\
        .execute()

    return redirect(url_for('email_setup.email_setup', message='Connection removed'))


@email_setup_bp.route('/<connection_id>/toggle', methods=['POST'])
@login_required
def toggle_connection(connection_id):
    user_id = session['user_id']

    # Get current status
    conn = supabase.table('email_connections')\
        .select('is_active')\
        .eq('id', connection_id)\
        .eq('user_id', user_id)\
        .single()\
        .execute()

    if conn.data:
        new_status = not conn.data['is_active']
        supabase.table('email_connections').update({
            'is_active': new_status
        }).eq('id', connection_id).execute()

    return redirect(url_for('email_setup.email_setup'))
