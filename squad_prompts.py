"""
Squad AI Prompts
Claude prompts for Squad email classification and parsing.

All functions accept an `anthropic` client instance and return dicts (parsed JSON).
"""

import json
from anthropic import Anthropic


# ── Classification ───────────────────────────────────────────────────────────

def classify_email(anthropic: Anthropic, subject: str, body: str) -> str:
    """
    Classify an email into one of three types.

    Returns: 'club_update' | 'parent_message' | 'fixture_list'
    """
    prompt = f"""You are classifying an email for a youth soccer team manager.

Subject: {subject}

Body:
{body[:2000]}

Classify this email into exactly one of these types:
- "club_update": Official club communication — training changes, game rescheduling, venue changes, cancellations, general announcements from the club or association
- "parent_message": A message from a parent — RSVP, apology for absence, injury/medical update, query, complaint, personal note
- "fixture_list": A list of upcoming fixtures or season schedule (usually a table or bullet list of dates, opponents, venues)

Respond with ONLY the type string, nothing else. No quotes, no explanation."""

    response = anthropic.messages.create(
        model='claude-opus-4-6',
        max_tokens=20,
        messages=[{'role': 'user', 'content': prompt}]
    )
    result = response.content[0].text.strip().lower().strip('"\'')
    if result not in ('club_update', 'parent_message', 'fixture_list'):
        return 'club_update'
    return result


# ── Club Update ──────────────────────────────────────────────────────────────

CLUB_UPDATE_PROMPT = """\
You are an assistant helping a youth soccer team manager parse club emails.

Extract structured information and return ONLY valid JSON — no markdown fences, no explanation.

Return this exact JSON structure:
{
  "summary": "1-2 sentence plain English summary of what this email means for the team manager",
  "fixtures": [
    {
      "date": "YYYY-MM-DD or null",
      "time": "HH:MM (24h) or null",
      "opponent": "opponent team name or null",
      "venue": "full venue name/address or null",
      "is_home": true or false or null,
      "type": "game" | "training" | "cup" | "other"
    }
  ],
  "cancellations": [
    {
      "date": "YYYY-MM-DD or null",
      "description": "what was cancelled and why"
    }
  ],
  "venue_changes": [
    {
      "date": "YYYY-MM-DD or null",
      "original_venue": "string or null",
      "new_venue": "string or null",
      "reason": "reason or null"
    }
  ],
  "proposed_actions": [
    {
      "type": "create_event" | "update_event" | "cancel_event" | "notify_parents",
      "description": "plain English description of the action to take",
      "data": {}
    }
  ],
  "confidence": 0.95
}"""


def parse_club_update(anthropic: Anthropic, subject: str, body: str) -> dict:
    """Parse a club update email. Returns structured dict."""
    response = anthropic.messages.create(
        model='claude-opus-4-6',
        max_tokens=2000,
        system=CLUB_UPDATE_PROMPT,
        messages=[{
            'role': 'user',
            'content': f"Subject: {subject}\n\nBody:\n{body[:5000]}"
        }]
    )
    text = response.content[0].text.strip()
    # Strip markdown fences if model adds them despite instructions
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {'raw_response': text, 'error': 'JSON parse failed', 'summary': subject}


# ── Parent Message ────────────────────────────────────────────────────────────

PARENT_MESSAGE_PROMPT = """\
You are an assistant helping a youth soccer team manager parse parent emails.

Extract structured information and return ONLY valid JSON — no markdown fences, no explanation.

Return this exact JSON structure:
{
  "summary": "1-2 sentence plain English summary of the parent's message",
  "parent_name": "parent's name if identifiable, else null",
  "player_name": "player's name if mentioned, else null",
  "intent": "rsvp" | "apology" | "medical_update" | "query" | "complaint" | "other",
  "rsvp": {
    "event_date": "YYYY-MM-DD or null",
    "status": "attending" | "not_attending" | "maybe" | null,
    "reason": "reason given or null"
  },
  "medical_update": {
    "condition": "description of injury/illness or null",
    "cleared_to_play": true | false | null,
    "notes": "any additional medical notes or null"
  },
  "proposed_actions": [
    {
      "type": "record_rsvp" | "update_medical" | "send_reply" | "add_note",
      "description": "plain English description of the action",
      "data": {}
    }
  ],
  "suggested_reply": "a friendly, brief reply the manager could send to the parent, or null"
}"""


def parse_parent_message(anthropic: Anthropic, subject: str, body: str) -> dict:
    """Parse a parent message email. Returns structured dict."""
    response = anthropic.messages.create(
        model='claude-opus-4-6',
        max_tokens=1500,
        system=PARENT_MESSAGE_PROMPT,
        messages=[{
            'role': 'user',
            'content': f"Subject: {subject}\n\nBody:\n{body[:5000]}"
        }]
    )
    text = response.content[0].text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {'raw_response': text, 'error': 'JSON parse failed', 'summary': subject}


# ── Fixture List ──────────────────────────────────────────────────────────────

FIXTURE_LIST_PROMPT = """\
You are an assistant helping a youth soccer team manager parse a full season fixture list.

Extract the complete schedule and return ONLY valid JSON — no markdown fences, no explanation.

Return this exact JSON structure:
{
  "summary": "1-2 sentence summary e.g. '18 games from March to August 2026'",
  "team_name": "the team name if mentioned, else null",
  "season": "season identifier e.g. '2026 Spring Season' or null",
  "fixtures": [
    {
      "date": "YYYY-MM-DD or null",
      "time": "HH:MM (24h) or null",
      "opponent": "opponent team name or null",
      "venue": "venue name or null",
      "is_home": true | false | null,
      "type": "game" | "training" | "cup" | "friendly" | "other",
      "round": "round number or description or null"
    }
  ],
  "proposed_actions": [
    {
      "type": "create_event",
      "description": "Create calendar events for all fixtures",
      "count": 18
    }
  ]
}"""


def parse_fixture_list(anthropic: Anthropic, subject: str, body: str) -> dict:
    """Parse a fixture list email. Returns structured dict with full season schedule."""
    response = anthropic.messages.create(
        model='claude-opus-4-6',
        max_tokens=4000,
        system=FIXTURE_LIST_PROMPT,
        messages=[{
            'role': 'user',
            'content': f"Subject: {subject}\n\nBody:\n{body[:8000]}"
        }]
    )
    text = response.content[0].text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {'raw_response': text, 'error': 'JSON parse failed', 'summary': subject}


# ── Pasted Text (WhatsApp / general) ─────────────────────────────────────────

PASTE_PROMPT = """\
You are an assistant helping a youth soccer team manager parse a pasted message.

The text may be from WhatsApp, email, SMS, or any source. Extract any squad-relevant information.

Return ONLY valid JSON — no markdown fences, no explanation:
{
  "summary": "1-2 sentence summary of what this message contains",
  "email_type": "club_update" | "parent_message" | "fixture_list" | "other",
  "fixtures": [
    {
      "date": "YYYY-MM-DD or null",
      "time": "HH:MM (24h) or null",
      "opponent": "string or null",
      "venue": "string or null",
      "is_home": true | false | null,
      "type": "game" | "training" | "other"
    }
  ],
  "cancellations": [],
  "venue_changes": [],
  "proposed_actions": [
    {
      "type": "create_event" | "record_rsvp" | "notify_parents" | "add_note" | "other",
      "description": "plain English description",
      "data": {}
    }
  ],
  "confidence": 0.85
}"""


def parse_pasted_text(anthropic: Anthropic, source: str, text: str) -> dict:
    """
    Parse pasted text from any source (WhatsApp, email, SMS).

    Args:
        source: 'whatsapp' | 'email' | 'text'
        text:   the pasted content

    Returns structured dict.
    """
    response = anthropic.messages.create(
        model='claude-opus-4-6',
        max_tokens=2000,
        system=PASTE_PROMPT,
        messages=[{
            'role': 'user',
            'content': f"Source: {source}\n\nMessage:\n{text[:6000]}"
        }]
    )
    raw = response.content[0].text.strip()
    if raw.startswith('```'):
        raw = raw.split('```')[1]
        if raw.startswith('json'):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {'raw_response': raw, 'error': 'JSON parse failed', 'email_type': 'other'}


# ── Team Sheet Upload ─────────────────────────────────────────────────────────

TEAM_SHEET_SYSTEM = """\
You are an assistant helping a youth soccer team manager extract player and parent information from a team sheet.

The team sheet may be an image (photo of a printed sheet, screenshot) or text/CSV.

Extract all players and parents/guardians and return ONLY valid JSON — no markdown fences, no explanation:
{
  "players": [
    {
      "player_name": "full name",
      "shirt_number": 7,
      "position": "Midfielder or null"
    }
  ],
  "parents": [
    {
      "parent_name": "full name",
      "parent_email": "email or null",
      "player_name": "the player this parent is linked to, or null"
    }
  ]
}

If shirt numbers or positions are not on the sheet, use null. Extract every player and every parent/guardian you can find."""


def parse_team_sheet(anthropic: Anthropic, text: str = None,
                     image_b64: str = None, media_type: str = 'image/png') -> dict:
    """
    Parse a team sheet from either plain text or a base64-encoded image.
    Returns dict with 'players' and 'parents' lists.
    """
    if image_b64:
        messages = [{
            'role': 'user',
            'content': [
                {
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': media_type,
                        'data': image_b64,
                    }
                },
                {
                    'type': 'text',
                    'text': 'Extract all players and parents from this team sheet.'
                }
            ]
        }]
    else:
        messages = [{
            'role': 'user',
            'content': f"Extract all players and parents from this team sheet:\n\n{text[:8000]}"
        }]

    response = anthropic.messages.create(
        model='claude-opus-4-6',
        max_tokens=2000,
        system=TEAM_SHEET_SYSTEM,
        messages=messages,
    )
    raw = response.content[0].text.strip()
    if raw.startswith('```'):
        raw = raw.split('```')[1]
        if raw.startswith('json'):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {'players': [], 'parents': [], 'error': 'JSON parse failed', 'raw': raw}
