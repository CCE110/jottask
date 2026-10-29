"""Condense the SYSTEM/CRM text of a DSW task description. Rob's own notes are never read, sent or changed.

Safety model (Rob, 29 Oct 2026): default to leaving text alone.
  * The body is partitioned line-by-line into runs. A run is "system" ONLY if it matches a known
    system signature (form fields, "Lead received via", OpenSolar errors, proposal/install email
    boilerplate, the AI Call Summary, booking-note template, the generated REQUIREMENTS/PROPERTY lists).
  * Everything else - the MY NOTES section, mirrored "MY NOTES (ts):" blocks, the Sub-note, and ANY
    unrecognised text (including unlabelled call notes) - is kept VERBATIM.
  * Only the concatenated system runs are sent to the LLM. It cannot see or alter anything else.
  * Non-destructive: the caller stores the result separately (tasks.metadata); description is untouched.
  * partition() guarantees ''.join(run texts) == the original body, and verify() re-checks that every
    verbatim run survives byte-for-byte in the output.
"""
import os
import re

HEADER_KEYS = ('Phone:', 'Email:', 'Address:', 'Source:', 'CRM:', 'OpenSolar:', 'Sub-note:')
APPT_RE = re.compile(r'<!--\s*APPT-POLL\s*-->.*?<!--\s*/APPT-POLL\s*-->', re.S)
MIRROR_RE = re.compile(r'^MY NOTES \([^)\n]*\):[ \t]*\n', re.M)
MIRROR_END = re.compile(r'^(<!--\s*crm-note|OpenSolar:|CUSTOMER REQUIREMENTS|PROPERTY\b|CRM NOTES:|Hi ,|Lead #|<!--\s*APPT-POLL)', re.M)

FORM_KEYS = (r'Size|Systempricetype|Timeframe|Owntheroofspace|Typeofroof|Direction|Storeys|Features|Leadtype|'
             r'Homevisit|Quarterlybill|Importantnotes|Importantnotessplit|Requestedquotes')
LEAD_KEYS = r'System|Priority|Budget approach|Payment|Timeframe|Roof|Panel direction|Quarterly bill|Lead source|Home visit'
SYS_LINE = [re.compile(p) for p in (
    r'^<!--\s*crm-note:.*-->\s*$',
    rf'^({FORM_KEYS}):',
    r'^(Project successfully created\.|Missing Data:|The system has updated this value|Error Code: Unknown|'
    r'There was an issue when attempting to contact OpenSolar|Confirm that the OpenSolar site)',
    r'^https://app\.opensolar\.com/projects/\d+/design\s*$',
    r'^📝 Lead received via',
    r'^Lead #\d+',
    rf'^({LEAD_KEYS}):',
    r'^(CUSTOMER REQUIREMENTS|PROPERTY|CRM NOTES:)\s*$',
    r'^Source: Referral — ',
    r'^pls update appt status after calling the lead',
    r'^.* Scheduled a call with you for ',
    r'^Appointment: .* due_at offset',
    r'^Jottask due_at offset',
    r'^Appointment confirmed via PipeReply event',
)]
BULLET = re.compile(r'^\s*\*\s')
BOOKING = re.compile(r'^\w+, \w+ \d+ \d+\s?(AM|PM) QLD time with .+ - ')


def _starts(line, pats):
    return any(p.match(line) for p in pats)


def split_description(desc):
    """-> dict(header=[lines], appt=[blocks], body=str, mine=[(kind, text)]). Returns None if a 'MY NOTES'
    occurrence is in neither recognised form (caller must then leave the task alone)."""
    d = desc or ''
    for m in re.finditer(r'MY NOTES', d):
        s = d[m.start():m.start() + 70]
        if not (s.startswith('MY NOTES:') or re.match(r'MY NOTES \([^)\n]*\):', s)):
            return None
    mine = []
    body = d
    if 'MY NOTES:' in body:
        body, top = body.split('MY NOTES:', 1)
        mine.append(('MY NOTES', top))
    appt = APPT_RE.findall(body)
    body = APPT_RE.sub('', body)
    pieces, pos = [], 0
    for m in MIRROR_RE.finditer(body):
        if m.start() < pos:
            continue
        nxt = MIRROR_END.search(body, m.end())
        end = nxt.start() if nxt else len(body)
        mine.append(('MY NOTES (mirrored)', body[m.start():end]))
        pieces.append(body[pos:m.start()])
        pos = end
    pieces.append(body[pos:])
    body = ''.join(pieces)
    lines = body.split('\n')
    header = []
    while lines and (lines[0].startswith(HEADER_KEYS) or not lines[0].strip()):
        if lines[0].strip():
            header.append(lines[0])
        lines.pop(0)
    return {'header': header, 'appt': appt, 'body': '\n'.join(lines), 'mine': mine}


def partition(body):
    """Split body into ordered runs [(kind, text)] with kind in {'system', 'verbatim'}. Lossless."""
    lines = body.split('\n')
    kinds = []
    i, n = 0, len(lines)
    in_block = None
    prev_form_features = False
    while i < n:
        ln = lines[i]
        k = 'verbatim'
        if in_block == 'proposal':
            k = 'system'
            if ln.startswith('View Solar Energy Proposal'):
                in_block = None
        elif in_block == 'install':
            k = 'system'
            if ln.startswith('No. of Batteries:'):
                in_block = None
        elif in_block == 'ai':
            k = 'system'
        elif in_block == 'bullets':
            if BULLET.match(ln):
                k = 'system'
            else:
                in_block = None
                continue
        elif in_block == 'booking':
            if ln.strip():
                k = 'system'
            else:
                in_block = None
        else:
            if ln.startswith('Message contained within proposal email:'):
                in_block, k = 'proposal', 'system'
            elif re.match(r'^Hi ,\s*$', ln):
                in_block, k = 'install', 'system'
            elif ln.startswith('AI Call Summary:'):
                in_block, k = 'ai', 'system'
            elif re.match(r'^(CUSTOMER REQUIREMENTS|PROPERTY)\s*$', ln):
                in_block, k = 'bullets', 'system'
            elif BOOKING.match(ln):
                in_block, k = 'booking', 'system'
            elif _starts(ln, SYS_LINE):
                k = 'system'
            elif prev_form_features and ln.strip() and ':' not in ln and len(ln) < 40:
                k = 'system'                              # 2nd line of "Features:\nBattery System"
            elif not ln.strip():
                k = 'blank'
        prev_form_features = ln.startswith('Features:') or (prev_form_features and k == 'system' and ':' not in ln)
        kinds.append(k)
        i += 1
    # blank lines join the run before them (kept in place so the partition stays lossless)
    runs = []
    for ln, k in zip(lines, kinds):
        if k == 'blank':
            k = runs[-1][0] if runs else 'verbatim'
        if runs and runs[-1][0] == k:
            runs[-1][1].append(ln)
        else:
            runs.append([k, [ln]])
    out = [(k, '\n'.join(ls)) for k, ls in runs]
    assert '\n'.join(t for _, t in out) == body, 'partition not lossless'
    return out


PROMPT = """You are tidying the SYSTEM-GENERATED CRM text of a solar sales lead for the salesperson's reference.
Below is raw text from lead forms, CRM automations, OpenSolar status messages and an AI call summary. It repeats
the same facts many times. Merge it into ONE clean, deduplicated fact list.

Rules:
- State each fact ONCE. Where sources disagree, keep the more specific value and add "(also: ...)" only if truly different.
- Use ONLY facts present in the text. Never invent, infer or soften. Keep numbers/units exactly as written.
- Drop boilerplate: email signatures/addresses, payment-option marketing text, OpenSolar error/status messages, template phrases.
- Group under short headings, plain text, one fact per line starting with "- ":
  Requirements / Property / Bill & electricity / Timeframe & payment / Appointment / Call takeaways & next steps / Still to collect
  (omit empty groups).
- Mention the quarterly/electricity bill ONCE only (one line, under Bill & electricity), never again elsewhere.
- "Still to collect": at most 5 short items (2-6 words each), most important first. No sentences.
- "Appointment" holds ONLY a booked call/visit date, time or type. Whether a home visit is wanted, and any
  other preference, belongs under Requirements. If no appointment is stated, omit the Appointment group.
- No preamble, no closing remarks.

TEXT:
"""


def llm_merge(system_text, client=None, model='claude-haiku-4-5-20251001'):
    if client is None:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    r = client.messages.create(model=model, max_tokens=1200, messages=[{'role': 'user', 'content': PROMPT + system_text}])
    return ''.join(b.text for b in r.content if getattr(b, 'text', None)).strip()


STILL_MAX = 5
_STILL = re.compile(r'^still to collect\b', re.I)
_BILL = re.compile(r'\b(bill|quarterly|electricity (cost|usage))\b', re.I)


def tighten(merged):
    """Deterministic clean-up of the LLM output (system text only): drop repeated lines, keep ONE bill line,
    cap the 'Still to collect' list. Never touches anything but the merged system summary."""
    out, seen, bill_seen, in_still, still_n = [], set(), False, False, 0
    for ln in (merged or '').split('\n'):
        s = ln.strip()
        if not s.startswith('-'):                           # heading / blank
            in_still, still_n = bool(_STILL.match(s.rstrip(':'))), 0
            out.append(ln)
            continue
        key = re.sub(r'\W+', ' ', s.lower()).strip()
        if key in seen:
            continue
        if _BILL.search(s) and not in_still:
            if bill_seen:
                continue
            bill_seen = True
        if in_still:
            still_n += 1
            if still_n > STILL_MAX:
                continue
        seen.add(key)
        out.append(ln)
    return '\n'.join(out).strip()


def condense(desc, merge=llm_merge):
    """-> (display_text, report) or None when the task must be left alone (unrecognised MY NOTES form).

    `merge(system_text)` is the only thing that sees system text; nothing else is ever passed to it.
    """
    p = split_description(desc)
    if p is None:
        return None
    runs = partition(p['body'])
    system_text = '\n'.join(t for k, t in runs if k == 'system').strip()
    verbatim = [t.strip('\n') for k, t in runs if k == 'verbatim' and t.strip()]
    merged = tighten(merge(system_text)) if system_text else ''
    out = list(p['header'])
    out += ['', '── CRM SUMMARY (condensed - reference only) ──', merged or '(nothing to condense)']
    if verbatim:
        out += ['', '── OTHER NOTES (unrecognised, kept verbatim) ──'] + verbatim
    if p['appt']:
        out += [''] + p['appt']
    out += ['', '── MY NOTES (verbatim, untouched) ──']
    out += [t for _, t in p['mine']] if p['mine'] else ['(none)']
    text = '\n'.join(out)
    mirrored = [t for kind, t in p['mine'] if kind != 'MY NOTES']
    cust_display = '\n'.join(['── CRM SUMMARY (condensed - reference only) ──', merged or '(nothing to condense)']
                             + (['', '── OTHER NOTES (unrecognised, kept verbatim) ──'] + verbatim if verbatim else [])
                             + (['', '── MY NOTES (mirrored copy, verbatim) ──'] + mirrored if mirrored else []))
    report = {'cust_display': cust_display, 'system_chars': len(system_text), 'verbatim_runs': verbatim, 'mine': [t for _, t in p['mine']],
              'header': p['header'], 'system_text': system_text, 'merged': merged}
    return text, report


def verify(report, text):
    """True only if every verbatim run, MY NOTES block and header line is present byte-for-byte."""
    return (all(v in text for v in report['verbatim_runs'])
            and all(m in text for m in report['mine'])
            and all(h in text for h in report['header']))


VERSION = 2


def stored_or_build(task, sb, merge=llm_merge, force=False):
    """Return the stored condensed block for this task, rebuilding it (and storing it in tasks.metadata.condensed)
    when the description changed since it was built. The description itself is NEVER written.

    -> dict(text, cust_display, source_sha, built_at, ...) or None when the task must be left alone.
    """
    import hashlib
    from datetime import datetime, timezone
    desc = task.get('description') or ''
    sha = hashlib.sha1(desc.encode('utf-8')).hexdigest()
    meta = task.get('metadata') or {}
    cur = meta.get('condensed') or {}
    if not force and cur.get('source_sha') == sha and cur.get('version') == VERSION:
        return cur
    res = condense(desc, merge=merge)
    if res is None:
        return None
    text, rep = res
    if not verify(rep, text):                 # never store (or show) a block that fails the verbatim check
        return None
    new = {'version': VERSION, 'source_sha': sha, 'built_at': datetime.now(timezone.utc).isoformat(),
           'text': text, 'cust_display': rep['cust_display'], 'system_chars': rep['system_chars']}
    sb.table('tasks').update({'metadata': {**meta, 'condensed': new}}).eq('id', task['id']).execute()
    return new
