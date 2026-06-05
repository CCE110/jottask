#!/usr/bin/env python3
import os, re, json, requests as req, subprocess, time
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
    'no_reply':           '📵 NO REPLY',
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

# ── Lead-text cleanup ─────────────────────────────────────────────────────────
# SolarQuotes' API dumps internal fields into PipeReply notes and DSW Energy
# drops HTML-formatted system emails into them too. Strip HTML + anything
# matching these patterns from lead summaries and raw CRM-notes rendering.
_JUNK_PATTERNS = [
    # SolarQuotes API dump
    r'verified phone',
    r'phone number verified',
    r'phone.+verified',
    r'consent(?:ed)?\b',
    r'lead submitted',
    r'\bsubmission\b',
    r'requested quotes',
    r'quote count',
    r'number of quotes',
    r'roof ownership',
    r'north[\s\-]?facing',
    r'\bsupplier\s*id\b',
    r'\bsupplierid\b',
    r'\bsuppliername\b',
    r'\bidleadsupplier\b',
    r'^\s*claimed\s*:',
    r'^\s*id\s*:\s*\S',
    # DSW Energy system-note noise
    r'link\.dswenergy\.com\.au',
    r'^\s*system added note',
    r'click here',
    r'\bB-\d+-WF-',           # appointment codes like "B-008-WF-..."
    r'sales meeting status',
    r'site inspection form',
    r'appt\s+to\s+quote',
    r'appt\s+confirmed',
]
_JUNK_RE = re.compile('|'.join(_JUNK_PATTERNS), re.IGNORECASE)


# Minimal HTML-to-text. Handles <br>, <p>, <div>, <li>, <h*> as line breaks;
# drops <script>/<style> blocks; strips remaining tags; decodes common
# entities. Good enough for DSW Energy system-email notes and hand-pasted
# "My Notes" — avoids adding BeautifulSoup as a dep.
_HTML_BLOCK_RE  = re.compile(r'<(script|style)[^>]*>.*?</\1>', re.IGNORECASE | re.DOTALL)
_HTML_BR_RE     = re.compile(r'<\s*br\s*/?\s*>', re.IGNORECASE)
_HTML_BLK_END   = re.compile(r'</\s*(p|div|li|h[1-6]|tr)\s*>', re.IGNORECASE)
_HTML_TAG_RE    = re.compile(r'<[^>]+>')
_HTML_ENTITIES = {
    '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>',
    '&quot;': '"', '&#39;': "'", '&#x27;': "'", '&apos;': "'",
}


def _strip_html(text):
    """Convert HTML fragments to plain text. No-op if no '<' present."""
    if not text or '<' not in text:
        return text
    t = _HTML_BLOCK_RE.sub('', text)
    t = _HTML_BR_RE.sub('\n', t)
    t = _HTML_BLK_END.sub('\n', t)
    t = _HTML_TAG_RE.sub('', t)
    for k, v in _HTML_ENTITIES.items():
        t = t.replace(k, v)
    # Collapse runs of whitespace inside each line, preserve newlines.
    lines = [re.sub(r'[ \t]+', ' ', ln).strip() for ln in t.splitlines()]
    return '\n'.join(lines)


def filter_junk_lines(text):
    """Strip HTML, drop junk-pattern lines, collapse blank runs."""
    if not text:
        return text
    text = _strip_html(text)
    kept = []
    prev_blank = False
    for raw in text.splitlines():
        if _JUNK_RE.search(raw):
            continue
        if raw.strip() == '':
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        kept.append(raw)
    return '\n'.join(kept).strip()


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

def _normalize_phone(p):
    """Normalize to 10-digit Australian format (0XXXXXXXXX) for comparison."""
    import re as _re
    if not p:
        return ''
    d = _re.sub(r'\D', '', str(p))
    if d.startswith('61') and len(d) == 11:  # +61XXXXXXXXX → 0XXXXXXXXX
        d = '0' + d[2:]
    return d


def _to_e164_au(p):
    """Coerce an AU mobile to E.164 (+614XXXXXXXX) for outbound queries.

    PipeReply stores phones as '+614XXXXXXXX' and its `query=` parameter does
    prefix-style matching against the stored value — searching with '04xxx...'
    against '+614xxx...' returns zero results. Use this when building search
    queries; use _normalize_phone for in-Python equality checks (it strips +
    and returns '04...').
    """
    import re as _re
    if not p:
        return ''
    d = _re.sub(r'\D', '', str(p))
    if d.startswith('61') and len(d) == 11:
        return '+' + d
    if d.startswith('0') and len(d) == 10:
        return '+61' + d[1:]
    if len(d) == 9 and d[0] == '4':
        return '+61' + d
    return ''


def _fuzzy_name_match(a, b, threshold=0.80):
    """Return True if two name strings match with >= threshold similarity."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio() >= threshold


def find_or_create_pipereply_contact(name, phone, email='', address='', src='referral'):
    """Find an existing Pipereply contact by phone, email, or fuzzy name.

    Search order: phone → email → fuzzy name. Any match reuses the contact
    and patches in missing fields. Only creates a new contact if all three
    searches miss.

    Returns (cid, is_new):
      - cid: Pipereply contact ID (or None on failure)
      - is_new: True if a new contact was created, False if an existing one was reused
    """
    norm_phone = _normalize_phone(phone)
    norm_email = (email or '').strip().lower()

    # ── 1. Phone-first dedup ──────────────────────────────────────────────
    # PipeReply stores phones as +614XXXXXXXX and its `query=` does prefix
    # matching against the stored value. Querying with '04xxx...' against a
    # '+614xxx...' record returns zero hits, which previously meant the dedup
    # short-circuit missed (MYTIEN Do / 'Tian' lead, 2026-06-02) and PipeReply's
    # server-side dedup fired with a 400 instead. Try E.164 first (the form
    # PipeReply actually stores), fall back to the 0... form for any legacy
    # contacts stored that way, and post-filter every result through
    # _normalize_phone so the comparison works regardless of stored format.
    if norm_phone:
        queries = []
        e164 = _to_e164_au(phone)
        if e164:
            queries.append(e164)
        if norm_phone not in queries:
            queries.append(norm_phone)

        seen_cids = set()
        for q in queries:
            r = req.get(f'{BASE}/contacts/', headers=H,
                        params={'locationId': LOCATION_ID, 'query': q, 'limit': 10},
                        timeout=10)
            if not r.ok:
                continue
            for c in r.json().get('contacts', []):
                cid = c.get('id')
                if not cid or cid in seen_cids:
                    continue
                seen_cids.add(cid)
                if _normalize_phone(c.get('phone', '')) != norm_phone:
                    continue
                existing_name = c.get('contactName', name)
                print(f'[Pipereply] Reused existing contact (phone match via {q!r}): '
                      f'{existing_name} ({cid[:8]})')
                # Patch in any missing fields
                patch = {}
                if email and not c.get('email'):
                    patch['email'] = email
                if address and not c.get('address1'):
                    patch['address1'] = address
                if patch:
                    req.put(f'{BASE}/contacts/{cid}', headers=H, json=patch, timeout=10)
                return cid, False

    # ── 2. Email dedup ────────────────────────────────────────────────────
    if norm_email:
        r = req.get(f'{BASE}/contacts/', headers=H,
                    params={'locationId': LOCATION_ID, 'query': norm_email, 'limit': 10},
                    timeout=10)
        if r.ok:
            for c in r.json().get('contacts', []):
                if (c.get('email') or '').strip().lower() == norm_email:
                    cid = c['id']
                    existing_name = c.get('contactName', name)
                    print(f'[Pipereply] Reused existing contact (email match): {existing_name} ({cid[:8]})')
                    patch = {}
                    if phone and not _normalize_phone(c.get('phone', '')):
                        patch['phone'] = phone
                    if address and not c.get('address1'):
                        patch['address1'] = address
                    if patch:
                        req.put(f'{BASE}/contacts/{cid}', headers=H, json=patch, timeout=10)
                    return cid, False

    # ── 3. Fuzzy name dedup ───────────────────────────────────────────────
    if name:
        r = req.get(f'{BASE}/contacts/', headers=H,
                    params={'locationId': LOCATION_ID, 'query': name, 'limit': 5},
                    timeout=10)
        if r.ok:
            for c in r.json().get('contacts', []):
                if _fuzzy_name_match(name, c.get('contactName', '')):
                    cid = c['id']
                    existing_name = c.get('contactName', name)
                    print(f'[Pipereply] Reused existing contact (name match ≥80%%): {existing_name} ({cid[:8]})')
                    patch = {}
                    if phone and not _normalize_phone(c.get('phone', '')):
                        patch['phone'] = phone
                    if email and not c.get('email'):
                        patch['email'] = email
                    if address and not c.get('address1'):
                        patch['address1'] = address
                    if patch:
                        req.put(f'{BASE}/contacts/{cid}', headers=H, json=patch, timeout=10)
                    return cid, False

    # ── 4. Create new contact ─────────────────────────────────────────────
    # PipeReply rejects blank email with a 422 "email must be an email",
    # so omit empty optional fields entirely rather than sending ''.
    parts = (name or '').strip().split()
    first = parts[0] if parts else name or 'Unknown'
    last  = ' '.join(parts[1:]) if len(parts) > 1 else ''
    payload = {
        'locationId': LOCATION_ID,
        'firstName':  first,
        'lastName':   last,
        'tags':       [src if src else 'referral'],
        'source':     src.replace('_', ' ').title() if src else 'Referral',
    }
    if phone:   payload['phone']    = phone
    if email:   payload['email']    = email
    if address: payload['address1'] = address
    r = req.post(f'{BASE}/contacts/', headers=H, json=payload, timeout=10)
    if r.ok:
        data = r.json()
        cid  = (data.get('contact') or data).get('id', '')
        print(f'[Pipereply] Created new contact: {name} ({(cid or "?")[:8]})')
        return cid, True
    print(f'[Pipereply] Contact creation failed ({r.status_code}): {r.text[:120]}')
    return None, False


def _list_mac_contacts():
    """Dump every Mac Contacts entry as a list of dicts: {first, last, phones}.

    Enumerating in AppleScript and matching in Python avoids the 'whose name
    is/contains ...' filter, which substring-matched 'Tian DSW' against
    'Esther Christian DSW'. Phones are normalised through _normalize_phone()
    so '+61413537679' and '0413537679' compare equal.
    """
    script = (
        'set out to ""\n'
        'tell application "Contacts"\n'
        '    repeat with p in every person\n'
        '        set fn to ""\n'
        '        set ln to ""\n'
        '        try\n'
        '            set fn to first name of p as string\n'
        '        end try\n'
        '        try\n'
        '            set ln to last name of p as string\n'
        '        end try\n'
        '        set phs to ""\n'
        '        try\n'
        '            repeat with ph in phones of p\n'
        '                set phs to phs & (value of ph as string) & "|"\n'
        '            end repeat\n'
        '        end try\n'
        '        set out to out & fn & tab & ln & tab & phs & linefeed\n'
        '    end repeat\n'
        'end tell\n'
        'return out\n'
    )
    try:
        r = subprocess.run(['osascript', '-e', script],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:
        print(f'[iCloud] enumerate error: {e}')
        return []
    out = []
    for line in (r.stdout or '').splitlines():
        cols = line.split('\t')
        if len(cols) < 3:
            continue
        phones = [_normalize_phone(p) for p in cols[2].split('|') if p.strip()]
        phones = [p for p in phones if p]
        out.append({'first': cols[0].strip(), 'last': cols[1].strip(), 'phones': phones})
    return out


def _mac_contact_exists(name, phone=''):
    """Return True if a Mac Contacts entry already exists for this lead.

    Lookup order:
      1. Exact normalised phone match — strongest signal.
      2. Exact case-insensitive first + (last + ' DSW') match.

    If the name matches but the existing contact carries a *different* phone,
    log a warning and return False — that's almost certainly a different
    person who happens to share a name (e.g. two 'John Smith DSW' entries),
    and we'd rather create a fresh contact than silently treat them as one
    and risk attaching the new lead's phone to the wrong record.

    Substring/contains matching is never used.
    """
    target_phone = _normalize_phone(phone or '')
    parts = (name or '').strip().split()
    target_first = (parts[0] if parts else '').lower()
    target_last  = ((' '.join(parts[1:]) + ' DSW').strip()
                    if len(parts) > 1 else 'DSW').lower()

    contacts = _list_mac_contacts()
    if not contacts:
        # osascript failure or empty book — be conservative and let the
        # caller try to create (PUT is idempotent on UID).
        return False

    if target_phone:
        for c in contacts:
            if target_phone in c['phones']:
                print(f"[iCloud] Contact exists (phone match {target_phone}): "
                      f"{c['first']} {c['last']}")
                return True

    if target_first:
        for c in contacts:
            if c['first'].lower() != target_first:
                continue
            if c['last'].lower() != target_last:
                continue
            # Exact name match. Phone-safety check:
            if not target_phone or not c['phones'] or target_phone in c['phones']:
                print(f"[iCloud] Contact exists (name match): "
                      f"{c['first']} {c['last']}")
                return True
            print(
                f"[iCloud] WARNING: name match for {name!r} "
                f"({c['first']} {c['last']}) but existing phone(s) {c['phones']} "
                f"don't include lead phone {target_phone}. Treating as a "
                f"different person — proceeding to create a new contact."
            )
            return False

    return False


def source(c):
    tags = " ".join([t.lower() for t in (c.get("tags") or [])])
    for s, k in [("SolarQuotes","solar_quotes"),("Bid My Solar","bid_my_solar"),("Bid My Solar","bidmysolar"),("SEM","sem"),("Oxley FC","oxley_fc"),("Facebook","facebook"),("Website","website"),("Referral","referral")]:
        if k in tags: return s
    return c.get("source", "Unknown")


def source_badge(src_name, referred_by=''):
    """Pretty-printed lead source badge for emails and task descriptions."""
    sn = (src_name or '').strip().lower()
    if 'solarquotes' in sn.replace(' ', '') or sn == 'solar quotes':
        return '📋 SolarQuotes'
    if 'bidmysolar' in sn.replace(' ', ''):
        return '📋 Bid My Solar'
    if 'referral' in sn:
        if referred_by:
            return f'👤 Referral from: {referred_by}'
        return '👤 Referral'
    if 'oxley' in sn or sn == 'oxley fc':
        return '⚽ Oxley United FC'
    if not src_name or sn == 'unknown':
        return '📋 Unknown'
    return f'📋 {src_name}'


def get_referred_by_from_crm(cid):
    """Fetch the PipeReply contact's notes and extract 'Referred by: ...' if present."""
    try:
        r = req.get(f"{BASE}/contacts/{cid}/notes", headers=H, timeout=10)
        if not r.ok:
            return ''
        for n in r.json().get('notes', []) or []:
            body = n.get('body') or ''
            m = re.search(r'^Referred by:\s*(.+)$', body, re.MULTILINE)
            if m:
                return m.group(1).strip()
    except Exception as e:
        print(f'[Referral] CRM notes lookup failed for {cid}: {e}')
    return ''


def get_crm_notes_bodies(cid):
    """Return all CRM note bodies for a PipeReply contact, newest first, joined by blank lines.

    Lines matching SolarQuotes API junk fields (Id:, Supplierid:, Claimed:,
    etc.) are stripped before joining — see filter_junk_lines().
    """
    try:
        r = req.get(f"{BASE}/contacts/{cid}/notes", headers=H, timeout=10)
        if not r.ok:
            return ''
        notes = r.json().get('notes') or []
        notes.sort(key=lambda n: n.get('createdAt') or n.get('dateAdded') or '', reverse=True)
        bodies = [filter_junk_lines((n.get('body') or '').strip()) for n in notes]
        return '\n\n'.join(b for b in bodies if b)
    except Exception as e:
        print(f'[CRM notes] fetch failed for {cid}: {e}')
        return ''


_AU_STATES = r'(?:QLD|NSW|VIC|SA|WA|NT|ACT|TAS)'
_ADDR_RE = re.compile(
    r'(\d+[A-Za-z]?(?:[-/]\d+[A-Za-z]?)?\s+[^,\n]+?)'  # 75 Lisk Street
    r'[,\s]+'
    r"([A-Za-z][A-Za-z \-']+?)"                        # Pullenvale
    rf'[,\s]+({_AU_STATES})'                            # QLD
    r'\s+(\d{4})',                                      # 4069
    re.IGNORECASE,
)


def extract_address_from_notes(text):
    """Find an Australian-format address inside free-form text.

    Matches patterns like '75 Lisk Street, Pullenvale, QLD 4069' (or without
    commas). Returns (street, suburb, state, postcode) or None.
    """
    if not text:
        return None
    m = _ADDR_RE.search(text)
    if not m:
        return None
    return (
        m.group(1).strip().rstrip(','),
        m.group(2).strip().rstrip(','),
        m.group(3).upper(),
        m.group(4).strip(),
    )


def summarise(name, phone, addr, src, notes, custom):
    """Return (summary_text, referred_by). referred_by is scraped from notes/custom
    fields via 'Referred by: ...' pattern before the AI call."""
    extra_parts = [str(f.get("value","")) for f in (custom or []) if f.get("value")]
    extra_parts.sort(key=len, reverse=True)
    extra = chr(10).join(extra_parts)

    # Scan both raw notes and custom field values for an explicit referred_by line
    referred_by = ''
    for blob in (notes or '', extra):
        m = re.search(r'^Referred by:\s*(.+)$', blob, re.MULTILINE)
        if m:
            referred_by = m.group(1).strip()
            break

    prompt = ("Summarise this into actionable customer requirements. Ignore duplicates. "
              "NEVER include any of these — even as bullets: verified phone number, "
              "phone number verified, consented / consent to discuss energy plans, "
              "lead submitted, submission, requested quotes / quote count / number of "
              "quotes, roof ownership, north facing / north-facing, supplier id / "
              "supplierid / suppliername / idleadsupplier, claimed, standalone lead "
              "'Id:' numbers. These are internal SolarQuotes API fields, not sales data.\n\n"
              "Format exactly (plain text, no ## markdown):\n"
              "CUSTOMER REQUIREMENTS\n* [requirement]\n\n"
              "PROPERTY\n* [property detail]\n\n"
              "Keep: system size kW, solar/battery/both, EV charger, bill amount, "
              "payment method, urgency/timeframe, property type/storeys/roof type, "
              "motivation, blackout/backup needs, home visit.\nConcise bullets only.\n\n"
              f"Name: {name}\nSource: {src}\nAddress: {addr}\n"
              f"Notes: {(extra if len(extra) > len(notes) else notes)[:3000]}")
    r = claude.messages.create(model="claude-haiku-4-5-20251001", max_tokens=600, messages=[{"role":"user","content":prompt}])
    return filter_junk_lines(r.content[0].text), referred_by

_STREET_ABBREVS = [
    ('St', 'Street'), ('Pl', 'Place'), ('Ave', 'Avenue'),
    ('Rd', 'Road'), ('Dr', 'Drive'), ('Ct', 'Court'),
    ('Cres', 'Crescent'), ('Crst', 'Crescent'),
    ('Tce', 'Terrace'), ('Cl', 'Close'),
    ('Pde', 'Parade'), ('Blvd', 'Boulevard'), ('Bvd', 'Boulevard'),
    ('Hwy', 'Highway'), ('Ln', 'Lane'), ('Gr', 'Grove'),
    ('Sq', 'Square'), ('Cct', 'Circuit'), ('Way', 'Way'),
    ('Esp', 'Esplanade'), ('Qy', 'Quay'), ('Wk', 'Walk'),
    ('Pk', 'Park'), ('Gdns', 'Gardens'), ('Mws', 'Mews'),
]


def _expand_street_abbrevs(addr):
    """Expand AU street-type abbreviations ("Cct" → "Circuit") with word boundaries.

    Matches whether the abbreviation is followed by a space, a comma, or the end
    of the string, and tolerates an optional trailing dot ("Cct.").
    """
    if not addr:
        return addr
    out = addr
    for abbr, full in _STREET_ABBREVS:
        out = re.sub(rf'\b{re.escape(abbr)}\b\.?', full, out)
    return out


def join_address_parts(*parts):
    """Join address components without duplicate tokens.

    Handles the common PipeReply case where address1 already contains the
    suburb (e.g. "59 Hazelton Street, Riverhills") AND city is also set
    ("Riverhills") — naive ", ".join() produces a doubled "Riverhills,
    Riverhills". Each part is split on commas, then deduped case-insensitively
    while preserving the input order before re-joining.

    Whitespace and empty fragments are stripped.
    """
    seen = set()
    out = []
    for p in parts:
        if not p:
            continue
        for piece in str(p).split(','):
            piece = piece.strip()
            if not piece:
                continue
            key = piece.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(piece)
    return ', '.join(out)


def make_opensolar(name, phone, email, address, city, state, postcode,
                   first_name=None, last_name=None):
    try:
        conn = opensolar()
        if not conn.token: return None, None

        # Sanitise phone: strip non-digit/non-plus chars; drop if <8 digits.
        # Prevents "N/A", dashes, or other junk from triggering OpenSolar 400s.
        _clean_phone = re.sub(r'[^\d+]', '', phone or '')
        if sum(ch.isdigit() for ch in _clean_phone) < 8:
            _clean_phone = ''
        phone = _clean_phone

        # Prefer explicit first/last (from PipeReply firstName/lastName) and fall
        # back to splitting the display name. We always send both first_name and
        # last_name to OpenSolar so the stored contact isn't missing the surname.
        parts = (name or '').strip().split()
        first = (first_name or (parts[0] if parts else 'Unknown')).strip()
        last  = (last_name  or (' '.join(parts[1:]) if len(parts) > 1 else '')).strip()

        # Build full address string — OpenSolar geocodes from this. Use
        # join_address_parts so a suburb baked into address1 doesn't appear
        # twice when city is also set.
        clean_addr = _expand_street_abbrevs(address or '')
        full_addr = join_address_parts(clean_addr, city, state, postcode)

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
        # Handle duplicate email — OpenSolar refuses a second project for an
        # existing contact email. Primary fallback: look up the contact via
        # GET /api/orgs/<org>/contacts/?email=<email> and pull the first
        # project ID off its `projects` field. This works reliably because
        # OpenSolar's contacts endpoint filters by exact email match
        # (unlike the projects ?search= param which ignores the query).
        # Legacy name-search retained as last-ditch fallback.
        if r.status_code == 400 and "already in use" in r.text:
            if email:
                print(f"[OpenSolar] Email in use, looking up existing contact: {email}")
                try:
                    cr = req.get(f"https://api.opensolar.com/api/orgs/{conn.org_id}/contacts/",
                                 headers=th, params={"email": email}, timeout=15)
                    if cr.ok:
                        payload = cr.json()
                        rows = payload if isinstance(payload, list) else (
                            payload.get("data") or payload.get("results") or []
                        )
                        matches = [row for row in rows
                                   if (row.get("email") or "").lower() == email.lower()]
                        for m in matches:
                            projects = m.get("projects") or []
                            if projects:
                                # projects entries look like
                                # https://api.opensolar.com/api/orgs/14523/projects/9872706/
                                pid_str = projects[0].rstrip("/").split("/")[-1]
                                if pid_str.isdigit():
                                    pid = int(pid_str)
                                    url = f"https://app.opensolar.com/#/projects/{pid}/info"
                                    print(f"[OpenSolar] Linked existing project for "
                                          f"{email} → {url}")
                                    return pid, url
                        print(f"[OpenSolar] Contact found for {email} but no projects "
                              f"linked — falling back to name search")
                    else:
                        print(f"[OpenSolar] contacts lookup HTTP {cr.status_code}")
                except Exception as ce:
                    print(f"[OpenSolar] contacts lookup error: {ce}")

            # Last-resort: name search. The projects ?search= param doesn't
            # filter reliably, but on the off chance a result matches we'll
            # take it before giving up.
            print(f"[OpenSolar] Falling back to name search: {name}")
            sr = req.get(f"https://api.opensolar.com/api/orgs/{conn.org_id}/projects/",
                         headers=th, params={"search": name, "limit": 5}, timeout=15)
            if sr.ok:
                results = sr.json() if isinstance(sr.json(), list) else sr.json().get("data", [])
                if results:
                    pid = results[0].get("id", "")
                    url = f"https://app.opensolar.com/#/projects/{pid}/info"
                    print(f"[OpenSolar] Found existing project (name search): {url}")
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

def save_to_crm(cid, os_url, summary, overwrite=True):
    """Save lead summary + OpenSolar URL to Pipereply CRM notes.

    overwrite=True  → update the existing 'OpenSolar' note (default for new contacts)
    overwrite=False → always add a new note (used when reusing an existing contact
                      so historical notes are preserved)
    """
    note_body = "OpenSolar: " + os_url + chr(10) + chr(10) + summary
    if overwrite:
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
            return
    # Add as new note (either overwrite=False, or no existing note found)
    r2 = req.post(f"{BASE}/contacts/{cid}/notes", headers=H, json={"body": note_body})
    print("CRM note created:", r2.status_code)


def icloud_contact(name, phone, email='', address='', city='', state='', postcode='', src=''):
    """Create a contact in iCloud via CardDAV. Returns True on success, False on failure.

    Checks whether a contact named '{name} DSW' already exists (via Mac Contacts) before
    creating. Discovers partition + DSID dynamically so it works even if Apple moves the account.
    """
    if _mac_contact_exists(name, phone=phone):
        return True  # Already exists — treat as success, skip creation
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
    if _mac_contact_exists(name, phone=phone):
        return  # Already exists — skip
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

def make_task(name, phone, summary, crm_url, os_url, email='', prev_notes_block='', supersede_task_id=None, source_badge_text=''):
    """Create a DSW Solar task in Jottask.

    If prev_notes_block is provided, it is appended to the description under
    a "PREVIOUS NOTES" header (used when migrating from an older task).
    If supersede_task_id is provided, that task is cancelled after the new
    task is created and a supersede note is added to it.
    """
    try:
        from task_manager import TaskManager
        tm = TaskManager()
        users = tm.supabase.table("users").select("id").eq("email","rob@cloudcleanenergy.com.au").execute()
        if not users.data:
            # Loud fail — silent None here means send_email later renders
            # /task/None and zero action buttons (anon key + RLS hides users).
            raise RuntimeError(
                "make_task: users.select returned 0 rows for rob@cloudcleanenergy.com.au. "
                "RLS is likely blocking — ensure SUPABASE_SERVICE_KEY is set in env."
            )
        # Default due: now + 4 hours in AEST (Australia/Brisbane is UTC+10, no DST).
        # Ensures the task always lands inside today's daily-summary + reminder window.
        _aest = timezone(timedelta(hours=10))
        _target = datetime.now(_aest) + timedelta(hours=4)
        due = _target.strftime("%Y-%m-%d")
        due_time = _target.strftime("%H:%M:00")
        email_line = ("Email: "+email+"\n") if email else ""
        source_line = ("Source: "+source_badge_text+"\n") if source_badge_text else ""
        desc = "Phone: "+phone+"\n"+email_line+source_line+"CRM: "+crm_url+"\nOpenSolar: "+(os_url or "pending")+"\n\n"+summary
        if prev_notes_block:
            desc += "\n\n--- PREVIOUS NOTES ---\n" + prev_notes_block
        result = tm.supabase.table("tasks").insert({
            "user_id":      users.data[0]["id"],
            "title":        "Call "+name+" - New DSW Lead",
            "description":  desc,
            "due_date":     due,
            "due_time":     due_time,
            "priority":     "high",
            "status":       "pending",
            "category":     "DSW Solar",
            "client_name":  name,
            "client_phone": phone or None,
            "client_email": email or None,
        }).execute()
        tid = result.data[0]["id"] if result.data else None
        print("Task created:", name, "id:", tid)

        # Cancel the superseded task and drop a supersede note on it
        if tid and supersede_task_id:
            try:
                tm.supabase.table("tasks").update({
                    "status": "cancelled",
                    "completed_at": datetime.utcnow().isoformat() + "Z",
                }).eq("id", supersede_task_id).execute()
                tm.add_note(
                    task_id=supersede_task_id,
                    content=f"Superseded by task {tid}",
                    source="system",
                )
                print(f"[Migrate] Cancelled old task {supersede_task_id[:8]} → superseded by {tid[:8]}")
            except Exception as e:
                print(f"[Migrate] Failed to cancel old task {supersede_task_id}: {e}")

        return tid
    except Exception as e: print("Task error:", e); return None

def send_email(name, phone, addr, src, summary, crm_url, os_url, task_id=None, lead_status=None, subject=None, email='', source_badge_text='', reminder_tag=None, appointment=None):
    now = datetime.now().strftime("%d %b %Y %I:%M %p")
    if appointment:
        header_title = 'DSW Appointment Booked'
        header_bg = '#059669'
    elif reminder_tag:
        header_title = f'DSW Lead REMINDER ({reminder_tag})'
        header_bg = '#b45309'
    else:
        header_title = 'New DSW Lead'
        header_bg = '#1e40af'
    import urllib.parse
    maps_url = "https://maps.google.com/?q=" + urllib.parse.quote(addr)
    AU = "https://www.jottask.app/action"
    # Initialize ALL action-button vars up front. Each is reassigned inside
    # `if task_id:` below, but referenced unconditionally in the f-string
    # template later — leaving them undefined raised UnboundLocalError on
    # any forwarded-lead email that didn't yet have a task_id (the DSW
    # forward handler calls send_email() before make_task() in some paths).
    abtns = ""
    abtns2 = ""
    no_reply_btn = ""
    sbtns = ""
    if task_id:
        bl = [("Complete",f"{AU}?action=complete&task_id={task_id}","#10B981"),("+1 Hour",f"{AU}?action=delay_1hour&task_id={task_id}","#6B7280"),("+1 Day",f"{AU}?action=delay_1day&task_id={task_id}","#6B7280"),("Tmrw 8am",f"{AU}?action=delay_next_day_8am&task_id={task_id}","#0EA5E9"),("Tmrw 9am",f"{AU}?action=delay_next_day_9am&task_id={task_id}","#0EA5E9"),("Mon 9am",f"{AU}?action=delay_next_monday_9am&task_id={task_id}","#F59E0B"),("Close / Not Mine",f"{AU}?action=cancel&task_id={task_id}","#374151")]
        bh = "".join(f'<a href="{u}" style="display:inline-block;padding:10px 15px;background:{col};color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:13px">{l}</a>' for l,u,col in bl)
        abtns = f'<div style="margin:16px 0;display:flex;flex-wrap:wrap;gap:8px">{bh}</div>'
        _now = datetime.now()
        _days_to_fri = (4 - _now.weekday()) % 7 or 7
        _days_to_mon = (7 - _now.weekday()) % 7 or 7
        _days_to_wed = (2 - _now.weekday()) % 7 or 7
        _this_fri  = (_now + timedelta(days=_days_to_fri)).strftime("%Y-%m-%d")
        _next_mon  = (_now + timedelta(days=_days_to_mon)).strftime("%Y-%m-%d")
        _next_wed  = (_now + timedelta(days=_days_to_wed)).strftime("%Y-%m-%d")
        _plus_week = (_now + timedelta(days=7)).strftime("%Y-%m-%d")
        bl2 = [
            ("This Fri 9am", f"{AU}?action=set_custom&date={_this_fri}&time=09:00&task_id={task_id}", "#0EA5E9"),
            ("Next Mon 9am", f"{AU}?action=set_custom&date={_next_mon}&time=09:00&task_id={task_id}", "#F59E0B"),
            ("Next Wed 9am", f"{AU}?action=set_custom&date={_next_wed}&time=09:00&task_id={task_id}", "#F59E0B"),
            ("+1 Week",      f"{AU}?action=set_custom&date={_plus_week}&time=09:00&task_id={task_id}", "#6B7280"),
        ]
        bh2 = "".join(f'<a href="{u}" style="display:inline-block;padding:10px 15px;background:{col};color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:13px">{l}</a>' for l,u,col in bl2)
        abtns2 = f'<div style="margin:4px 0 8px;display:flex;flex-wrap:wrap;gap:8px">{bh2}</div>'
        no_reply_btn = f'<div style="margin:8px 0"><a href="{AU}?action=no_reply&task_id={task_id}" style="display:inline-block;padding:10px 15px;background:#D97706;color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:13px">No Reply ↩ Try Again Tmrw</a></div>'
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

    # ── Lead tag pills ──────────────────────────────────────────────────────
    # Look up any tags set on this task and render coloured pills in the
    # header below the source badge. Best-effort — silent skip on any error
    # so a tagging glitch can't block a reminder going out.
    tag_pills_html = ''
    if task_id:
        try:
            from supabase import create_client
            from db_keys import get_admin_key
            _sb = create_client(os.getenv('SUPABASE_URL'), get_admin_key())
            _tag_rows = _sb.table('lead_tags').select('tag').eq('task_id', task_id).execute().data or []
            _tags = {row['tag'] for row in _tag_rows}
            _TAG_META = {
                'v2g':          ('⚡ V2G Ready',    '#7c3aed', '#ede9fe', '#c4b5fd'),
                'three_phase':  ('🔌 3 Phase',      '#0369a1', '#dbeafe', '#93c5fd'),
                'single_phase': ('🔌 Single Phase', '#0891b2', '#cffafe', '#67e8f9'),
                'battery':      ('🔋 Battery',      '#10b981', '#d1fae5', '#6ee7b7'),
                'ev_charger':   ('🚗 EV Charger',   '#f59e0b', '#fef3c7', '#fcd34d'),
            }
            ordered = [k for k in ('v2g','three_phase','single_phase','battery','ev_charger') if k in _tags]
            if ordered:
                pills = ''.join(
                    f'<span style="display:inline-block;background:{_TAG_META[t][2]};'
                    f'color:{_TAG_META[t][1]};border:1px solid {_TAG_META[t][3]};'
                    f'padding:3px 10px;border-radius:14px;font-size:11px;'
                    f'font-weight:700;margin:2px 4px 2px 0;white-space:nowrap;">'
                    f'{_TAG_META[t][0]}</span>'
                    for t in ordered
                )
                tag_pills_html = f'<p style="margin:8px 0 0">{pills}</p>'
        except Exception as _tag_err:
            print(f'[email tags] lookup failed for {task_id}: {_tag_err}')

    appt_banner = ''
    if appointment:
        _when = appointment.get('when') or 'TBC'
        _type = appointment.get('type') or 'Appointment'
        _aphone = appointment.get('phone') or phone or ''
        _call_btn = (
            f'<a href="tel:{_aphone}" style="display:inline-block;background:rgba(255,255,255,0.25);'
            f'color:white;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:700;'
            f'font-size:14px;margin-top:10px">📞 Call {_aphone}</a>'
        ) if _aphone else ''
        appt_banner = (
            '<div style="background:#047857;color:white;padding:16px 20px;border-bottom:2px solid #065f46">'
            '<div style="font-size:11px;font-weight:700;letter-spacing:1px;opacity:0.9">📅 APPOINTMENT BOOKED</div>'
            f'<div style="font-size:18px;font-weight:700;margin-top:4px">{_when}</div>'
            f'<div style="font-size:14px;opacity:0.95;margin-top:2px">{_type}</div>'
            f'{_call_btn}'
            '</div>'
        )
    html = (
        '<div style="font-family:sans-serif;max-width:620px;margin:0 auto">'
        '<div style="background:'+header_bg+';color:white;padding:20px;border-radius:8px 8px 0 0">'
        '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap">'
        '<h2 style="margin:0">'+header_title+'</h2>'
        '<span style="background:rgba(255,255,255,0.25);padding:4px 14px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:0.5px">'+badge_text+'</span>'
        '</div>'
        '<p style="opacity:.8;margin:4px 0 0">'+now+' &middot; '+src+'</p>'
        +('<p style="margin:6px 0 0"><span style="display:inline-block;background:rgba(255,255,255,0.18);padding:4px 12px;border-radius:16px;font-size:12px;font-weight:600">Lead Source: '+source_badge_text+'</span></p>' if source_badge_text else '')
        +tag_pills_html
        +'</div>'
        +appt_banner
        +'<div style="padding:20px;border:1px solid #e2e8f0">'
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
        f'{abtns2}'
        '<p style="font-weight:600;color:#6B7280;font-size:13px;margin-top:12px">Lead Status</p>'
        f'{sbtns}'
        f'{no_reply_btn}'
        '</div>'
        '<div style="background:'+header_bg+';padding:12px;border-radius:0 0 8px 8px;text-align:center">'
        + (f'<a href="https://www.jottask.app/task/{task_id}" style="color:white;font-weight:bold;text-decoration:none">Open Jottask</a>'
           if task_id else
           '<a href="https://www.jottask.app/" style="color:white;font-weight:bold;text-decoration:none">Open Jottask</a>')
        +'</div></div>'
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
            if appointment:
                lead_prefix = 'APPOINTMENT'
            elif reminder_tag:
                lead_prefix = f"REMINDER ({reminder_tag})"
            else:
                lead_prefix = 'New Lead'
            # Append a short task-id suffix so two leads for the same customer
            # (different properties) can't collide on mail-client threading.
            # Without this, Gmail / Outlook collapse messages with very
            # similar Subject + From + nearby timestamps — which is why
            # Ali Masoodi - 32 Legacy Cres's email "didn't come through":
            # it threaded under Tapsall's and never surfaced as a new mail.
            short = f' [#{task_id.split("-")[0][:6]}]' if task_id else ''
            email_subject = (f"{lead_prefix}: {name} - {badge_text} | {brief_note}{short}"
                             if brief_note else f"{lead_prefix}: {name} - {badge_text}{short}")
        from email_utils import send_email as _send_email
        ok, err = _send_email(NOTIFY, email_subject, html, category='dsw_lead', task_id=task_id)
        if ok:
            print("Email sent:", name)
        else:
            print(f"Email error ({name}): {err}")
        return ok, err
    except Exception as e:
        print("Email error:", e)
        return False, str(e)

def _normalize_phone_for_dedup(p):
    """Strip everything but digits and drop a leading 61 (AU country code)
    so '0468876196' and '+61468876196' compare equal."""
    if not p:
        return ''
    digits = re.sub(r'\D', '', str(p))
    if digits.startswith('61') and len(digits) == 11:
        digits = '0' + digits[2:]
    return digits


def _find_recent_pending_dsw_task(client_name, hours=2, phone=None):
    """Look up a pending DSW Solar task for this client created within the
    last `hours` window. Returns the most recent match, or None.

    Used as a duplicate-fire guard at the top of process(): if a second
    SMS / forwarded-email / admin retrigger arrives shortly after the first,
    we don't want to spawn a second OpenSolar project, a second task, and a
    second lead email. Falls back silently to None on any error so the
    caller stays on the create path rather than crashing.

    Matches on client_name (case-insensitive) OR client_phone (normalised).
    Phone matching catches the case where the same contact gets renamed
    between fires (e.g. 'Ali Masoodi' → 'Ali Masoodi - 6 Tapsall Pl') so
    the second send still gets short-circuited.
    """
    if (not client_name or client_name.lower() == 'unknown') and not phone:
        return None
    try:
        from task_manager import TaskManager
        from datetime import timezone as _tz
        cutoff = (datetime.now(_tz.utc) - timedelta(hours=hours)).isoformat()
        tm = TaskManager()
        q = tm.supabase.table('tasks')\
            .select('id, title, client_name, client_phone, status, lead_status, due_date, due_time, '
                    'description, created_at, reminder_sent_at, user_id')\
            .eq('status', 'pending').eq('category', 'DSW Solar')\
            .gte('created_at', cutoff)\
            .order('created_at', desc=True).limit(20)
        # Build the OR filter: client_name ILIKE OR client_phone matches any
        # of the normalised phone permutations (raw, +61, 0-prefixed)
        clauses = []
        if client_name and client_name.lower() != 'unknown':
            clauses.append(f'client_name.ilike.{client_name}')
        norm = _normalize_phone_for_dedup(phone)
        if norm:
            # Cover the common stored variants
            variants = {norm, '+61' + norm[1:] if norm.startswith('0') else norm,
                        norm[1:] if norm.startswith('0') else norm}
            for v in variants:
                if v:
                    clauses.append(f'client_phone.eq.{v}')
        if not clauses:
            return None
        r = q.or_(','.join(clauses)).execute()
        return (r.data or [None])[0]
    except Exception as e:
        print(f"[dedup] lookup failed for {client_name!r}/{phone!r}: {e}")
        return None


def _resend_lead_email_for_recent(task, contact, full, cid, name,
                                  phone, email, addr, src, summary,
                                  source_badge_text, crm_url):
    """Short-circuit path for the dedup guard in process(): the lead is
    fresh (recent pending task already exists), so re-send the existing
    task's lead email without spawning a new OpenSolar project or task.

    Prefers the OpenSolar URL embedded in the existing task description;
    falls back to the PipeReply CRM note if absent. No new CRM note is
    written and the old task's reminder_sent_at is updated so the
    scheduler doesn't compound with another reminder.
    """
    desc = task.get('description') or ''
    os_m = re.search(r'^OpenSolar:\s*(https?://\S+)', desc, re.MULTILINE)
    os_url = os_m.group(1) if os_m else (get_os_url_from_crm(cid) or '')

    lead_status = task.get('lead_status') or 'new_lead'
    task_id = task['id']

    ok, err = send_email(
        name, phone, addr, src, summary, crm_url, os_url,
        task_id=task_id, lead_status=lead_status,
        email=email, source_badge_text=source_badge_text,
    )
    if ok:
        try:
            from task_manager import TaskManager
            from datetime import timezone as _tz
            TaskManager().supabase.table('tasks').update({
                'reminder_sent_at': datetime.now(_tz.utc).isoformat()
            }).eq('id', task_id).execute()
        except Exception as _e:
            print(f"[dedup] could not stamp reminder_sent_at on {task_id[:8]}: {_e}")
    print(f"[dedup] resent existing lead email for {name} → task {task_id[:8]} (ok={ok})")
    return ok, err


def process(contact, task_id=None, lead_status=None, is_new_contact=True,
            force_new=False, address_override=None):
    """Process a DSW lead contact.

    is_new_contact=True  → freshly created Pipereply contact; overwrite CRM note
    is_new_contact=False → existing contact being reused; add a new CRM note to
                           preserve history rather than overwriting the old one
    force_new=True       → bypass the duplicate-fire dedup guard. Use when
                           legitimately creating a second lead for the same
                           contact (e.g. one customer with multiple properties,
                           each needing its own OpenSolar project + Jottask
                           task).
    address_override     → dict like {'address1','city','state','postalCode'}
                           used in place of PipeReply's stored address fields.
                           Required for force_new scenarios where the second
                           property has a different address from the contact's
                           primary, otherwise the new OpenSolar project would
                           geocode at the primary address.
    """
    t0 = time.time()
    cid = contact.get("id")
    full = get_full(cid)
    # Prefer contactName from full (PipeReply often puts the full name in
    # contactName AND duplicates it into firstName + lastName both, so
    # concatenating firstName+lastName would double it). Only fall back
    # through firstName+lastName when contactName is blank.
    _full_name = (
        full.get('contactName')
        or contact.get('contactName')
        or ((full.get('firstName') or '') + ' ' + (full.get('lastName') or '')).strip()
        or 'Unknown'
    )
    name = " ".join(w.capitalize() for w in _full_name.split())

    # Apply address_override here so all downstream code (dedup short-circuit
    # address fallback, OpenSolar payload, task description) sees the right
    # address. The override mutates `full` in-place — get_full was just called
    # so we own the dict.
    if address_override and isinstance(address_override, dict):
        for k in ('address1', 'city', 'state', 'postalCode'):
            if address_override.get(k):
                full[k] = address_override[k]
        print(f"[override] address: {full.get('address1')}, {full.get('city')} "
              f"{full.get('state')} {full.get('postalCode')}")

    # ── Duplicate-fire guard ─────────────────────────────────────────────
    # If a pending DSW Solar task already exists for this client and was
    # created in the last 2 hours, the most recent prior process() call has
    # already done the heavy work. Spawning a second OpenSolar project +
    # task + email (the "triple-send" bug) is wasted work and clutters the
    # CRM. Skip to resending the existing task's lead email instead.
    #
    # Only short-circuits the new-lead path. Callers that pass task_id
    # explicitly (reminder resends, admin retriggers) bypass this guard.
    # force_new=True also bypasses — used for the second-property-same-
    # customer scenario.
    if not task_id and not force_new:
        _dedup_phone = full.get("phone") or contact.get("phone", "")
        _recent = _find_recent_pending_dsw_task(name, hours=2, phone=_dedup_phone)
        if _recent:
            print(f"[dedup] {name}: pending DSW task {_recent['id'][:8]} created "
                  f"{(_recent.get('created_at') or '')[:19]} (<2h ago) — short-circuiting")
            _phone = full.get("phone") or contact.get("phone", "N/A")
            _email = full.get("email") or contact.get("email", "")
            _addr  = join_address_parts(
                full.get("address1") or contact.get("address1", ""),
                full.get("city")     or contact.get("city", ""),
                full.get("state")    or contact.get("state", ""),
                full.get("postalCode") or contact.get("postalCode", ""),
            )
            _crm_url = CRM_BASE + "/detail/" + cid
            _src = source(full)
            _crm_notes_text = get_crm_notes_bodies(cid)
            _summary, _ref = summarise(name, _phone, _addr, _src,
                                       full.get("notes", "") or "",
                                       full.get("customFields", []) or [])
            if not _ref and _src.lower().startswith('referral'):
                _ref = get_referred_by_from_crm(cid)
            _badge = source_badge(_src, _ref)
            if _crm_notes_text:
                _summary = f"{_summary}\n\nCRM NOTES:\n{_crm_notes_text}"
            _resend_lead_email_for_recent(_recent, contact, full, cid, name,
                                          _phone, _email, _addr, _src,
                                          _summary, _badge, _crm_url)
            print("Done in", round(time.time() - t0, 1), "s:", name, "(dedup short-circuit)")
            return
    phone = full.get("phone") or contact.get("phone","N/A")
    email = full.get("email") or contact.get("email","")
    address = full.get("address1") or contact.get("address1","")
    city = full.get("city") or contact.get("city","")
    state = full.get("state") or contact.get("state","")
    postcode = full.get("postalCode") or contact.get("postalCode","")
    notes = full.get("notes","") or ""
    custom = full.get("customFields",[]) or []
    src = source(full)
    crm_url = CRM_BASE+"/detail/"+cid
    is_reminder = task_id is not None

    # Prefer PipeReply's structured firstName/lastName; fall back to splitting display name.
    name_parts = name.split()
    first_name = (full.get('firstName') or full.get('firstNameLowerCase') or (name_parts[0] if name_parts else 'Unknown')).strip()
    last_name  = (full.get('lastName')  or full.get('lastNameLowerCase')  or (' '.join(name_parts[1:]) if len(name_parts) > 1 else '')).strip()

    # Fetch PipeReply CRM notes once — used for address fallback, referral scrape, and summary enrichment.
    crm_notes_text = get_crm_notes_bodies(cid)

    # Address fallback: if the contact has no structured address, scrape one out of
    # CRM notes (or the contact's free-text notes field) before we build OpenSolar.
    if not any([address, city, state, postcode]):
        parsed = extract_address_from_notes(crm_notes_text) or extract_address_from_notes(notes)
        if parsed:
            address, city, state, postcode = parsed
            print(f"[Address] Recovered from CRM notes: {address}, {city}, {state} {postcode}")

    addr = join_address_parts(address, city, state, postcode)

    if is_reminder:
        print(f"[Pipereply] Resending reminder: {name} ({cid[:8]})")
    elif is_new_contact:
        print(f"[Pipereply] Created new contact: {name} ({cid[:8]})")
    else:
        print(f"[Pipereply] Reused existing contact: {name} ({cid[:8]})")

    summary, referred_by = summarise(name, phone, addr, src, notes, custom)
    # Fallback: if referred_by not in contact notes/custom fields, try CRM notes API
    if not referred_by and src.lower().startswith('referral'):
        referred_by = get_referred_by_from_crm(cid)
    source_badge_text = source_badge(src, referred_by)
    print(f"[Source] {name}: {source_badge_text}")

    # Append raw CRM notes so they show up in the email + task description.
    if crm_notes_text:
        summary = f"{summary}\n\nCRM NOTES:\n{crm_notes_text}"

    if not is_reminder:
        # New lead: check for an existing pending DSW Solar task for this client.
        # If one exists, scrape its MY NOTES + task_notes so they carry forward,
        # reuse its OpenSolar URL (if any), and mark the old task superseded.
        #
        # When force_new=True the caller has explicitly said "this is a separate
        # lead — leave any existing task for this client alone." Skip the whole
        # migrate path or we'd silently cancel the original (this happened on
        # Ali Masoodi's two-property batch — Legacy Crescent got cancelled when
        # Tapsall was added).
        existing_task = None
        prev_notes_block = ''
        reused_os_url = None
        if force_new:
            print(f"[force_new] skipping find_existing_task_by_client migrate path")
        else:
            try:
                from task_manager import TaskManager
                _tm = TaskManager()
                _users = _tm.supabase.table("users").select("id").eq("email","rob@cloudcleanenergy.com.au").execute()
                _uid = _users.data[0]["id"] if _users.data else None
                if _uid:
                    existing_task = _tm.find_existing_task_by_client(client_name=name, user_id=_uid)
                    # Only migrate from pending DSW Solar tasks — ignore other categories
                    if existing_task and (existing_task.get('category') != 'DSW Solar' or existing_task.get('status') != 'pending'):
                        existing_task = None
            except Exception as e:
                print(f"[Migrate] lookup failed: {e}")

        if existing_task:
            old_tid = existing_task['id']
            old_desc = existing_task.get('description') or ''
            print(f"[Migrate] Found existing task {old_tid[:8]} for {name} — migrating notes")

            # Reuse existing OpenSolar URL if present in old description
            _os_m = re.search(r'^OpenSolar:\s*(https?://\S+)', old_desc, re.MULTILINE)
            if _os_m:
                reused_os_url = _os_m.group(1)
                print(f"[Migrate] Reusing OpenSolar URL from old task: {reused_os_url}")

            # Scrape MY NOTES section from old description
            prev_parts = []
            if 'MY NOTES:' in old_desc:
                my_notes = old_desc.split('MY NOTES:', 1)[1].strip()
                if my_notes:
                    prev_parts.append(f"MY NOTES (from old task):\n{my_notes}")

            # Scrape task_notes entries (chronological)
            try:
                old_notes = _tm.get_all_task_notes(old_tid) or []
                if old_notes:
                    lines = []
                    for n in old_notes:
                        ts = (n.get('created_at') or '')[:16].replace('T', ' ')
                        content = (n.get('content') or '').strip()
                        if content:
                            lines.append(f"[{ts}] {content}")
                    if lines:
                        prev_parts.append("TASK NOTES (from old task):\n" + '\n'.join(lines))
            except Exception as e:
                print(f"[Migrate] Failed to fetch old task_notes: {e}")

            prev_notes_block = '\n\n'.join(prev_parts)

        # Reuse existing OpenSolar project if we have one; otherwise create fresh
        if reused_os_url:
            os_url = reused_os_url
        else:
            _, os_url = make_opensolar(name, phone, email, address, city, state, postcode,
                                       first_name=first_name, last_name=last_name)

        if os_url:
            # Overwrite CRM note for brand-new contacts; add new note for reused ones
            save_to_crm(cid, os_url, summary, overwrite=is_new_contact)
        if not icloud_contact(name, phone, email=email, address=address, city=city, state=state, postcode=postcode, src=src):
            mac_contact(name, phone, src=src)
        task_id = make_task(
            name, phone, summary, crm_url, os_url, email=email,
            prev_notes_block=prev_notes_block,
            supersede_task_id=(existing_task['id'] if existing_task else None),
            source_badge_text=source_badge_text,
        )
        if not task_id:
            # make_task() failed but didn't raise (legacy paths). Skip the
            # email — sending without a task means the action buttons + Open
            # Jottask link point nowhere, which we just hit with the SMS
            # poller's anon-key run on Martyn Hancock + Craig Jolly.
            print(f"❌ Aborting send_email for {name}: make_task returned no task_id")
            return
    else:
        # Reminder resend: look up OpenSolar URL from existing CRM note
        os_url = get_os_url_from_crm(cid)

    send_email(name, phone, addr, src, summary, crm_url, os_url, task_id, lead_status, email=email, source_badge_text=source_badge_text)
    print("Done in", round(time.time()-t0,1), "s:", name)

def send_dsw_reminder_for_task(task, reminder_tag):
    """Send a DSW lead reminder using fresh PipeReply data.

    Re-reads the contact, CRM notes, and OpenSolar URL from PipeReply and
    rebuilds the summary via summarise() so Rob sees current lead data
    (current status, latest CRM notes, new address if any) rather than
    whatever was frozen into the task description when the lead first
    arrived.

    Falls back to reconstructing from the task description if PipeReply
    is unreachable or the task predates the CRM-URL convention, so a
    reminder still goes out during an outage.

    Returns (ok, err).
    """
    desc = task.get('description') or ''
    name = task.get('client_name') or (task.get('title') or '').replace('Call ', '').replace(' - New DSW Lead', '').strip() or 'Unknown'
    task_id = task.get('id')
    lead_status = task.get('lead_status') or 'new_lead'

    def _grab(label, text=desc):
        m = re.search(rf'^{label}:\s*(.+)$', text, re.MULTILINE)
        return m.group(1).strip() if m else ''

    crm_url = _grab('CRM')
    cid = ''
    if crm_url:
        m = re.search(r'/contacts/detail/([^/?\s]+)', crm_url)
        if m:
            cid = m.group(1)

    phone = email = source_badge_text = os_url = summary = ''
    fresh = False

    if cid:
        try:
            full = get_full(cid)
            if full:
                phone    = full.get('phone')      or ''
                email    = full.get('email')      or ''
                address  = full.get('address1')   or ''
                city     = full.get('city')       or ''
                state    = full.get('state')      or ''
                postcode = full.get('postalCode') or ''
                notes_field = full.get('notes') or ''
                custom   = full.get('customFields', []) or []

                crm_notes_text = get_crm_notes_bodies(cid)

                if not any([address, city, state, postcode]):
                    parsed = extract_address_from_notes(crm_notes_text) or extract_address_from_notes(notes_field)
                    if parsed:
                        address, city, state, postcode = parsed

                addr = join_address_parts(address, city, state, postcode)
                os_url = get_os_url_from_crm(cid) or ''

                src = source(full)
                summary, referred_by = summarise(name, phone, addr, src, notes_field, custom)
                if not referred_by and src.lower().startswith('referral'):
                    referred_by = get_referred_by_from_crm(cid)
                source_badge_text = source_badge(src, referred_by)

                if crm_notes_text:
                    summary = f"{summary}\n\nCRM NOTES:\n{crm_notes_text}"

                fresh = True
                print(f"[DSW reminder] Fresh PipeReply data for {name} ({cid[:8]})")
        except Exception as e:
            print(f"[DSW reminder] Fresh fetch failed for {cid[:8]}, falling back to description: {e}")

    if not fresh:
        phone = _grab('Phone') or 'N/A'
        email = _grab('Email')
        source_badge_text = _grab('Source')
        os_raw = _grab('OpenSolar')
        os_url = os_raw if os_raw.startswith('http') else ''
        body = desc
        if '\n\n' in body:
            body = body.split('\n\n', 1)[1]
        if '--- PREVIOUS NOTES ---' in body:
            body = body.split('--- PREVIOUS NOTES ---', 1)[0]
        summary = body.strip() or '(summary unavailable)'

    src_label = source_badge_text.split('·')[0].strip() if source_badge_text else ''

    return send_email(
        name, phone or 'N/A', '', src_label, summary, crm_url, os_url,
        task_id=task_id, lead_status=lead_status,
        email=email, source_badge_text=source_badge_text,
        reminder_tag=reminder_tag,
    )


def resend_email_only(contact_name):
    """Resend the lead email for an existing DSW Solar task by client_name.

    Looks up the pending task, finds the Pipereply contact, and calls
    process() with task_id + lead_status so no new task or OpenSolar
    project is created.
    """
    from supabase import create_client
    from db_keys import get_admin_key
    sb = create_client(os.getenv("SUPABASE_URL"), get_admin_key())

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
