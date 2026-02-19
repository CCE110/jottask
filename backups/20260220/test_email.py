import smtplib
from email.mime.text import MIMEText

# Test basic SMTP connection
try:
    print("🔌 Testing SMTP connection to Zoho...")
    server = smtplib.SMTP('smtp.zoho.com', 587)
    server.starttls()
    print("✅ STARTTLS successful")
    
    # Try authentication
    server.login('rob@cloudcleanenergy.com.au', 'fcvANSJdqgFW')
    print("✅ Authentication successful!")
    server.quit()
    
except Exception as e:
    print(f"❌ Error: {e}")
    print("\n💡 Possible solutions:")
    print("   1. Double-check the app password is correct")
    print("   2. Make sure SMTP is enabled in Zoho settings")
    print("   3. Try generating a new app password")
