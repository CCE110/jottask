import os
import pytz
from datetime import datetime

# Add timezone awareness to cloud_email_processor.py
timezone_fix = '''
import pytz
from datetime import datetime

# Set AEST timezone
AEST = pytz.timezone('Australia/Brisbane')

def get_aest_time():
    """Get current time in AEST"""
    return datetime.now(AEST)

def is_daily_summary_time():
    """Check if it's 8AM AEST"""
    current_time = get_aest_time()
    return current_time.hour == 8 and current_time.minute == 0
'''

print("🕐 Adding AEST timezone support...")
print("This will fix the timezone for daily summaries and email timestamps")
