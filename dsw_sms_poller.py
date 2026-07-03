#!/usr/bin/env python3
"""DSW SMS Poller - reads SolarQuotes SMS from Messages app and processes new leads"""
import os, sys, sqlite3, json, time, importlib.util, requests, re
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv('/Users/ductpress/Developer/jottask/.env')

DONE_FILE = os.path.expanduser('~/.dsw_sms_done.json')
# DSW's SMS trigger senders. Both numbers deliver lead assignments,
# appointments, and staff-assigned task notifications. The new number
# rolled out ~2026-07-02 and was silently dropped until this fix —
# Karina Martinez (Email Quote Requested / Hot lead alert), Tim Salvestro,
# and the Blackmilk Coffee / ARIA review notification were all missed.
# Keep both trusted so a future rollout of another number doesn't repeat
# the same silent drop.
SMS_SOURCES = (
    '+61468001558',   # original SolarQuotes number
    '+61483988945',   # DSW trigger number (added 2026-07-03)
)
CHAT_DB = os.path.expanduser('~/Library/Messages/chat.db')

def load_done():
    try:
        return set(json.load(open(DONE_FILE)))
    except:
        return set()

def save_done(ids):
    json.dump(list(ids), open(DONE_FILE, 'w'))

def _extract_text_from_attributed_body(blob):
    """Pull the plain-text SMS body out of the NSKeyedArchiver blob that
    macOS Messages stores in message.attributedBody when message.text is
    NULL (the common case for modern Messages-sync'd SMS).

    Anchors on the known DSW prefix "Hi Rob" — the SolarQuotes SMS we care
    about always starts that way. Falls back to the longest printable-ASCII
    run >= 40 chars so non-DSW texts at least decode to something.
    """
    if not blob:
        return ''
    try:
        raw = bytes(blob)
    except Exception:
        return ''
    idx = raw.find(b'Hi Rob')
    if idx == -1:
        runs = re.findall(rb'[\x20-\x7E\r\n]{40,}', raw)
        return max(runs, key=len).decode('utf-8', 'replace') if runs else ''
    end = idx
    while end < len(raw) and end - idx < 500 and (raw[end] in b'\r\n' or 0x20 <= raw[end] <= 0x7E):
        end += 1
    return raw[idx:end].decode('utf-8', 'replace').strip()


def get_new_sms():
    try:
        con = sqlite3.connect(CHAT_DB)
        cur = con.cursor()
        # Trust every number in SMS_SOURCES. Don't filter on text IS NOT
        # NULL — modern Messages leaves text NULL and stores the body in
        # attributedBody. Decode that fallback below.
        placeholders = ','.join('?' * len(SMS_SOURCES))
        cur.execute(
            "SELECT m.ROWID, m.text, m.attributedBody, m.date "
            "FROM message m "
            "JOIN handle h ON m.handle_id = h.ROWID "
            f"WHERE h.id IN ({placeholders}) AND m.is_from_me = 0 "
            "ORDER BY m.date DESC LIMIT 20",
            SMS_SOURCES,
        )
        rows = []
        for rowid, text, attr_body, date in cur.fetchall():
            body = text if text else _extract_text_from_attributed_body(attr_body)
            if body:
                rows.append((rowid, body, date))
        con.close()
        return rows
    except Exception as e:
        print(f"[SMS] DB error: {e}")
        return []

def find_pipereply_contact(name):
    TOKEN = os.getenv('PIPEREPLY_TOKEN')
    LOC = os.getenv('PIPEREPLY_LOCATION_ID')
    H = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json', 'Version': '2021-07-28'}
    r = requests.get('https://services.leadconnectorhq.com/contacts/', headers=H,
        params={'locationId': LOC, 'query': name, 'limit': 3})
    contacts = r.json().get('contacts', [])
    if not contacts:
        return None
    # Match by name
    for c in contacts:
        if name.lower() in (c.get('contactName') or '').lower():
            return c
    return contacts[0]

def extract_name(sms_text):
    """Extract lead name from SolarQuotes SMS"""
    import re
    # Name part: capital letter followed by letters/apostrophe, with optional
    # hyphenated suffix ("Canoa-Rojas") or mixed-case suffix ("McDonald").
    # Accepts mixed case AND ALL-CAPS ("JOHNSON") — DSW Energy sometimes
    # sends names shouting.
    NAME_PART = r"[A-Z][A-Za-z']+(?:-[A-Z][A-Za-z']+)?(?:[A-Z][A-Za-z']+)?"
    NAME_FULL = rf'{NAME_PART}(?:\s+{NAME_PART})+'
    # DSW Energy format: "Hi Rob, Peter Smith has just been assigned to you."
    # Optional job reference (e.g. "Q2021980") may appear between name and "has just been".
    m = re.search(rf'Hi Rob,\s+({NAME_FULL})(?:\s+[A-Z]\d{{5,}})?\s+has just been assigned', sms_text)
    if m:
        return m.group(1)
    # DSW appointment-reminder format:
    #   "Reminder: Upcoming Appointment with Peter Smith in 5 minutes."
    #   "Upcoming Appointment with Peter Smith"
    # The "with ... in" / "with ... <end>" anchor is reliable; one pattern
    # covers both because NAME_FULL stops at lowercase "in".
    m = re.search(rf'Appointment with\s+({NAME_FULL})', sms_text)
    if m:
        return m.group(1)
    # NEW (2026-07 rollout on +61483988945): "🔥 Hot lead alert! Karina
    # Martinez on Jul 4th 2026 at 8:00 am for Karina Martinez". Anchor on
    # "Hot lead alert!" and stop at " on " so the trailing "for <name>"
    # (usually a duplicate of the customer) doesn't extend the boundary.
    m = re.search(rf'Hot lead alert!\s+({NAME_FULL})\s+on\s+', sms_text)
    if m:
        return m.group(1)
    # "Email Quote Requested for Karina Martinez on Jul 4th 2026 at
    # 12:00 am for Karina Martinez". Same anchor shape.
    m = re.search(rf'Email Quote Requested for\s+({NAME_FULL})\s+on\s+', sms_text)
    if m:
        return m.group(1)
    # Explicitly no reliable name: "Customer confidence issue, existing X
    # system needs review … for <owner>". The trailing "for <owner>" is
    # the account owner (e.g. "Aimee ARIA Property Group"), not the
    # customer, and the fallback loop would grab the SYSTEM name
    # ("Blackmilk Coffee"). Return None so main() drops through to the
    # raw-text task path instead of mis-scraping.
    if re.search(r'Customer confidence issue', sms_text, re.IGNORECASE):
        return None
    # Fallback: any two+ capitalised words (with optional apostrophe),
    # skipping matches that start with "Hi" (e.g. "Hi Rob").
    for m in re.finditer(rf'({NAME_FULL})', sms_text):
        candidate = m.group(1)
        if not candidate.startswith('Hi '):
            return candidate
    return None

def _raw_fallback_task(raw_text, extracted_name=None):
    """Escape hatch for SMS the parser can't reliably structure.

    Creates a Jottask task + emails Rob so the SMS is never silently
    dropped. NO PipeReply contact, NO OpenSolar project — those need a
    real customer identifier that only Rob can extract from the raw text.

    One explicit skip: pure DSW admin-link messages ("Here's the link to
    update client meeting status: <url>") are not actionable leads; we
    drop them rather than clutter the dashboard with review tasks.

    Returns the new task_id on success, or None if dropped or on error.
    """
    from datetime import datetime, timedelta, timezone

    clean = (raw_text or '').strip()
    # Silent-drop filter: DSW admin-link messages. Preserves the pre-fix
    # behaviour for this one benign class while every other unstructured
    # message still surfaces to Rob.
    if not clean:
        return None
    if ('link.dswenergy.com.au' in clean and len(clean) < 250) \
            or clean.lower().startswith("here's the link"):
        print(f"[SMS raw-fallback] system-link message — skipped: {clean[:80]!r}")
        return None

    try:
        from supabase import create_client
        from email_utils import send_email
    except Exception as e:
        print(f'[SMS raw-fallback] import failed: {e}')
        return None

    try:
        sb = create_client(os.getenv('SUPABASE_URL'),
                           os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_KEY'))
        u = sb.table('users').select('id').eq('email', 'rob@cloudcleanenergy.com.au').execute()
        if not u.data:
            print('[SMS raw-fallback] no user_id resolved — abort')
            return None

        aest = timezone(timedelta(hours=10))
        due = datetime.now(aest) + timedelta(hours=4)
        summary = (clean.replace('\n', ' ')[:60] + '…') if len(clean) > 60 else clean
        display_name = extracted_name or 'Unstructured DSW SMS'
        task = {
            'user_id':     u.data[0]['id'],
            'title':       f'Review DSW SMS — {summary}',
            'description': (f"Received via DSW SMS trigger ({', '.join(SMS_SOURCES)}).\n\n"
                            f"RAW MESSAGE:\n{clean}\n\n"
                            f"Parser couldn't reliably extract a customer contact. "
                            f"Review and create PipeReply / OpenSolar / task manually "
                            f"if actionable."),
            'due_date':    due.strftime('%Y-%m-%d'),
            'due_time':    due.strftime('%H:%M:00'),
            'priority':    'high',
            'status':      'pending',
            'category':    'DSW Solar',
            'lead_status': 'new_lead',
            'client_name': display_name,
        }
        ins = sb.table('tasks').insert(task).execute()
        tid = ins.data[0]['id'] if ins.data else None
        if not tid:
            print('[SMS raw-fallback] task insert returned no id')
            return None
        print(f'[SMS raw-fallback] task created: {tid}')

        # Fire the amber "review this" email so the SMS is surfaced.
        html = (
            '<div style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:620px">'
            '<div style="background:#b45309;color:white;padding:20px;border-radius:8px 8px 0 0">'
            '<div style="font-size:11px;font-weight:700;letter-spacing:1.2px;opacity:0.85">UNSTRUCTURED DSW SMS</div>'
            '<h2 style="margin:4px 0 0;font-size:20px">Review this — parser couldn\'t extract a customer</h2>'
            '</div>'
            '<div style="padding:18px;border:1px solid #e5e7eb;border-top:none">'
            '<p style="color:#374151;font-size:14px;margin:0 0 12px">'
            'This SMS came in from one of DSW\'s trigger numbers but didn\'t match any known '
            'parse format. Decide whether to create a PipeReply contact / OpenSolar project / '
            'proper task, or ignore.</p>'
            '<div style="font-size:12px;font-weight:700;color:#6b7280;letter-spacing:.3px;margin:16px 0 6px">RAW SMS</div>'
            f'<pre style="background:#f8fafc;padding:12px;border-radius:6px;'
            f'white-space:pre-wrap;font-size:14px;color:#111;margin:0">{clean}</pre>'
            f'<div style="margin-top:16px"><a href="https://www.jottask.app/task/{tid}" '
            'style="background:#1e40af;color:white;padding:10px 18px;border-radius:6px;'
            'text-decoration:none;font-weight:600;font-size:14px;display:inline-block">Open task in Jottask</a></div>'
            '</div></div>'
        )
        ok, err = send_email('rob.l@directsolarwholesaler.com.au',
                             f'⚠ Unstructured DSW SMS — {summary[:40]}',
                             html, category='reminder', task_id=tid)
        if not ok:
            print(f'[SMS raw-fallback] email failed: {err}')
        return tid
    except Exception as e:
        print(f'[SMS raw-fallback] error: {e}')
        return None


def main():
    done = load_done()
    rows = get_new_sms()
    
    if not rows:
        print("[SMS] No messages found from SolarQuotes")
        return
    
    print(f"[SMS] Found {len(rows)} messages, {len(done)} already processed")
    
    # Load dsw_lead_poller
    spec = importlib.util.spec_from_file_location('dsw_lead_poller', 
        '/Users/ductpress/Developer/jottask/dsw_lead_poller.py')
    poller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(poller)
    
    new_count = 0
    for rowid, text, date in rows:
        key = str(rowid)
        if key in done:
            continue
        
        print(f"[SMS] New message: {text[:80]}")
        name = extract_name(text or '')
        
        if not name:
            print(f"[SMS] Could not extract name — raw-text fallback")
            _raw_fallback_task(text or '')
            new_count += 1
            done.add(key)
            continue

        contact = find_pipereply_contact(name)
        if contact:
            print(f"[SMS] Processing: {name}")
            poller.process(contact)
            new_count += 1
        else:
            print(f"[SMS] No Pipereply contact for {name!r} — raw-text fallback")
            _raw_fallback_task(text or '', extracted_name=name)
            new_count += 1

        done.add(key)
        time.sleep(2)
    
    save_done(done)
    print(f"[SMS] Done. Processed {new_count} new leads.")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        import requests, importlib.util
        from dotenv import load_dotenv
        load_dotenv()
        name = ' '.join(sys.argv[1:])
        TOKEN = os.getenv('PIPEREPLY_TOKEN')
        LOC = os.getenv('PIPEREPLY_LOCATION_ID')
        H = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json', 'Version': '2021-07-28'}
        r = requests.get('https://services.leadconnectorhq.com/contacts/', headers=H, params={'locationId': LOC, 'query': name, 'limit': 1})
        contacts = r.json().get('contacts', [])
        spec = importlib.util.spec_from_file_location('poller', 'dsw_lead_poller.py')
        poller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(poller)
        if contacts:
            poller.process(contacts[0])
        else:
            print(f'NOT FOUND: {name}')
    else:
        main()
