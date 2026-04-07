#!/usr/bin/env python3
"""DSW SMS Poller - reads SolarQuotes SMS from Messages app and processes new leads"""
import os, sys, sqlite3, json, time, importlib.util, requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv('/Users/ductpress/Developer/jottask/.env')

DONE_FILE = os.path.expanduser('~/.dsw_sms_done.json')
SMS_SOURCE = '+61468001558'  # SolarQuotes SMS number
CHAT_DB = os.path.expanduser('~/Library/Messages/chat.db')

def load_done():
    try:
        return set(json.load(open(DONE_FILE)))
    except:
        return set()

def save_done(ids):
    json.dump(list(ids), open(DONE_FILE, 'w'))

def get_new_sms():
    try:
        con = sqlite3.connect(CHAT_DB)
        cur = con.cursor()
        cur.execute("""
            SELECT m.ROWID, m.text, m.date
            FROM message m
            JOIN handle h ON m.handle_id = h.ROWID
            WHERE h.id = ? AND m.is_from_me = 0 AND m.text IS NOT NULL
            ORDER BY m.date DESC LIMIT 20
        """, (SMS_SOURCE,))
        rows = cur.fetchall()
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
    # Name part: capitalised word, optionally with apostrophe (O'Neill, O'Brien)
    NAME_PART = r"[A-Z][a-z']+(?:[A-Z][a-z']+)?"
    NAME_FULL = rf'{NAME_PART}(?:\s+{NAME_PART})+'
    # DSW Energy format: "Hi Rob, Peter Smith has just been assigned to you."
    m = re.search(rf'Hi Rob,\s+({NAME_FULL})\s+has just been assigned', sms_text)
    if m:
        return m.group(1)
    # Fallback: any two+ capitalised words (with optional apostrophe)
    m = re.search(rf'({NAME_FULL})', sms_text)
    if m:
        return m.group(1)
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
            print(f"[SMS] Could not extract name, skipping")
            done.add(key)
            continue
        
        contact = find_pipereply_contact(name)
        if contact:
            print(f"[SMS] Processing: {name}")
            poller.process(contact)
            new_count += 1
        else:
            print(f"[SMS] No Pipereply contact found for: {name}")
        
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
