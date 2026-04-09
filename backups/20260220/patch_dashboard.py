#!/usr/bin/env python3
"""
PATCH DASHBOARD.PY - Adds approval routes for Jottask v2
=========================================================
Run on your Mac:
    cd ~/Documents/AI\ Project\ Pro/DSW\ folder\ for\ AI\ /jottask
    python3 patch_dashboard.py

It will:
1. Download dashboard.py from GitHub
2. Add the 3 approval routes (/action/approve, /action/reject, /action/edit)
3. Save as 'dashboard_patched.py'
4. Rename to dashboard.py, then upload to GitHub
"""

import urllib.request
import sys
import os

REPO_URL = "https://raw.githubusercontent.com/CCE110/jottask/main/dashboard.py"

# The approval routes to add at the end of dashboard.py (before if __name__)
APPROVAL_ROUTES = '''

# ============================================
# V2 APPROVAL ROUTES (Tiered Action System)
# ============================================

@app.route('/action/approve')
def approve_action():
    """Approve a pending Tier 2 action via email button click"""
    token = request.args.get('token')
    if not token:
        return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>Missing token</p></div></body></html>', 400
    try:
        import json as _json
        from datetime import datetime
        import pytz
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_SERVICE_KEY')
        from supabase import create_client
        sb = create_client(supabase_url, supabase_key)
        result = sb.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()
        if not result.data:
            already = sb.table('pending_actions').select('status').eq('token', token).execute()
            if already.data:
                st = already.data[0]['status']
                return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fef3c7;border-radius:12px;padding:30px"><h2>Already Processed</h2><p>This action was already <strong>{st}</strong>.</p><a href="https://www.jottask.app/dashboard" style="color:#3b82f6">Dashboard</a></div></body></html>'
            return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Not Found</h2><p>Action not found or expired</p></div></body></html>', 404
        action_data = result.data[0]
        action = _json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_type = action.get('action_type', '')
        action_title = action.get('title', 'Unknown action')
        task_data = {
            'title': action_title,
            'description': action.get('description', action.get('crm_notes', '')),
            'status': 'pending',
            'priority': 'medium',
            'created_at': datetime.now(pytz.UTC).isoformat(),
            'business_id': os.getenv('BUSINESS_ID_CCE', 'feb14276-5c3d-4fcf-af06-9a8f54cf7159'),
            'user_id': os.getenv('ROB_USER_ID', 'e515407e-dbd6-4331-a815-1878815c89bc'),
            'client_name': action.get('customer_name', ''),
        }
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
        sb.table('tasks').insert(task_data).execute()
        sb.table('pending_actions').update({
            'status': 'approved',
            'processed_at': datetime.now(pytz.UTC).isoformat()
        }).eq('token', token).execute()
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#dcfce7;border-radius:12px;padding:30px"><h2 style="color:#166534">Action Approved</h2><p><strong>{action_title}</strong></p><p>The action has been executed.</p><a href="https://www.jottask.app/dashboard" style="display:inline-block;margin-top:16px;padding:10px 24px;background:#22c55e;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Dashboard</a></div></body></html>'
    except Exception as e:
        print(f'Error approving action: {e}')
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>{str(e)}</p></div></body></html>', 500


@app.route('/action/reject')
def reject_action():
    """Skip/reject a pending Tier 2 action"""
    token = request.args.get('token')
    if not token:
        return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>Missing token</p></div></body></html>', 400
    try:
        import json as _json
        from datetime import datetime
        import pytz
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_SERVICE_KEY')
        from supabase import create_client
        sb = create_client(supabase_url, supabase_key)
        result = sb.table('pending_actions').select('*').eq('token', token).eq('status', 'pending').execute()
        if not result.data:
            already = sb.table('pending_actions').select('status').eq('token', token).execute()
            if already.data:
                st = already.data[0]['status']
                return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fef3c7;border-radius:12px;padding:30px"><h2>Already Processed</h2><p>This action was already <strong>{st}</strong>.</p></div></body></html>'
            return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Not Found</h2><p>Action not found or expired</p></div></body></html>', 404
        action_data = result.data[0]
        action = _json.loads(action_data['action_data']) if isinstance(action_data['action_data'], str) else action_data['action_data']
        action_title = action.get('title', 'Unknown action')
        sb.table('pending_actions').update({
            'status': 'rejected',
            'processed_at': datetime.now(pytz.UTC).isoformat()
        }).eq('token', token).execute()
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Action Skipped</h2><p><strong>{action_title}</strong></p><p>This action has been skipped.</p><a href="https://www.jottask.app/dashboard" style="display:inline-block;margin-top:16px;padding:10px 24px;background:#6b7280;color:white;text-decoration:none;border-radius:8px;font-weight:bold">Dashboard</a></div></body></html>'
    except Exception as e:
        print(f'Error rejecting action: {e}')
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>{str(e)}</p></div></body></html>', 500


@app.route('/action/edit')
def edit_action():
    """Show pending action details"""
    token = request.args.get('token')
    if not token:
        return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>Missing token</p></div></body></html>', 400
    try:
        import json as _json
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_SERVICE_KEY')
        from supabase import create_client
        sb = create_client(supabase_url, supabase_key)
        result = sb.table('pending_actions').select('*').eq('token', token).execute()
        if not result.data:
            return '<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Not Found</h2><p>Action not found</p></div></body></html>', 404
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
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:600px;margin:50px auto"><div style="background:#eff6ff;border-radius:12px;padding:30px"><h2 style="color:#1e40af;text-align:center">Action Details</h2><div style="background:white;border-radius:8px;padding:20px;margin:16px 0"><p><strong>Type:</strong> {action_type_display}</p><p><strong>Title:</strong> {action_title}</p>{customer_html}<p><strong>Details:</strong> {description}</p><p><strong>Status:</strong> {status}</p></div>{buttons}</div></body></html>'
    except Exception as e:
        print(f'Error loading action: {e}')
        return f'<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>{str(e)}</p></div></body></html>', 500

'''

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Step 1: Download dashboard.py from GitHub
    print("Downloading dashboard.py from GitHub...")
    try:
        req = urllib.request.Request(REPO_URL)
        req.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(req) as response:
            content = response.read().decode('utf-8')
        print(f"  Downloaded: {len(content)} bytes, {content.count(chr(10))} lines")
    except Exception as e:
        print(f"ERROR downloading: {e}")
        local_path = os.path.join(script_dir, 'dashboard.py')
        if os.path.exists(local_path):
            with open(local_path, 'r') as f:
                content = f.read()
            print(f"  Using local dashboard.py: {len(content)} bytes")
        else:
            print("  No local dashboard.py found. Exiting.")
            sys.exit(1)

    # Step 2: Check if routes already exist
    if "/action/approve" in content:
        print("\n*** Approval routes already exist in dashboard.py! No changes needed. ***")
        sys.exit(0)

    # Step 3: Insert approval routes at the end
    # Find if there's an `if __name__` block to insert before
    if "if __name__" in content:
        idx = content.index("if __name__")
        content = content[:idx] + APPROVAL_ROUTES + "\n" + content[idx:]
        print(f"  Inserted approval routes before if __name__ block")
    else:
        # Just append at the end
        content += APPROVAL_ROUTES
        print(f"  Appended approval routes at end of file")

    # Step 4: Save patched file
    output_path = os.path.join(script_dir, 'dashboard_patched.py')
    with open(output_path, 'w') as f:
        f.write(content)

    new_line_count = content.count('\n')
    print(f"\nSUCCESS! Patched dashboard.py saved as: dashboard_patched.py")
    print(f"  New file: {len(content)} bytes, {new_line_count} lines")
    print(f"  Location: {output_path}")
    print(f"\nNext steps:")
    print(f"  1. Rename: mv dashboard_patched.py dashboard.py")
    print(f"  2. Go to https://github.com/CCE110/jottask")
    print(f"  3. Click on dashboard.py > ... menu > Delete file > Commit")
    print(f"  4. Click 'Add file' > 'Upload files'")
    print(f"  5. Drag in the new dashboard.py")
    print(f"  6. Commit > Railway auto-deploys")

if __name__ == '__main__':
    main()
