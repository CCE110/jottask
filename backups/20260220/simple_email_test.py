import os
import imaplib

def test_email_connection():
    try:
        print("🔗 Testing Gmail connection...")
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login('robcrm.ai@gmail.com', 'sgho tbwr optz yxie')
        mail.select('inbox')
        
        # Get email count
        status, messages = mail.search(None, 'ALL')
        if status == 'OK':
            email_count = len(messages[0].split()) if messages[0] else 0
            print(f"📧 Total emails found: {email_count}")
            
            # Get recent emails
            if email_count > 0:
                email_ids = messages[0].split()
                for email_id in email_ids[-5:]:  # Last 5 emails
                    status, msg_data = mail.fetch(email_id, '(ENVELOPE)')
                    print(f"📨 Email ID: {email_id}")
        
        mail.close()
        mail.logout()
        print("✅ Connection test successful")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_email_connection()
