import smtplib
from email.mime.text import MIMEText

# Try different port
try:
    print("🔌 Testing SMTP with port 465 (SSL)...")
    server = smtplib.SMTP_SSL('smtp.zoho.com', 465)
    server.login('rob@cloudcleanenergy.com.au', 'fcvANSJdqgFW')
    print("✅ Authentication successful on port 465!")
    server.quit()
    
except Exception as e:
    print(f"❌ Port 465 failed: {e}")
    
    # Try port 587 with different approach
    try:
        print("🔌 Testing SMTP with port 587 (different method)...")
        server = smtplib.SMTP('smtp.zoho.com', 587)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login('rob@cloudcleanenergy.com.au', 'fcvANSJdqgFW')
        print("✅ Authentication successful on port 587!")
        server.quit()
    except Exception as e2:
        print(f"❌ Port 587 also failed: {e2}")
