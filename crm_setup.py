"""
Jottask CRM Connection Setup
Allows users to connect their CRM accounts for automatic syncing
"""

import os
from flask import Blueprint, render_template, request, redirect, url_for, session
from supabase import create_client, Client
from auth import login_required
from crm_manager import CRMManager

crm_setup_bp = Blueprint('crm_setup', __name__, url_prefix='/crm')

crm_mgr = CRMManager()


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


@crm_setup_bp.route('/add/pipereply', methods=['POST'])
@login_required
def add_pipereply():
    """Test and save a PipeReply CRM connection"""
    user_id = session['user_id']
    api_key = request.form.get('api_key', '').strip()
    api_base_url = request.form.get('api_base_url', '').strip()

    if not api_key:
        return redirect(url_for('crm_setup.crm_setup', error='Please enter your API key'))

    # Test the connection first
    result = crm_mgr.test_connection_for_user(
        provider='pipereply',
        api_key=api_key,
        api_base_url=api_base_url,
    )

    if not result.success:
        return redirect(url_for('crm_setup.crm_setup', error=f'Connection failed: {result.message}'))

    # Save the connection
    crm_mgr.save_connection(
        user_id=user_id,
        provider='pipereply',
        api_key=api_key,
        api_base_url=api_base_url,
        display_name='PipeReply CRM',
        connection_status='connected',
        is_active=True,
    )

    return redirect(url_for('crm_setup.crm_setup', message='PipeReply connected successfully!'))


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
    """Re-test an existing CRM connection"""
    user_id = session['user_id']

    # Get the connection
    connections = crm_mgr.get_user_connections(user_id)
    conn = next((c for c in connections if c['id'] == connection_id), None)

    if not conn:
        return redirect(url_for('crm_setup.crm_setup', error='Connection not found'))

    result = crm_mgr.test_connection_for_user(
        provider=conn['provider'],
        api_key=conn.get('api_key', ''),
        api_base_url=conn.get('api_base_url', ''),
    )

    if result.success:
        crm_mgr.update_connection_status(connection_id, 'connected')
        return redirect(url_for('crm_setup.crm_setup', message=f'Connection working: {result.message}'))
    else:
        crm_mgr.update_connection_status(connection_id, 'error', result.message)
        return redirect(url_for('crm_setup.crm_setup', error=f'Connection failed: {result.message}'))
