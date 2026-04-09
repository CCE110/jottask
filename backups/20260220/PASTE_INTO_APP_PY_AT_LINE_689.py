
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
        import json as _json
        result = tm.supabase.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()
        if not result.data:
            already = tm.supabase.table('pending_actions').select('status').eq('token', token).execute()
            if already.data:
                st = already.data[0]['status']
                return f"""<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fef3c7;border-radius:12px;padding:30px"><h2>Already Processed</h2><p>This action was already <strong>{st}</strong>.</p><a href="https://www.jottask.app/dashboard" style="color:#3b82f6">Dashboard</a></div></body></html>"""
            return ERROR_TEMPLATE.format(error='Action not found or expired'), 404
        action_data = result.data[0]
        action = _json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_type = action.get('action_type', '')
        action_title = action.get('title', 'Unknown action')
        task_data = {'title': action_title, 'description': action.get('description', action.get('crm_notes', '')), 'status': 'pending', 'created_at': datetime.now(pytz.UTC).isoformat()}
        if action_type == 'update_crm':
            task_data['category'] = 'crm'
            task_data['title'] = f"CRM Update: {action.get('customer_name', '')}" if action.get('customer_name') else action_title
        elif action_type == 'send_email':
            task_data['category'] = 'email'
        elif action_type == 'create_calendar_event':
            task_data['category'] = 'calendar'
            task_data['due_date'] = action.get('due_date')
        elif action_type == 'change_deal_status':
            task_data['category'] = 'deals'
        tm.supabase.table('tasks').insert(task_data).execute()
        tm.supabase.table('pending_actions').update({'status': 'approved', 'processed_at': datetime.now(pytz.UTC).isoformat()}).eq('token', token).execute()
        return f"""<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#dcfce7;border-radius:12px;padding:30px"><h2 style="color:#166534">Action Approved</h2><p><strong>{action_title}</strong></p><p>The action has been executed.</p><a href="https://www.jottask.app/dashboard" style="display:inline-block;margin-top:16px;padding:10px 24px;background:#22c55e;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Dashboard</a></div></body></html>"""
    except Exception as e:
        print(f'Error approving action: {e}')
        return ERROR_TEMPLATE.format(error=f'Error: {str(e)}'), 500


@app.route('/action/reject')
def reject_action():
    """Skip/reject a pending Tier 2 action"""
    token = request.args.get('token')
    if not token:
        return ERROR_TEMPLATE.format(error="Missing token"), 400
    try:
        import json as _json
        result = tm.supabase.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()
        if not result.data:
            already = tm.supabase.table('pending_actions').select('status').eq('token', token).execute()
            if already.data:
                st = already.data[0]['status']
                return f"""<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fef3c7;border-radius:12px;padding:30px"><h2>Already Processed</h2><p>This action was already <strong>{st}</strong>.</p></div></body></html>"""
            return ERROR_TEMPLATE.format(error='Action not found or expired'), 404
        action_data = result.data[0]
        action = _json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_title = action.get('title', 'Unknown action')
        tm.supabase.table('pending_actions').update({'status': 'rejected', 'processed_at': datetime.now(pytz.UTC).isoformat()}).eq('token', token).execute()
        return f"""<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Action Skipped</h2><p><strong>{action_title}</strong></p><p>This action has been skipped.</p><a href="https://www.jottask.app/dashboard" style="display:inline-block;margin-top:16px;padding:10px 24px;background:#6b7280;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Dashboard</a></div></body></html>"""
    except Exception as e:
        print(f'Error rejecting action: {e}')
        return ERROR_TEMPLATE.format(error=f'Error: {str(e)}'), 500


@app.route('/action/edit')
def edit_action():
    """Show pending action details"""
    token = request.args.get('token')
    if not token:
        return ERROR_TEMPLATE.format(error="Missing token"), 400
    try:
        import json as _json
        result = tm.supabase.table('pending_actions').select('*').eq('token', token).execute()
        if not result.data:
            return ERROR_TEMPLATE.format(error='Action not found'), 404
        action_data = result.data[0]
        action = _json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_title = action.get('title', 'Unknown action')
        action_type_display = action.get('action_type', '').replace('_', ' ').upper()
        description = action.get('description', action.get('crm_notes', ''))
        customer = action.get('customer_name', '')
        status = action_data['status']
        customer_html = f'<p><strong>Customer:</strong> {customer}</p>' if customer else ''
        if status == 'pending':
            buttons = f'<div style="text-align:center;margin-top:20px"><a href="/action/approve?token={token}" style="display:inline-block;padding:10px 24px;background:#22c55e;color:white;text-decoration:none;border-radius:8px;font-weight:bold;margin-right:8px">Approve</a><a href="/action/reject?token={token}" style="display:inline-block;padding:10px 24px;background:#ef4444;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Skip</a></div>'
        else:
            buttons = f'<p style="text-align:center;color:#666">Already {status}.</p>'
        return f"""<html><body style="font-family:-apple-system,sans-serif;max-width:600px;margin:50px auto"><div style="background:#eff6ff;border-radius:12px;padding:30px"><h2 style="color:#1e40af;text-align:center">Action Details</h2><div style="background:white;border-radius:8px;padding:20px;margin:16px 0"><p><strong>Type:</strong> {action_type_display}</p><p><strong>Title:</strong> {action_title}</p>{customer_html}<p><strong>Details:</strong> {description}</p><p><strong>Status:</strong> {status}</p></div>{buttons}</div></body></html>"""
    except Exception as e:
        print(f'Error loading action: {e}')
        return ERROR_TEMPLATE.format(error=f'Error: {str(e)}'), 500

