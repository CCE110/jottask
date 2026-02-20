"""
Jottask Onboarding Flow
Guides new users through initial setup
Steps: 1=Profile, 2=Email, 3=CRM, 4=First Task
"""

import os
from flask import Blueprint, render_template, request, redirect, url_for, session
from supabase import create_client, Client
from auth import login_required

onboarding_bp = Blueprint('onboarding', __name__, url_prefix='/onboarding')

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


@onboarding_bp.route('/')
@login_required
def start():
    """Start onboarding flow"""
    user_id = session['user_id']

    # Check if user has completed onboarding
    user = supabase.table('users').select('onboarding_completed').eq('id', user_id).single().execute()

    if user.data and user.data.get('onboarding_completed'):
        return redirect(url_for('dashboard'))

    return render_template(
        'onboarding.html',
        step=1,
        user_name=session.get('user_name', 'there')
    )


@onboarding_bp.route('/step1', methods=['POST'])
@login_required
def step1():
    """Save profile info"""
    user_id = session['user_id']

    full_name = request.form.get('full_name')
    company_name = request.form.get('company_name')
    timezone = request.form.get('timezone')

    supabase.table('users').update({
        'full_name': full_name,
        'company_name': company_name,
        'timezone': timezone
    }).eq('id', user_id).execute()

    session['user_name'] = full_name
    session['timezone'] = timezone

    return render_template('onboarding.html', step=2, user_name=full_name)


@onboarding_bp.route('/step2', methods=['POST'])
@login_required
def step2():
    """Email setup (placeholder) — advances to CRM step"""
    return render_template(
        'onboarding.html',
        step=3,
        user_name=session.get('user_name'),
    )


@onboarding_bp.route('/step3', methods=['GET', 'POST'])
@login_required
def step3():
    """CRM connection step"""
    user_id = session['user_id']

    if request.method == 'POST':
        crm_provider = request.form.get('crm_provider', '')
        api_key = request.form.get('api_key', '').strip()

        if crm_provider == 'pipereply' and api_key:
            # Test and save PipeReply connection
            try:
                from crm_manager import CRMManager
                crm = CRMManager()
                result = crm.test_connection_for_user(
                    provider='pipereply',
                    api_key=api_key,
                    api_base_url=request.form.get('api_base_url', '').strip(),
                )
                if result.success:
                    crm.save_connection(
                        user_id=user_id,
                        provider='pipereply',
                        api_key=api_key,
                        api_base_url=request.form.get('api_base_url', '').strip(),
                        display_name='PipeReply CRM',
                        connection_status='connected',
                        is_active=True,
                    )
            except Exception as e:
                print(f"Onboarding CRM setup error: {e}")

    # Advance to step 4 (first task)
    from datetime import date
    return render_template(
        'onboarding.html',
        step=4,
        user_name=session.get('user_name'),
        today=date.today().isoformat()
    )


@onboarding_bp.route('/step4')
@login_required
def step4():
    """Show step 4 (first task)"""
    from datetime import date
    return render_template(
        'onboarding.html',
        step=4,
        user_name=session.get('user_name'),
        today=date.today().isoformat()
    )


@onboarding_bp.route('/complete', methods=['POST'])
@login_required
def complete():
    """Complete onboarding and create first task"""
    user_id = session['user_id']

    task_title = request.form.get('task_title')
    due_date = request.form.get('due_date')

    # Create first task
    if task_title:
        supabase.table('tasks').insert({
            'user_id': user_id,
            'title': task_title,
            'due_date': due_date,
            'due_time': '09:00:00',
            'priority': 'medium',
            'status': 'pending'
        }).execute()

    # Mark onboarding complete
    supabase.table('users').update({
        'onboarding_completed': True
    }).eq('id', user_id).execute()

    return redirect(url_for('dashboard'))
