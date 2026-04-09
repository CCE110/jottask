
    def get_emails_since(self, since_time):
        """Get emails received since a specific time"""
        try:
            # Connect to Gmail
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            mail.login('robcrm.ai@gmail.com', os.getenv('GMAIL_APP_PASSWORD'))
            mail.select('inbox')
            
            # Format date for IMAP search
            since_date = since_time.strftime('%d-%b-%Y')
            
            # Search for emails since the date
            status, messages = mail.search(None, f'SINCE {since_date}')
            
            if status != 'OK':
                return []
                
            email_ids = messages[0].split()
            emails = []
            
            for email_id in email_ids[-50:]:  # Limit to last 50 emails
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                if status == 'OK':
                    email_message = email.message_from_bytes(msg_data[0][1])
                    
                    # Parse email date
                    date_str = email_message.get('Date')
                    if date_str:
                        email_date = email.utils.parsedate_to_datetime(date_str)
                        
                        # Only include emails within our time range
                        if email_date >= since_time:
                            subject = email_message.get('Subject', '')
                            sender = email_message.get('From', '')
                            
                            # Get email body
                            body = ''
                            if email_message.is_multipart():
                                for part in email_message.walk():
                                    if part.get_content_type() == "text/plain":
                                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        break
                            else:
                                body = email_message.get_payload(decode=True).decode('utf-8', errors='ignore')
                            
                            emails.append({
                                'subject': subject,
                                'body': body,
                                'sender': sender,
                                'received_time': email_date
                            })
            
            mail.close()
            mail.logout()
            
            return emails
            
        except Exception as e:
            print(f"❌ Error getting historical emails: {str(e)}")
            return []
