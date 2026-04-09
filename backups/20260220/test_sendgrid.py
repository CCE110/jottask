from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import os
from datetime import datetime

print("🧪 Testing SendGrid configuration...")

api_key = os.getenv('SENDGRID_API_KEY')
print(f'✓ API Key present: {bool(api_key)}')
if api_key:
    print(f'✓ API Key starts with: {api_key[:10]}...')

message = Mail(
    from_email='rob@cloudcleanenergy.com.au',
    to_emails='rob@cloudcleanenergy.com.au',
    subject='URGENT TEST - SendGrid from Railway',
    html_content=f'<h1>SendGrid Test</h1><p>If you see this, SendGrid is working! Sent at {datetime.now()}</p>'
)

try:
    sg = SendGridAPIClient(api_key)
    response = sg.send(message)
    print(f'✓ Status: {response.status_code}')
    if 200 <= response.status_code < 300:
        print('✅ SUCCESS! Email sent via SendGrid')
        print('📧 Check rob@cloudcleanenergy.com.au NOW')
    else:
        print(f'❌ Failed with status {response.status_code}')
        print(f'Response: {response.body}')
except Exception as e:
    print(f'❌ ERROR: {e}')
    import traceback
    traceback.print_exc()
