#!/usr/bin/env python3
"""DSW Lead Poller - polls Pipereply for new contacts assigned to Rob Lowe,
creates macOS contacts, Jottask tasks, and emails a brief."""
import os, json, requests, subprocess, base64
from datetime import datetime, timedelta
from dotenv import load_dotenv
from anthropic import Anthropic
import resend

load_dotenv()

TOKEN = os.getenv("PIPEREPLY_TOKEN")
LOCATION_ID = os.getenv("PIPEREPLY_LOCATION_ID")
NOTIFY_EMAIL = "rob.l@directsolarwholesaler.com.au"
FROM_EMAIL = "jottask@flowquote.ai"
PROCESSED_FILE = os.path.expanduser("~/.dsw_processed_leads.json")
BASE_URL = "https://services.leadconnectorhq.com"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Version": "2021-07-28"}
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
resend.api_key = os.getenv("RESEND_API_KEY")


def load_processed():
    try:
        with open(PROCESSED_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_processed(ids):
    with open(PROCESSED_FILE, "w") as f:
        json.dump(list(ids), f)


def get_recent_contacts():
    since = (datetime.utcnow() - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = requests.get(f"{BASE_URL}/contacts/", headers=HEADERS, params={
        "locationId": LOCATION_ID, "startAfterDate": since,
    })
    if resp.status_code != 200:
        print(f"Error: {resp.status_code} {resp.text[:200]}")
        return []
    contacts = resp.json().get("contacts", [])
    rob = [c for c in contacts if is_rob_lead(c)]
    print(f"Found {len(contacts)} recent contacts, {len(rob)} for Rob")
    return rob


def is_rob_lead(c):
    tags = c.get("tags") or []
    # SolarQuotes leads are tagged solar_quotes_lead
    # Also check if recently added (within 30 min)
    from datetime import timezone
    date_added = c.get("dateAdded", "")
    if not date_added:
        return False
    try:
        added = datetime.fromisoformat(date_added.replace("Z", "+00:00"))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        is_recent = added > cutoff
    except:
        is_recent = False
    has_solar_tag = any("solar_quotes_lead" in t.lower() for t in tags)
    assigned = (c.get("assignedTo") or "")
    return is_recent and (has_solar_tag or bool(assigned))


def summarise(contact):
    name = contact.get("contactName", "Unknown")
    phone = contact.get("phone", "N/A")
    email = contact.get("email", "N/A")
    address = ", ".join(filter(None, [
        contact.get("address1", ""), contact.get("city", ""), contact.get("state", "")
    ]))
    source = contact.get("source", "Unknown")
    notes = (contact.get("notes") or "")[:2000]
    resp = client.messages.create(
        model="claude-opus-4-5", max_tokens=400,
        messages=[{"role": "user", "content":
            f"Summarise this solar lead concisely for a sales rep. Include: "
            f"system size (kW), monthly electricity bill, property type, "
            f"key motivations, any urgency. Skip missing info.\n\n"
            f"Name: {name}\nPhone: {phone}\nAddress: {address}\n"
            f"Source: {source}\nNotes: {notes}"}]
    )
    return resp.content[0].text, name, phone, address, source, email


def create_contact(name, phone, email, address, source):
    parts = name.strip().split()
    first = parts[0] if parts else "Unknown"
    last = (" ".join(parts[1:]) + " DSW").strip() if len(parts) > 1 else "DSW"
    note = f"DSW Lead | {source} | {datetime.now().strftime('%d %b %Y')}"
    first = first.replace('"', "").replace("'", "")
    last = last.replace('"', "").replace("'", "")
    phone_clean = phone.replace('"', "")
    note_clean = note.replace('"', "").replace("'", "")
    script = f"""tell application "Contacts"
    set p to make new person with properties {{first name:"{first}", last name:"{last}", note:"{note_clean}"}}
    tell p
        make new phone at end of phones with properties {{label:"mobile", value:"{phone_clean}"}}
    end tell
    save
end tell"""
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        print(f"Contact {'created' if r.returncode == 0 else 'error'}: {first} {last}")
    except Exception as e:
        print(f"Contact error: {e}")


def create_task(name, phone, summary):
    try:
        from task_manager import TaskManager
        tm = TaskManager()
        users = tm.supabase.table("users").select("id").eq(
            "email", "rob@cloudcleanenergy.com.au"
        ).execute()
        if not users.data:
            print("User not found")
            return
        task = {
            "user_id": users.data[0]["id"],
            "title": f"Call {name} - New DSW Lead",
            "description": f"Phone: {phone}\n\n{summary}",
            "due_date": (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d"),
            "due_time": "09:00",
            "priority": "high",
            "status": "pending",
            "category": "DSW Solar",
            "client_name": name,
            "source": "dsw_lead_poller"
        }
        result = tm.supabase.table("tasks").insert(task).execute()
        print(f"Task created: {bool(result.data)}")
    except Exception as e:
        print(f"Task error: {e}")


def send_email(name, phone, address, source, summary):
    now = datetime.now().strftime("%d %b %Y %I:%M %p")
    html = f"""<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
<div style="background:#1e40af;color:white;padding:20px;border-radius:8px 8px 0 0">
<h2 style="margin:0">New DSW Lead Assigned</h2>
<p style="opacity:.8;margin:4px 0 0">{now}</p></div>
<div style="padding:20px;border:1px solid #e2e8f0">
<h3 style="color:#1e40af;margin-top:0">{name}</h3>
<p><b>Phone:</b> <a href="tel:{phone}">{phone}</a></p>
<p><b>Address:</b> {address}</p>
<p><b>Source:</b> {source}</p>
<hr style="border:1px solid #e2e8f0">
<h4>Lead Summary</h4>
<p style="white-space:pre-line">{summary}</p></div>
<div style="background:#1e40af;padding:15px;text-align:center;border-radius:0 0 8px 8px">
<a href="https://jottask.app/dashboard" style="color:white;font-weight:bold">Open Jottask Dashboard</a>
</div></div>"""
    try:
        resend.Emails.send({
            "from": f"Jottask <{FROM_EMAIL}>",
            "to": [NOTIFY_EMAIL],
            "subject": f"New Lead: {name} - Call ASAP",
            "html": html
        })
        print(f"Email sent: {name}")
    except Exception as e:
        print(f"Email error: {e}")


def main():
    print(f"\nDSW Lead Poller - {datetime.now().strftime('%H:%M:%S')}")
    if not TOKEN:
        print("PIPEREPLY_TOKEN not set")
        return
    processed = load_processed()
    new_leads = 0
    for c in get_recent_contacts():
        cid = c.get("id")
        if not cid or cid in processed:
            continue
        print(f"Processing: {c.get('contactName')}")
        try:
            summary, name, phone, address, source, email = summarise(c)
            create_contact(name, phone, email, address, source)
            create_task(name, phone, summary)
            send_email(name, phone, address, source, summary)
            processed.add(cid)
            new_leads += 1
            print(f"Done: {name}")
        except Exception as e:
            print(f"Error: {e}")
    save_processed(processed)
    print(f"Complete - {new_leads} new leads processed")


if __name__ == "__main__":
    main()
