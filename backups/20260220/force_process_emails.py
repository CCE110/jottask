#!/usr/bin/env python3
import sys
import os
sys.path.append('/app')  # For Railway cloud environment

from cloud_email_processor import CloudEmailProcessor

if __name__ == "__main__":
    print("🔄 Force processing all emails...")
    processor = CloudEmailProcessor()
    
    # Force check emails regardless of "new" status
    processor.ep.check_and_process_emails(force_all=True)
    
    # Send daily summary
    processor.ep.send_daily_summary()
    print("✅ Force processing complete!")
