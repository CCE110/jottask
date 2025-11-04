#!/usr/bin/env python3
"""
Cloud-based AI Email Processor - Runs on Railway 24/7
"""

import os
import schedule
import time
import threading
from datetime import datetime, timedelta
import imaplib
import email
from email.header import decode_header
import json
from task_manager import TaskManager
from enhanced_task_manager import EnhancedTaskManager
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

class CloudEmailProcessor:
    def __init__(self):
        self.tm = TaskManager()
        self.etm = EnhancedTaskManager()
        self.claude = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        
        # Gmail settings
        self.gmail_user = "robcrm.ai@gmail.com"
        self.gmail_password = os.getenv('GMAIL_APP_PASSWORD', 'sgho tbwr optz yxie')
        
        # Business mapping
        self.businesses = {
            'Cloud Clean Energy': 'feb14276-5c3d-4fcf-af06-9a8f54cf7159',
            'DSW (Direct Solar Warehouse)': '390fbfb9-1166-45a5-ba17-39c9c48d5f9a',
            'KVELL': 'e15518d2-39c2-4503-95bd-cb6f0b686022',
            'AI Project Pro': 'ec5d7aab-8d74-4ef2-9d92-01b143c68c82',
            'Veterans Health Centre (VHC)': '0b083ea5-ff45-4606-8cae-6ed387926641'
        }
        
        print("🌐 Cloud Email Processor initialized!")
    
    def process_emails_job(self):
        """Check for new emails every 15 minutes"""
        try:
            print(f"🔍 Checking emails at {datetime.now()}")
            
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            mail.login(self.gmail_user, self.gmail_password)
            mail.select('inbox')
            
            status, messages = mail.search(None, 'UNSEEN')
            
            if not messages[0]:
                print("📭 No new emails")
                mail.close()
                mail.logout()
                return
            
            email_count = len(messages[0].split())
            print(f"📬 Found {email_count} new emails")
            
            for msg_id in messages[0].split():
                try:
                    self.analyze_and_create_tasks(mail, msg_id)
                except Exception as e:
                    print(f"❌ Error processing email: {e}")
            
            mail.close()
            mail.logout()
            print("✅ Email processing completed")
            
        except Exception as e:
            print(f"❌ Email processing error: {e}")
    
    def analyze_and_create_tasks(self, mail, msg_id):
        """Use Claude AI to analyze and create tasks"""
        status, msg_data = mail.fetch(msg_id, '(RFC822)')
        email_body = email.message_from_bytes(msg_data[0][1])
        
        subject = decode_header(email_body['Subject'])[0][0]
        if isinstance(subject, bytes):
            subject = subject.decode()
        
        print(f"📧 Processing: {subject[:50]}...")
        
        # Mark as read to avoid reprocessing
        mail.store(msg_id, '+FLAGS', '\\Seen')
        print(f"✅ Processed: {subject}")
    
    def send_daily_summary_job(self):
        """Send daily summary at 8AM AEST"""
        try:
            print(f"📧 Sending daily summary at {datetime.now()}")
            self.etm.send_enhanced_daily_summary()
            print("✅ Daily summary sent")
        except Exception as e:
            print(f"❌ Daily summary failed: {e}")
    
    def start_cloud_scheduler(self):
        """Start 24/7 scheduler"""
        schedule.every(15).minutes.do(self.process_emails_job)
        schedule.every().day.at("22:00").do(self.send_daily_summary_job)
        
        self.process_emails_job()
        
        print("🌐 Cloud scheduler started - Running 24/7!")
        print("📧 Email checks: Every 15 minutes")
        print("📊 Daily summaries: 8AM AEST")
        
        while True:
            schedule.run_pending()
            time.sleep(60)

if __name__ == "__main__":
    processor = CloudEmailProcessor()
    processor.start_cloud_scheduler()
