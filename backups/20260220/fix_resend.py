with open('enhanced_task_manager.py', 'r') as f:
    lines = f.readlines()

new_method = '''    def send_html_email(self, to_email: str, subject: str, html_body: str, plain_body: str) -> bool:
        """Send HTML email using Resend API"""
        import requests
        import os
        
        resend_api_key = os.getenv('RESEND_API_KEY')
        from_email = 'rob@cloudcleanenergy.com.au'
        
        print(f"📧 Sending via Resend API: '{subject[:50]}...' to {to_email}")
        
        try:
            response = requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {resend_api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'from': from_email,
                    'to': [to_email],
                    'subject': subject,
                    'html': html_body
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Email sent! ID: {result.get('id')}")
                return True
            else:
                print(f"❌ Error {response.status_code}: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return False
'''

start_line = None
for i, line in enumerate(lines):
    if 'def send_html_email(self, to_email: str' in line:
        start_line = i
        break

if start_line:
    end_line = None
    for i in range(start_line + 1, len(lines)):
        if lines[i].startswith('    def ') or lines[i].startswith('class '):
            end_line = i
            break
    
    if end_line is None:
        end_line = len(lines)
    
    new_lines = lines[:start_line] + [new_method + '\n'] + lines[end_line:]
    
    with open('enhanced_task_manager.py', 'w') as f:
        f.writelines(new_lines)
    
    print(f"✅ Replaced at lines {start_line+1}-{end_line}")
else:
    print("❌ Method not found")
