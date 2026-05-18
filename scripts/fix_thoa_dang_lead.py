#!/usr/bin/env python3
"""One-off fix for Thoa Dang's lead.

The lead arrived as a plain "Remember to Callback" task instead of going
through the DSW Solar pipeline (PipeReply contact + OpenSolar project +
DSW Solar task with action buttons + status badges + tags). This script
rebuilds the lead end-to-end:

  1. Cancel the existing plain task.
  2. Find or create the PipeReply contact (Thoa Dang / 0426285540).
  3. Patch the address (148 Botanical Circuit, Pallara QLD 4110) and add
     a CRM note summarising the appointment + requirements.
  4. Create the OpenSolar project at that address.
  5. Create a DSW Solar task with lead_status=site_visit_booked, due
     today at 17:00 (the reminder time), with title, description, and
     client fields populated.
  6. Tag the task as `battery` and `ev_charger`.
  7. Send the full DSW lead email with Call / PipeReply / OpenSolar
     buttons, status badge (SITE VISIT BOOKED), and the appointment
     banner (Wed 18 May 10am).
"""

import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from supabase import create_client
from db_keys import get_admin_key

import requests as req

from dsw_lead_poller import (
    BASE, H, CRM_BASE,
    find_or_create_pipereply_contact,
    make_opensolar,
    save_to_crm,
    icloud_contact,
    mac_contact,
    send_email,
)

# ── Lead facts ────────────────────────────────────────────────────────────────
NAME       = 'Thoa Dang'
PHONE      = '0426285540'
EMAIL      = ''                # not supplied
CONTACT_PERSON = 'Toan'
ADDRESS1   = '148 Botanical Circuit'
CITY       = 'Pallara'
STATE      = 'QLD'
POSTCODE   = '4110'            # Pallara
LEAD_STATUS = 'site_visit_booked'
SRC         = 'Referral'        # unknown true source; safest default
SOURCE_BADGE = '👤 Referral'

OLD_TASK_ID = 'ea2cd2cc-ff5c-4de4-a4aa-8eca9989c6fa'

APPOINTMENT_WHEN = 'Wed 18 May, 10:00 AM'
APPOINTMENT_TYPE = 'Site Visit'

SUMMARY = (
    'CUSTOMER REQUIREMENTS\n'
    '* 30kW battery system\n'
    '* Provide 2 quotes: 22kW and 30kW options\n'
    '* Has hybrid EV (not plug-in)\n'
    f'* Contact person on site: {CONTACT_PERSON} ({PHONE})\n'
    '* Site visit booked: Wed 18 May, 10:00 AM\n'
    '\n'
    'PROPERTY\n'
    f'* {ADDRESS1}, {CITY} {STATE} {POSTCODE}'
)


def cancel_old_task(sb):
    print(f"[step 1] Cancelling old plain task {OLD_TASK_ID[:8]}…")
    sb.table('tasks').update({
        'status':       'cancelled',
        'completed_at': datetime.now(timezone.utc).isoformat(),
    }).eq('id', OLD_TASK_ID).execute()
    # Best-effort supersede note
    try:
        from task_manager import TaskManager
        TaskManager().add_note(
            task_id=OLD_TASK_ID,
            content='Superseded — rebuilt as proper DSW Solar lead with OpenSolar + PipeReply + buttons.',
            source='system',
        )
    except Exception as e:
        print(f"  (supersede note failed: {e})")
    print("  done.")


def find_or_create_contact():
    print(f"[step 2] Find or create PipeReply contact for {NAME} / {PHONE}…")
    cid, is_new = find_or_create_pipereply_contact(
        NAME, PHONE, email=EMAIL,
        address=ADDRESS1, src=SRC.lower(),
    )
    if not cid:
        raise RuntimeError("PipeReply contact lookup/create failed")

    # Patch the address fields so OpenSolar geocodes at the right place.
    patch = {
        'address1':   ADDRESS1,
        'city':       CITY,
        'state':      STATE,
        'postalCode': POSTCODE,
    }
    r = req.put(f'{BASE}/contacts/{cid}', headers=H, json=patch, timeout=15)
    print(f"  patched address (HTTP {r.status_code})")
    return cid, is_new


def add_initial_crm_note(cid):
    print("[step 2b] Adding CRM note with appointment + requirements…")
    body = (
        f"Site Visit Booked: {APPOINTMENT_WHEN}\n"
        f"Contact on site: {CONTACT_PERSON} ({PHONE})\n\n"
        + SUMMARY
    )
    r = req.post(f"{BASE}/contacts/{cid}/notes", headers=H, json={'body': body}, timeout=15)
    print(f"  CRM note created (HTTP {r.status_code})")


def create_opensolar_project():
    print("[step 3] Creating OpenSolar project…")
    pid, os_url = make_opensolar(
        NAME, PHONE, EMAIL, ADDRESS1, CITY, STATE, POSTCODE,
        first_name='Thoa', last_name='Dang',
    )
    print(f"  pid={pid} url={os_url}")
    return os_url


def save_combined_crm_note(cid, os_url):
    """Mirror what process() does — save the OpenSolar URL + summary into a
    CRM note so future reminder lookups can find it."""
    print("[step 3b] Saving OpenSolar URL + summary to CRM note…")
    save_to_crm(cid, os_url or '', SUMMARY, overwrite=False)


def create_dsw_task(sb, os_url, crm_url):
    print("[step 4] Creating DSW Solar task with site_visit_booked status…")
    users = sb.table('users').select('id').eq('email', 'rob@cloudcleanenergy.com.au').execute()
    if not users.data:
        raise RuntimeError("user lookup failed — RLS or wrong key")
    uid = users.data[0]['id']

    addr_full = f"{ADDRESS1}, {CITY} {STATE} {POSTCODE}"
    desc = (
        f"Phone: {PHONE}\n"
        f"Contact: {CONTACT_PERSON}\n"
        f"Source: {SOURCE_BADGE}\n"
        f"CRM: {crm_url}\n"
        f"OpenSolar: {os_url or 'pending'}\n\n"
        f"Appointment: {APPOINTMENT_WHEN} (Site Visit)\n\n"
        + SUMMARY
    )

    # AEST today
    aest = timezone(timedelta(hours=10))
    today = datetime.now(aest).date().isoformat()

    row = {
        'user_id':      uid,
        'title':        f'Call {NAME} - Site Visit Today 10am (reminder 5pm)',
        'description':  desc,
        'due_date':     today,
        'due_time':     '17:00:00',
        'priority':     'high',
        'status':       'pending',
        'category':     'DSW Solar',
        'lead_status':  LEAD_STATUS,
        'client_name':  NAME,
        'client_phone': PHONE,
        'client_email': EMAIL or None,
    }
    result = sb.table('tasks').insert(row).execute()
    tid = result.data[0]['id']
    print(f"  task created: {tid}")
    return tid


def tag_task(sb, tid):
    print("[step 4b] Tagging battery + ev_charger…")
    for tag in ('battery', 'ev_charger'):
        try:
            sb.table('lead_tags').insert({'task_id': tid, 'tag': tag}).execute()
            print(f"  + {tag}")
        except Exception as e:
            if '23505' in str(e) or 'duplicate' in str(e).lower():
                print(f"  (already tagged: {tag})")
            else:
                raise


def create_local_contact():
    print("[step 4c] Creating local/iCloud contact…")
    if not icloud_contact(NAME, PHONE, email=EMAIL,
                          address=ADDRESS1, city=CITY, state=STATE,
                          postcode=POSTCODE, src=SRC):
        mac_contact(NAME, PHONE, src=SRC)


def send_dsw_email(tid, os_url, crm_url):
    print("[step 5] Sending DSW lead email with appointment banner + status badge + tags…")
    addr_full = f"{ADDRESS1}, {CITY} {STATE} {POSTCODE}"
    appointment = {
        'when':  APPOINTMENT_WHEN,
        'type':  APPOINTMENT_TYPE,
        'phone': PHONE,
    }
    ok, err = send_email(
        NAME, PHONE, addr_full, SRC, SUMMARY, crm_url, os_url,
        task_id=tid,
        lead_status=LEAD_STATUS,
        email=EMAIL,
        source_badge_text=SOURCE_BADGE,
        appointment=appointment,
    )
    print(f"  ok={ok} err={err}")


def main():
    sb = create_client(os.getenv('SUPABASE_URL'), get_admin_key())

    # Already cancelled on the first run; idempotent re-cancel is harmless
    cancel_old_task(sb)

    cid, is_new = find_or_create_contact()
    crm_url = f"{CRM_BASE}/detail/{cid}"
    add_initial_crm_note(cid)

    os_url = create_opensolar_project()
    if os_url:
        save_combined_crm_note(cid, os_url)

    tid = create_dsw_task(sb, os_url, crm_url)
    tag_task(sb, tid)
    create_local_contact()
    send_dsw_email(tid, os_url, crm_url)

    print("\nFixed. New task:", tid)
    print("OpenSolar:", os_url)
    print("PipeReply:", crm_url)


if __name__ == '__main__':
    main()
