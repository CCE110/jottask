import os
import sys
from datetime import datetime, timedelta
from task_manager import TaskManager
from email_processor import EmailProcessor
import pytz

class HistoricalEmailProcessor:
    def __init__(self):
        self.tm = TaskManager()
        self.ep = EmailProcessor()
        
    def process_last_14_hours(self):
        """Process emails from the last 14 hours"""
        print("🔍 Processing emails from the last 14 hours...")
        
        # Calculate time range
        now = datetime.now(pytz.UTC)
        start_time = now - timedelta(hours=14)
        
        print(f"📅 Time range: {start_time.strftime('%Y-%m-%d %H:%M')} to {now.strftime('%Y-%m-%d %H:%M')} UTC")
        
        # Get emails in time range
        emails = self.ep.get_emails_since(start_time)
        
        if not emails:
            print("📭 No emails found in the last 14 hours")
            return
            
        print(f"📧 Found {len(emails)} emails to process")
        
        processed_count = 0
        
        for email_data in emails:
            try:
                # Process each email
                result = self.ep.process_email_content(
                    email_data['subject'],
                    email_data['body'],
                    email_data['sender'],
                    email_data['received_time']
                )
                
                if result:
                    processed_count += 1
                    print(f"✅ Processed: {email_data['subject'][:50]}...")
                else:
                    print(f"⚠️ Skipped: {email_data['subject'][:50]}...")
                    
            except Exception as e:
                print(f"❌ Error processing email: {str(e)}")
        
        print(f"🎉 Successfully processed {processed_count}/{len(emails)} emails")
        
        # Generate and send daily summary
        if processed_count > 0:
            print("📊 Generating daily summary...")
            self.ep.send_daily_summary()
            print("✅ Daily summary sent!")
        
        return processed_count

if __name__ == "__main__":
    processor = HistoricalEmailProcessor()
    processed = processor.process_last_14_hours()
    
    if processed > 0:
        print(f"🎯 Historical processing complete! {processed} emails processed and daily summary sent.")
    else:
        print("📭 No historical emails to process.")
