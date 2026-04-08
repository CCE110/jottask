#!/usr/bin/env python3
import os, json, requests as req, subprocess, time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from anthropic import Anthropic
import resend

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
TOKEN = os.getenv("PIPEREPLY_TOKEN")
LOCATION_ID = os.getenv("PIPEREPLY_LOCATION_ID")
NOTIFY = "rob.l@directsolarwholesaler.com.au"
FROM_EMAIL = "jottask@flowquote.ai"
PROCESSED = os.path.expanduser("~/.dsw_processed_leads.json")
BASE = "https://services.leadconnectorhq.com"
CRM_BASE = "https://app.pipereply.com/v2/location/0k6Ix1hW5QoHuUh2YSru/contacts"
LEAD_TAGS = ["solar_quotes_lead","sem","website","facebook","google","referral"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Version": "2021-07-28"}
STATUS_LABELS = {
    'new_lead':           '🔵 NEW LEAD',
    'intro_call':         '📞 INTRO CALL',
    'site_visit_booked':  '📅 SITE VISIT BOOKED',
    'awaiting_docs':      '📋 AWAITING DOCS',
    'build_quote':        '🔨 BUILD QUOTE',
    'quote_submitted':    '📤 QUOTE SENT',
    'quote_followup':     '🔔 QUOTE FOLLOW UP',
    'revise_quote':       '✏️ REVISE QUOTE',
    'customer_deciding':  '🤔 DECIDING',
    'nurture':            '💧 NURTURE',
    'won':                '🎉 WON',
    'lost':               '❌ LOST',
}
claude = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
resend.api_key = os.getenv("RESEND_API_KEY")
_os = None

def opensolar():
    global _os
    if _os and _os.token: return _os
    from opensolar_connector import OpenSolarConnector
    _os = OpenSolarConnector(email=os.getenv("OPENSOLAR_EMAIL"), password=os.getenv("OPENSOLAR_PASSWORD"))
    _os.authenticate()
    return _os

def load_done():
    try:
        with open(PROCESSED) as f: return set(json.load(f))
    except: return set()

def save_done(ids):
    with open(PROCESSED, "w") as f: json.dump(list(ids), f)

def get_contacts():
    # Fetch last 7 days of contacts - poller tracks processed IDs to avoid duplicates
    r = req.get(f"{BASE}/contacts/", headers=H, params={"locationId": LOCATION_ID, "limit": 100})
    if r.status_code != 200: print("Pipereply error:", r.status_code); return []
    contacts = r.json().get("contacts", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    out = []
    for c in contacts:
        da = c.get("dateAdded", "")
        if not da: continue
        try:
            if datetime.fromisoformat(da.replace("Z", "+00:00")) < cutoff: continue
        except: continue
        tags = " ".join([t.lower() for t in (c.get("tags") or [])])
        if any(lt in tags for lt in LEAD_TAGS): out.append(c)
    print("Found", len(contacts), "contacts,", len(out), "unprocessed DSW leads in last 7 days")
    return out

def get_full(cid):
    r = req.get(f"{BASE}/contacts/{cid}", headers=H)
    if r.ok:
        d = r.json(); return d.get("contact", d)
    return {}

def source(c):
    tags = " ".join([t.lower() for t in (c.get("tags") or [])])
    for s, k in [("SolarQuotes","solar_quotes"),("SEM","sem"),("Facebook","facebook"),("Website","website"),("Referral","referral")]:
        if k in tags: return s
    return c.get("source", "Unknown")

def summarise(name, phone, addr, src, notes, custom):
    # Extract full SolarQuotes notes - sort by length, longest has the full lead data
    extra_parts = [str(f.get("value","")) for f in (custom or []) if f.get("value")]
    extra_parts.sort(key=len, reverse=True)
    extra = chr(10).join(extra_parts)
    prompt = "Summarise this into actionable customer requirements. Ignore duplicates. Ignore: verified phone number, consented to discuss energy plans, lead submitted, requested quotes number, roof ownership confirmed, north facing, supplier info, lead IDs.\n\nFormat exactly (plain text, no ## markdown):\nCUSTOMER REQUIREMENTS\n* [requirement]\n\nPROPERTY\n* [property detail]\n\nKeep: system size kW, solar/battery/both, EV charger, bill amount, payment method, urgency/timeframe, property type/storeys/roof type, motivation, blackout/backup needs, home visit.\nConcise bullets only.\n\nName: "+name+"\nSource: "+src+"\nAddress: "+addr+"\nNotes: "+(extra if len(extra) > len(notes) else notes)[:3000]
    r = claude.messages.create(model="claude-haiku-4-5-20251001", max_tokens=600, messages=[{"role":"user","content":prompt}])
    return r.content[0].text

def make_opensolar(name, phone, email, address, city, state, postcode):
    try:
        conn = opensolar()
        if not conn.token: return None, None
        parts = name.strip().split()
        first = parts[0] if parts else "Unknown"
        last = " ".join(parts[1:]) if len(parts) > 1 else ""

        # Build full address string — OpenSolar geocodes from this
        full_addr = ", ".join(filter(None, [address, city, state, postcode, "Australia"]))

        print(f"[OpenSolar] Address components: street='{address}' city='{city}' state='{state}' postcode='{postcode}'")
        print(f"[OpenSolar] Full address string: '{full_addr}'")
        print(f"[OpenSolar] Contact: first='{first}' last='{last}' phone='{phone}' email='{email}'")

        payload = {
            "address": full_addr,
            "is_residential": True,
            # contacts_new is the correct OpenSolar API field (not contacts_data)
            "contacts_new": [
                {
                    "first_name": first,
                    "last_name":  last,
                    "phone":      phone or "",
                    "email":      email or "",
                }
            ],
        }
        print(f"[OpenSolar] Payload: {payload}")

        th = {"Authorization": f"Bearer {conn.token}", "Content-Type": "application/json"}
        r = req.post(f"https://api.opensolar.com/api/orgs/{conn.org_id}/projects/",
                     headers=th, json=payload, timeout=20)
        if r.ok:
            pid = r.json().get("id", "")
            url = f"https://app.opensolar.com/#/projects/{pid}/info"
            print("OpenSolar:", url)
            return pid, url
        print(f"OpenSolar error: {r.status_code} {r.text[:200]}")
        return None, None
    except Exception as e:
        print("OpenSolar exc:", e)
        return None, None

def get_os_url_from_crm(cid):
    """Parse the OpenSolar URL from the contact's CRM note (line starting with 'OpenSolar: ')."""
    try:
        r = req.get(f"{BASE}/contacts/{cid}/notes", headers=H, timeout=10)
        if not r.ok:
            return None
        for note in (r.json().get("notes") or []):
            for line in (note.get("body") or "").splitlines():
                if line.startswith("OpenSolar: "):
                    url = line[len("OpenSolar: "):].strip()
                    if url.startswith("http"):
                        return url
    except Exception as e:
        print(f"get_os_url_from_crm error: {e}")
    return None

def save_to_crm(cid, os_url, summary):
    note_body = "OpenSolar: " + os_url + chr(10) + chr(10) + summary
    r_notes = req.get(f"{BASE}/contacts/{cid}/notes", headers=H)
    existing_id = None
    if r_notes.ok:
        for note in (r_notes.json().get("notes") or []):
            if "OpenSolar" in (note.get("body") or ""):
                existing_id = note.get("id")
                break
    if existing_id:
        r2 = req.put(f"{BASE}/contacts/{cid}/notes/{existing_id}", headers=H, json={"body": note_body})
        print("CRM note updated:", r2.status_code)
    else:
        r2 = req.post(f"{BASE}/contacts/{cid}/notes", headers=H, json={"body": note_body})
        print("CRM note created:", r2.status_code)


def icloud_contact(name, phone, email='', address='', city='', state='', postcode='', src=''):
    """Create a contact in iCloud via CardDAV. Returns True on success, False on failure.

    Discovers partition + DSID dynamically so it works even if Apple moves the account.
    """
    import uuid, re as _re, requests as _req, xml.etree.ElementTree as ET
    from requests.auth import HTTPBasicAuth

    icloud_email    = os.getenv('ICLOUD_EMAIL', '')
    icloud_password = os.getenv('ICLOUD_APP_PASSWORD', '')
    if not icloud_password:
        print('[iCloud] No ICLOUD_APP_PASSWORD set')
        return False

    parts = name.strip().split()
    first = parts[0] if parts else 'Unknown'
    last  = (' '.join(parts[1:]) + ' DSW').strip() if len(parts) > 1 else 'DSW'
    note  = f"DSW Lead | {src} | {datetime.now().strftime('%d %b %Y')}"
    date_str = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    phone_clean = _re.sub(r'[^\d+]', '', phone or '')

    auth = HTTPBasicAuth(icloud_email, icloud_password)
    propfind_body = (
        '<?xml version="1.0"?>'
        '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
    )

    try:
        # Step 1: get user partition from well-known endpoint
        r0 = _req.request('PROPFIND', 'https://contacts.icloud.com/.well-known/carddav',
                          auth=auth, headers={'Depth': '0', 'Content-Type': 'application/xml'},
                          data=propfind_body, allow_redirects=True, timeout=15)
        partition = r0.headers.get('x-apple-user-partition', '')
        if not partition:
            print(f'[iCloud] No x-apple-user-partition header (status {r0.status_code})')
            return False
        print(f'[iCloud] Partition: {partition}')

        # Step 2: get DSID from partition server
        r1 = _req.request('PROPFIND', f'https://p{partition}-contacts.icloud.com/',
                          auth=auth, headers={'Depth': '0', 'Content-Type': 'application/xml'},
                          data=propfind_body, timeout=15)
        root = ET.fromstring(r1.text)
        principal_el = root.find('.//{DAV:}current-user-principal/{DAV:}href')
        if principal_el is None:
            print(f'[iCloud] Could not parse current-user-principal from response')
            return False
        dsid = principal_el.text.strip('/').split('/')[0]
        if not dsid.isdigit():
            print(f'[iCloud] Unexpected principal href: {principal_el.text}')
            return False
        print(f'[iCloud] DSID: {dsid}')

        # Step 3: build vCard 3.0
        uid = str(uuid.uuid4()).upper()
        vcard_lines = [
            'BEGIN:VCARD',
            'VERSION:3.0',
            f'UID:{uid}',
            f'N:{last};{first};;;',
            f'FN:{first} {last}',
            f'REV:{date_str}',
            f'NOTE:{note}',
        ]
        if phone_clean:
            vcard_lines.append(f'TEL;TYPE=CELL:{phone_clean}')
        if email:
            vcard_lines.append(f'EMAIL;TYPE=INTERNET:{email}')
        if any([address, city, state, postcode]):
            vcard_lines.append(f'ADR;TYPE=HOME:;;{address};{city};{state};{postcode};Australia')
        vcard_lines.append('END:VCARD')
        vcard = '\r\n'.join(vcard_lines) + '\r\n'

        # Step 4: PUT vCard to addressbook
        ab_url   = f'https://p{partition}-contacts.icloud.com/{dsid}/carddavhome/card/'
        card_url = ab_url + uid + '.vcf'
        r2 = _req.put(card_url, auth=auth,
                      headers={'Content-Type': 'text/vcard; charset=utf-8'},
                      data=vcard.encode('utf-8'), timeout=15)
        print(f'[iCloud] PUT {r2.status_code} → {first} {last}')
        if r2.status_code in (200, 201, 204):
            return True
        print(f'[iCloud] PUT failed: {r2.status_code} {r2.text[:200]}')
        return False

    except Exception as e:
        print(f'[iCloud] Error: {e}')
        return False


def mac_contact(name, phone, src=''):
    """Fallback: create a contact in the local Mac Contacts app via osascript."""
    parts = name.strip().split()
    first = parts[0] if parts else 'Unknown'
    last  = (' '.join(parts[1:]) + ' DSW').strip() if len(parts) > 1 else 'DSW'
    note  = f"DSW Lead | {src} | {datetime.now().strftime('%d %b %Y')}"

    try:
        first_s = first.replace('"', '').replace("'", "")
        last_s  = last.replace('"', '').replace("'", "")
        note_s  = note.replace('"', '').replace("'", "")
        phone_s = (phone or '').replace('"', '')
        script = (
            'tell application "Contacts"\n'
            f'    set p to make new person with properties {{first name:"{first_s}", last name:"{last_s}", note:"{note_s}"}}\n'
            '    tell p\n'
            f'        make new phone at end of phones with properties {{label:"mobile", value:"{phone_s}"}}\n'
            '    end tell\n'
            '    save\n'
            'end tell'
        )
        r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        print(f'[osascript] Contact: {first_s} {last_s} rc={r.returncode}')
    except Exception as e:
        print(f'[osascript] Error: {e}')

def make_task(name, phone, summary, crm_url, os_url, email=''):
    try:
        from task_manager import TaskManager
        tm = TaskManager()
        users = tm.supabase.table("users").select("id").eq("email","rob@cloudcleanenergy.com.au").execute()
        if not users.data: return
        due = (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")
        email_line = ("Email: "+email+"\n") if email else ""
        desc = "Phone: "+phone+"\n"+email_line+"CRM: "+crm_url+"\nOpenSolar: "+(os_url or "pending")+"\n\n"+summary
        result = tm.supabase.table("tasks").insert({"user_id":users.data[0]["id"],"title":"Call "+name+" - New DSW Lead","description":desc,"due_date":due,"due_time":"09:00","priority":"high","status":"pending","category":"DSW Solar","client_name":name}).execute()
        tid = result.data[0]["id"] if result.data else None
        print("Task created:", name, "id:", tid)
        return tid
    except Exception as e: print("Task error:", e); return None

def send_email(name, phone, addr, src, summary, crm_url, os_url, task_id=None, lead_status=None, subject=None, email=''):
    now = datetime.now().strftime("%d %b %Y %I:%M %p")
    import urllib.parse
    maps_url = "https://maps.google.com/?q=" + urllib.parse.quote(addr)
    AU = "https://www.jottask.app/action"
    abtns = ""
    if task_id:
        bl = [("Complete",f"{AU}?action=complete&task_id={task_id}","#10B981"),("+1 Hour",f"{AU}?action=delay_1hour&task_id={task_id}","#6B7280"),("+1 Day",f"{AU}?action=delay_1day&task_id={task_id}","#6B7280"),("Tmrw 8am",f"{AU}?action=delay_next_day_8am&task_id={task_id}","#0EA5E9"),("Tmrw 9am",f"{AU}?action=delay_next_day_9am&task_id={task_id}","#0EA5E9"),("Mon 9am",f"{AU}?action=delay_next_monday_9am&task_id={task_id}","#F59E0B")]
        bh = "".join(f'<a href="{u}" style="display:inline-block;padding:10px 15px;background:{col};color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:13px">{l}</a>' for l,u,col in bl)
        abtns = f'<div style="margin:16px 0;display:flex;flex-wrap:wrap;gap:8px">{bh}</div>'
        # Status buttons
        statuses = [
            ("Intro Call","intro_call","#1e40af"),
            ("Site Visit Booked","site_visit_booked","#7c3aed"),
            ("Awaiting Docs","awaiting_docs","#b45309"),
            ("Build Quote","build_quote","#0369a1"),
            ("Quote Sent","quote_submitted","#0891b2"),
            ("Quote Follow Up","quote_followup","#0e7490"),
            ("Revise Quote","revise_quote","#7c3aed"),
            ("Customer Deciding","customer_deciding","#b45309"),
            ("Nurture","nurture","#6b7280"),
            ("WON","won","#10B981"),
            ("LOST","lost","#ef4444"),
        ]
        sh = "".join(f'<a href="{AU}?action=set_status&status={s}&task_id={task_id}" style="display:inline-block;padding:8px 12px;background:{col};color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:12px">{l}</a>' for l,s,col in statuses)
        sbtns = f'<div style="margin:8px 0;display:flex;flex-wrap:wrap;gap:6px">{sh}</div>'
    os_btn = '<a href="'+os_url+'" style="display:inline-block;background:#f59e0b;color:white;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600;font-size:13px">&#9728;&#65039; OpenSolar</a>' if os_url else ""
    badge_text = STATUS_LABELS.get(lead_status, '🔵 NEW LEAD') if lead_status else '🔵 NEW LEAD'
    html = (
        '<div style="font-family:sans-serif;max-width:620px;margin:0 auto">'
        '<div style="background:#1e40af;color:white;padding:20px;border-radius:8px 8px 0 0">'
        '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap">'
        '<h2 style="margin:0">New DSW Lead</h2>'
        '<span style="background:rgba(255,255,255,0.25);padding:4px 14px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:0.5px">'+badge_text+'</span>'
        '</div>'
        '<p style="opacity:.8;margin:4px 0 0">'+now+' &middot; '+src+'</p></div>'
        '<div style="padding:20px;border:1px solid #e2e8f0">'
        '<h3 style="color:#1e40af;margin-top:0">'+name+'</h3>'
        +('<p><a href="mailto:'+email+'" style="color:#1e40af;text-decoration:none">✉️ '+email+'</a></p>' if email else '')
        +'<p><a href="'+maps_url+'" style="color:#1e40af;text-decoration:none">'+addr+'</a></p>'
        '<div style="margin:12px 0;display:flex;flex-direction:row;gap:8px;flex-wrap:nowrap">'
        '<a href="tel:'+phone+'" style="display:inline-block;background:#10B981;color:white;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600;font-size:13px">Call '+phone+'</a>'
        '<a href="'+crm_url+'" style="display:inline-block;background:#1e40af;color:white;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600;font-size:13px">Pipereply</a>'
        +os_btn+'</div>'
        '<hr style="border:1px solid #e2e8f0"><h4>Lead Summary</h4>'
        '<div style="background:#f8fafc;padding:15px;border-radius:6px;white-space:pre-line;font-size:14px;line-height:1.6">'+summary+'</div>'
        '<hr style="border:1px solid #e2e8f0">'
        '<p style="font-weight:600;color:#6B7280;font-size:13px">Task Delay</p>'
        f'{abtns}'
        '<p style="font-weight:600;color:#6B7280;font-size:13px;margin-top:12px">Lead Status</p>'
        f'{sbtns}'
        '</div>'
        '<div style="background:#1e40af;padding:12px;border-radius:0 0 8px 8px;text-align:center">'
        f'<a href="https://www.jottask.app/task/{task_id}" style="color:white;font-weight:bold;text-decoration:none">Open Jottask</a>'
        '</div></div>'
    )
    try:
        if subject:
            email_subject = subject
        else:
            brief_note = ''
            for _l in (summary or '').splitlines():
                _l = _l.strip()
                if _l.startswith('*'):
                    brief_note = _l.lstrip('*').strip()[:40]
                    break
            email_subject = (f"New Lead: {name} - {badge_text} | {brief_note}"
                             if brief_note else f"New Lead: {name} - {badge_text}")
        resend.Emails.send({"from":"Jottask <"+FROM_EMAIL+">","to":[NOTIFY],"subject":email_subject,"html":html})
        print("Email sent:", name)
    except Exception as e: print("Email error:", e)

def process(contact, task_id=None, lead_status=None):
    t0 = time.time()
    cid = contact.get("id")
    name = " ".join(w.capitalize() for w in (contact.get("contactName") or "Unknown").split())
    full = get_full(cid)
    phone = full.get("phone") or contact.get("phone","N/A")
    email = full.get("email") or contact.get("email","")
    address = full.get("address1") or contact.get("address1","")
    city = full.get("city") or contact.get("city","")
    state = full.get("state") or contact.get("state","")
    postcode = full.get("postalCode") or contact.get("postalCode","")
    notes = full.get("notes","") or ""
    custom = full.get("customFields",[]) or []
    src = source(full)
    addr = ", ".join(filter(None,[address,city,state,postcode]))
    crm_url = CRM_BASE+"/detail/"+cid
    print("Processing:", name, "|", phone, "|", src)
    summary = summarise(name, phone, addr, src, notes, custom)
    is_reminder = task_id is not None
    if not is_reminder:
        # New lead: create OpenSolar project, Mac contact, and Jottask task
        _, os_url = make_opensolar(name, phone, email, address, city, state, postcode)
        if os_url: save_to_crm(cid, os_url, summary)
        if not icloud_contact(name, phone, email=email, address=address, city=city, state=state, postcode=postcode, src=src):
            mac_contact(name, phone, src=src)
        task_id = make_task(name, phone, summary, crm_url, os_url, email=email)
    else:
        # Reminder resend: look up OpenSolar URL from existing CRM note
        os_url = get_os_url_from_crm(cid)
    send_email(name, phone, addr, src, summary, crm_url, os_url, task_id, lead_status, email=email)
    print("Done in", round(time.time()-t0,1), "s:", name)

def resend_email_only(contact_name):
    """Resend the lead email for an existing DSW Solar task by client_name.

    Looks up the pending task, finds the Pipereply contact, and calls
    process() with task_id + lead_status so no new task or OpenSolar
    project is created.
    """
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    # 1. Find existing pending DSW Solar task for this contact
    result = sb.table('tasks')\
        .select('id, lead_status')\
        .eq('status', 'pending')\
        .eq('category', 'DSW Solar')\
        .ilike('client_name', contact_name)\
        .order('created_at', desc=True)\
        .limit(1)\
        .execute()

    if not result.data:
        print(f"resend_email_only: no pending DSW Solar task found for '{contact_name}'")
        return

    task = result.data[0]
    task_id = task['id']
    lead_status = task.get('lead_status') or 'new_lead'
    print(f"Found task {task_id[:8]} lead_status={lead_status} for '{contact_name}'")

    # 2. Find Pipereply contact by name
    r = req.get(f"{BASE}/contacts/", headers=H,
                params={'locationId': LOCATION_ID, 'query': contact_name, 'limit': 1},
                timeout=10)
    if not r.ok:
        print(f"resend_email_only: Pipereply lookup failed: HTTP {r.status_code}")
        return

    contacts = r.json().get('contacts', [])
    if not contacts:
        print(f"resend_email_only: no Pipereply contact found for '{contact_name}'")
        return

    # 3. Resend email only
    process(contacts[0], task_id=task_id, lead_status=lead_status)


def main():
    print("DSW Lead Poller v2 -", datetime.now().strftime("%H:%M:%S"))
    if not TOKEN: print("PIPEREPLY_TOKEN missing"); return
    done = load_done()
    new = 0
    for c in get_contacts():
        cid = c.get("id")
        if not cid or cid in done: continue
        try:
            process(c); done.add(cid); new += 1
        except Exception as e: print("Error:", e)
    save_done(done)
    print("Complete -", new, "leads")

if __name__ == "__main__":
    main()
