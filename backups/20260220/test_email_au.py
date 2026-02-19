import smtplib
from email.mime.text import MIMEText

# Test with Australian Zoho servers
try:
    print("🔌 Testing SMTP with Australian Zoho servers...")
    print("   Server: smtp.zoho.com.au")
    print("   Port: 465 (SSL)")
    
    server = smtplib.SMTP_SSL('smtp.zoho.com.au', 465)
    server.login('rob@cloudcleanenergy.com.au', 'ZWrxY5g5Ew96')
    print("✅ Authentication successful!")
    server.quit()
    
except Exception as e:
    print(f"❌ Error: {e}")
