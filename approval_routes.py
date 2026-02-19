"""
Approval Routes for Jottask v2 Tiered Action System
Add these routes to app.py (paste before the final 'if __name__' block)
"""

# ============================================
# V2 APPROVAL ROUTES (Tiered Action System)
# ============================================

@app.route('/action/approve')
def approve_action():
    """Approve a pending Tier 2 action via email button click"""
    token = request.args.get('token')
    if not token:
        return ERROR_TEMPLATE.format(error="Missing token"), 400

    try:
        # Look up the pending action
        result = tm.supabase.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()

        if not result.data:
            # Check if already processed
            already = tm.supabase.table('pending_actions').select('status').eq('token', token).execute()
            if already.data:
                status = already.data[0]['status']
                return f"""<html><body style="font-family: -apple-system, sans-serif; max-width: 500px; margin: 50px auto; text-align: center;">
                    <div style="background: #fef3c7; border-radius: 12px; padding: 30px;">
                        <h2>Already Processed</h2>
                        <p>This action was already <strong>{status}</strong>.</p>
                        <a href="https://www.jottask.app/dashboard" style="color: #3b82f6;">Go to Dashboard</a>
                    </div></body></html>"""
            return ERROR_TEMPLATE.format(error="Action not found or expired"), 404

        action_data = result.data[0]
        import json
        action = json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']

        # Execute the action based on type
        action_type = action.get('action_type', '')
        action_title = action.get('title', 'Unknown action')

        if action_type == 'update_crm':
            # Create a task with CRM notes
            crm_notes = action.get('crm_notes', action.get('description', ''))
            customer = action.get('customer_name', '')
            tm.supabase.table('tasks').insert({
                'title': f"CRM Update: {customer}" if customer else action_title,
                'description': f"CRM Notes: {crm_notes}",
                'status': 'pending',
                'category': 'crm',
                'created_at': datetime.now(pytz.UTC).isoformat(),
            }).execute()

        elif action_type == 'send_email':
            # Create a task to draft the email
            tm.supabase.table('tasks').insert({
                'title': action_title,
                'description': action.get('description', ''),
                'status': 'pending',
                'category': 'email',
                'created_at': datetime.now(pytz.UTC).isoformat(),
            }).execute()

        elif action_type == 'create_calendar_event':
            tm.supabase.table('tasks').insert({
                'title': action_title,
                'description': f"Calendar: {action.get('calendar_details', action.get('description', ''))}",
                'status': 'pending',
                'category': 'calendar',
                'due_date': action.get('due_date'),
                'created_at': datetime.now(pytz.UTC).isoformat(),
            }).execute()

        elif action_type == 'change_deal_status':
            tm.supabase.table('tasks').insert({
                'title': action_title,
                'description': action.get('description', ''),
                'status': 'pending',
                'category': 'deals',
                'created_at': datetime.now(pytz.UTC).isoformat(),
            }).execute()

        else:
            # Generic fallback - create task
            tm.supabase.table('tasks').insert({
                'title': action_title,
                'description': action.get('description', ''),
                'status': 'pending',
                'created_at': datetime.now(pytz.UTC).isoformat(),
            }).execute()

        # Mark as approved
        tm.supabase.table('pending_actions').update({
            'status': 'approved',
            'processed_at': datetime.now(pytz.UTC).isoformat()
        }).eq('token', token).execute()

        return f"""<html><body style="font-family: -apple-system, sans-serif; max-width: 500px; margin: 50px auto; text-align: center;">
            <div style="background: #dcfce7; border-radius: 12px; padding: 30px;">
                <div style="font-size: 48px; margin-bottom: 16px;">✅</div>
                <h2 style="color: #166534;">Action Approved</h2>
                <p style="color: #444; font-size: 16px;"><strong>{action_title}</strong></p>
                <p style="color: #666;">The action has been executed successfully.</p>
                <a href="https://www.jottask.app/dashboard" style="display: inline-block; margin-top: 16px; padding: 10px 24px; background: #22c55e; color: white; text-decoration: none; border-radius: 8px; font-weight: bold;">Go to Dashboard</a>
            </div></body></html>"""

    except Exception as e:
        print(f"Error approving action: {e}")
        return ERROR_TEMPLATE.format(error=f"Error processing approval: {str(e)}"), 500


@app.route('/action/reject')
def reject_action():
    """Skip/reject a pending Tier 2 action"""
    token = request.args.get('token')
    if not token:
        return ERROR_TEMPLATE.format(error="Missing token"), 400

    try:
        result = tm.supabase.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()

        if not result.data:
            already = tm.supabase.table('pending_actions').select('status').eq('token', token).execute()
            if already.data:
                status = already.data[0]['status']
                return f"""<html><body style="font-family: -apple-system, sans-serif; max-width: 500px; margin: 50px auto; text-align: center;">
                    <div style="background: #fef3c7; border-radius: 12px; padding: 30px;">
                        <h2>Already Processed</h2>
                        <p>This action was already <strong>{status}</strong>.</p>
                    </div></body></html>"""
            return ERROR_TEMPLATE.format(error="Action not found or expired"), 404

        import json
        action_data = result.data[0]
        action = json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_title = action.get('title', 'Unknown action')

        # Mark as rejected
        tm.supabase.table('pending_actions').update({
            'status': 'rejected',
            'processed_at': datetime.now(pytz.UTC).isoformat()
        }).eq('token', token).execute()

        return f"""<html><body style="font-family: -apple-system, sans-serif; max-width: 500px; margin: 50px auto; text-align: center;">
            <div style="background: #fee2e2; border-radius: 12px; padding: 30px;">
                <div style="font-size: 48px; margin-bottom: 16px;">⏭️</div>
                <h2 style="color: #991b1b;">Action Skipped</h2>
                <p style="color: #444; font-size: 16px;"><strong>{action_title}</strong></p>
                <p style="color: #666;">This action has been skipped.</p>
                <a href="https://www.jottask.app/dashboard" style="display: inline-block; margin-top: 16px; padding: 10px 24px; background: #6b7280; color: white; text-decoration: none; border-radius: 8px; font-weight: bold;">Go to Dashboard</a>
            </div></body></html>"""

    except Exception as e:
        print(f"Error rejecting action: {e}")
        return ERROR_TEMPLATE.format(error=f"Error processing rejection: {str(e)}"), 500


@app.route('/action/edit')
def edit_action():
    """Show the pending action details for editing (future: editable form)"""
    token = request.args.get('token')
    if not token:
        return ERROR_TEMPLATE.format(error="Missing token"), 400

    try:
        result = tm.supabase.table('pending_actions').select('*').eq('token', token).execute()

        if not result.data:
            return ERROR_TEMPLATE.format(error="Action not found"), 404

        import json
        action_data = result.data[0]
        action = json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_title = action.get('title', 'Unknown action')
        action_type = action.get('action_type', '').replace('_', ' ').upper()
        description = action.get('description', action.get('crm_notes', ''))
        customer = action.get('customer_name', '')
        status = action_data['status']

        return f"""<html><body style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 50px auto;">
            <div style="background: #eff6ff; border-radius: 12px; padding: 30px;">
                <div style="font-size: 48px; margin-bottom: 16px; text-align: center;">📝</div>
                <h2 style="color: #1e40af; text-align: center;">Action Details</h2>
                <div style="background: white; border-radius: 8px; padding: 20px; margin: 16px 0;">
                    <p><strong>Type:</strong> {action_type}</p>
                    <p><strong>Title:</strong> {action_title}</p>
                    {'<p><strong>Customer:</strong> ' + customer + '</p>' if customer else ''}
                    <p><strong>Details:</strong> {description}</p>
                    <p><strong>Status:</strong> {status}</p>
                </div>
                {'<div style="text-align: center; margin-top: 20px;"><a href="/action/approve?token=' + token + '" style="display: inline-block; padding: 10px 24px; background: #22c55e; color: white; text-decoration: none; border-radius: 8px; font-weight: bold; margin-right: 8px;">Approve</a><a href="/action/reject?token=' + token + '" style="display: inline-block; padding: 10px 24px; background: #ef4444; color: white; text-decoration: none; border-radius: 8px; font-weight: bold;">Skip</a></div>' if status == 'pending' else '<p style="text-align: center; color: #666;">This action has already been ' + status + '.</p>'}
            </div></body></html>"""

    except Exception as e:
        print(f"Error loading action for edit: {e}")
        return ERROR_TEMPLATE.format(error=f"Error loading action: {str(e)}"), 500
