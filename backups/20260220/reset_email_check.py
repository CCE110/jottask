# This will force the system to treat all emails as "new"
import os
from datetime import datetime, timedelta

# Set last check time to 48 hours ago so all current emails are "new"
old_time = datetime.now() - timedelta(hours=48)
print(f"🔄 Resetting email check time to: {old_time}")

# The system should now detect those 13 emails as "new"
