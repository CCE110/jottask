#!/usr/bin/env python3
"""
PATCH APP.PY - Adds approval routes for Jottask v2
===================================================
Run this script on your Mac:
    python3 patch_app.py

It will:
1. Download the latest app.py from GitHub
2. Add the 3 approval routes (/action/approve, /action/reject, /action/edit)
3. Add the ERROR_TEMPLATE constant
4. Save as 'app_patched.py' in the same folder
5. You then upload app_patched.py to GitHub (rename to app.py)
"""

import urllib.request
import sys
import os

REPO_URL = "https://raw.githubusercontent.com/CCE110/jottask/main/app.py"

# The ERROR_TEMPLATE to add near the top (after imports/init)
ERROR_TEMPLATE_CODE = '''
# Error template for approval routes
ERROR_TEMPLATE = """<html><body style="font-family:-apple-system,sans-serif;max-width:500px;margin:50px auto;text-align:center"><div style="background:#fee2e2;border-radius:12px;padding:30px"><h2 style="color:#991b1b">Error</h2><p>{error}</p><a href="https://www.jottask.app/dashboard" style="color:#3b82f6">Dashboard</a></div></body></html>"""
'''

# The 3 approval routes to insert before @app.route('/action')
APPROVAL_ROUTES = '''

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

'''

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Step 1: Download app.py from GitHub
    print("Downloading app.py from GitHub...")
    try:
        req = urllib.request.Request(REPO_URL)
        req.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(req) as response:
            content = response.read().decode('utf-8')
        print(f"  Downloaded: {len(content)} bytes, {content.count(chr(10))} lines")
    except Exception as e:
        print(f"ERROR downloading: {e}")
        print("\nFallback: Place your app.py in the same folder as this script and re-run.")
        # Try to read local app.py
        local_path = os.path.join(script_dir, 'app.py')
        if os.path.exists(local_path):
            with open(local_path, 'r') as f:
                content = f.read()
            print(f"  Using local app.py: {len(content)} bytes")
        else:
            print("  No local app.py found. Exiting.")
            sys.exit(1)

    # Step 2: Check if routes already exist
    if "/action/approve" in content:
        print("\n*** Approval routes already exist in app.py! No changes needed. ***")
        sys.exit(0)

    # Step 3: Add ERROR_TEMPLATE after the imports/init section
    # Find "etm = EnhancedTaskManager()" or similar init line
    lines = content.split('\n')
    error_template_inserted = False

    if 'ERROR_TEMPLATE' not in content:
        # Insert after the initialization block (after etm = EnhancedTaskManager())
        for i, line in enumerate(lines):
            if 'EnhancedTaskManager()' in line:
                # Insert ERROR_TEMPLATE a couple lines after
                insert_idx = i + 1
                # Skip any blank lines
                while insert_idx < len(lines) and lines[insert_idx].strip() == '':
                    insert_idx += 1
                lines.insert(insert_idx, ERROR_TEMPLATE_CODE)
                error_template_inserted = True
                print(f"  Added ERROR_TEMPLATE at line {insert_idx + 1}")
                break

        if not error_template_inserted:
            # Fallback: insert after 'import pytz' line
            for i, line in enumerate(lines):
                if 'import pytz' in line:
                    lines.insert(i + 2, ERROR_TEMPLATE_CODE)
                    error_template_inserted = True
                    print(f"  Added ERROR_TEMPLATE at line {i + 3}")
                    break
    else:
        print("  ERROR_TEMPLATE already exists")

    content = '\n'.join(lines)

    # Step 4: Insert approval routes before @app.route('/action')
    # Find the FIRST @app.route('/action') that is NOT /action/approve etc
    lines = content.split('\n')
    insert_line = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "@app.route('/action')" or stripped == '@app.route("/action")':
            insert_line = i
            break

    if insert_line == -1:
        print("ERROR: Could not find @app.route('/action') in app.py!")
        print("  Trying alternative: looking for '# ROUTES' comment...")
        for i, line in enumerate(lines):
            if '# ROUTES' in line.upper():
                insert_line = i + 1
                print(f"  Found # ROUTES at line {i + 1}, inserting after")
                break

    if insert_line == -1:
        print("ERROR: Could not find insertion point. Manual edit required.")
        sys.exit(1)

    print(f"  Inserting approval routes before line {insert_line + 1}")

    # Insert the routes
    route_lines = APPROVAL_ROUTES.split('\n')
    for j, route_line in enumerate(route_lines):
        lines.insert(insert_line + j, route_line)

    content = '\n'.join(lines)

    # Step 5: Save patched file
    output_path = os.path.join(script_dir, 'app_patched.py')
    with open(output_path, 'w') as f:
        f.write(content)

    new_line_count = content.count('\n')
    print(f"\nSUCCESS! Patched app.py saved as: app_patched.py")
    print(f"  New file: {len(content)} bytes, {new_line_count} lines")
    print(f"  Location: {output_path}")
    print(f"\nNext steps:")
    print(f"  1. Go to https://github.com/CCE110/jottask")
    print(f"  2. Click on app.py")
    print(f"  3. Delete app.py (click ... menu > Delete file > Commit)")
    print(f"  4. Click 'Add file' > 'Upload files'")
    print(f"  5. Drag app_patched.py in (rename to app.py first!)")
    print(f"  6. Commit changes")
    print(f"  7. Railway will auto-deploy")

if __name__ == '__main__':
    main()
