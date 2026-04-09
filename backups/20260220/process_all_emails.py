#!/usr/bin/env python3
import imaplib
import email
from email.header import decode_header
import json
from task_manager import TaskManager
from anthropic import Anthropic
import os

tm = TaskManager()
claude = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

# Connect to Gmail
mail = imaplib.IMAP4_SSL('imap.gmail.com')
mail.login('robcrm.ai@gmail.com', 'sgho tbwr optz yxie')
mail.select('inbox')

# Find the Neil Westgarth email specifically
status, messages = mail.search(None, 'SUBJECT "Neil Westgarth"')

if messages[0]:
    msg_id = messages[0].split()[0]
    status, msg_data = mail.fetch(msg_id, '(RFC822)')
    email_body = email.message_from_bytes(msg_data[0][1])
    
    # Extract content
    subject = decode_header(email_body['Subject'])[0][0]
    if isinstance(subject, bytes):
        subject = subject.decode()
    
    sender = email_body['From']
    
    # Get email content
    content = ""
    if email_body.is_multipart():
        for part in email_body.walk():
            if part.get_content_type() == "text/plain":
                content += part.get_payload(decode=True).decode('utf-8', errors='ignore')
    else:
        content = email_body.get_payload(decode=True).decode('utf-8', errors='ignore')
    
    print(f"📧 Found Neil Westgarth email!")
    print(f"Subject: {subject}")
    print(f"From: {sender}")
    print(f"Content preview: {content[:200]}...")
    
    # Ask Claude to analyze
    prompt = f"""
Analyze this email from Rob's solar business and create specific tasks:

EMAIL:
From: {sender}
Subject: {subject}
Content: {content}

Rob specifically requested:
1. "Add Neil as a contact"
2. "Set reminder to followup this issue tomorrow at 8am"

The email discusses Neil Westgarth considering a solar system under $7K budget.

Create tasks in JSON format:
{{
    "create_tasks": true,
    "tasks": [
        {{
            "title": "Add Neil Westgarth as contact",
            "description": "Add Neil Westgarth (3 Linthorpe Ct) as new contact - considering solar system under $7K budget",
            "business": "Cloud Clean Energy",
            "priority": "medium",
            "due_date": "2025-11-05",
            "due_time": null,
            "is_meeting": false
        }},
        {{
            "title": "Follow up with Neil Westgarth on solar quote",
            "description": "Follow up on Neil's solar system inquiry - budget under $7K, also considering Fox system installation",
            "business": "Cloud Clean Energy", 
            "priority": "high",
            "due_date": "2025-11-05",
            "due_time": "08:00",
            "is_meeting": false
        }}
    ]
}}
"""

    try:
        response = claude.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        analysis = json.loads(response.content[0].text)
        
        print("\n🤖 AI Analysis Complete!")
        print("Tasks to create:")
        
        businesses = {
            'Cloud Clean Energy': 'feb14276-5c3d-4fcf-af06-9a8f54cf7159',
            'AI Project Pro': 'ec5d7aab-8d74-4ef2-9d92-01b143c68c82'
        }
        
        for i, task in enumerate(analysis['tasks'], 1):
            print(f"\n{i}. {task['title']}")
            print(f"   Business: {task['business']}")
            print(f"   Due: {task['due_date']}{' at ' + task['due_time'] if task.get('due_time') else ''}")
            print(f"   Priority: {task['priority']}")
            
            # Create the task
            business_id = businesses[task['business']]
            result = tm.supabase.table('tasks').insert({
                'business_id': business_id,
                'title': task['title'],
                'description': task['description'],
                'due_date': task['due_date'],
                'due_time': task['due_time'],
                'priority': task['priority'],
                'is_meeting': task['is_meeting']
            }).execute()
            
            if result.data:
                created_task = result.data[0]
                print(f"   ✅ Task created!")
                
                # Send confirmation email
                tm.send_task_confirmation_email(created_task['id'])
                print(f"   📧 Confirmation email sent!")
            else:
                print(f"   ❌ Failed to create task")
        
    except Exception as e:
        print(f"❌ Error processing: {e}")

mail.close()
mail.logout()
