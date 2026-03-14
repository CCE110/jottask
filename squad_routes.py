"""
Squad Routes
Flask blueprint for Squad — AI-native youth soccer team management.

Blueprint prefix: none (routes use explicit /squad/ and /p/ prefixes)
"""

import json
import os
import uuid
from datetime import datetime

import pytz
from flask import (Blueprint, Response, jsonify, redirect, render_template,
                   request, session, url_for)
from supabase import create_client, Client

from auth import login_required

squad_bp = Blueprint('squad', __name__)

AEST = pytz.timezone('Australia/Brisbane')

# ── Lazy Supabase init ────────────────────────────────────────────────────────

_supabase = None

def _db() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
    return _supabase


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_json_field(value):
    """If value is a JSON string, parse it. Otherwise return as-is."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return value or {}


def _get_squad_for_user(user_id: str):
    """Return the first squad managed by this user, or None."""
    result = _db().table('squads').select('*').eq('manager_user_id', user_id).limit(1).execute()
    return result.data[0] if result.data else None


# ── Manager: Dashboard ────────────────────────────────────────────────────────

@squad_bp.route('/squad/')
@login_required
def dashboard():
    user_id = session.get('user_id')
    now = datetime.now(AEST)
    today = now.date().isoformat()

    squad = _get_squad_for_user(user_id)
    squad_id = squad['id'] if squad else None

    # Upcoming events
    upcoming_events = []
    if squad_id:
        ev = _db().table('squad_events') \
            .select('*') \
            .eq('squad_id', squad_id) \
            .gte('event_date', today) \
            .order('event_date') \
            .order('event_time') \
            .limit(10) \
            .execute()
        upcoming_events = ev.data or []

    # Pending inbox count
    inbox_q = _db().table('squad_email_inbox').select('id', count='exact').eq('status', 'pending')
    if squad_id:
        inbox_q = inbox_q.eq('squad_id', squad_id)
    pending_count = (inbox_q.execute().count or 0)

    # Recent inbox items (pending, newest first)
    recent_q = _db().table('squad_email_inbox') \
        .select('id, email_subject, email_from, email_type, status, parsed_data, created_at') \
        .eq('status', 'pending') \
        .order('created_at', desc=True) \
        .limit(4)
    if squad_id:
        recent_q = recent_q.eq('squad_id', squad_id)
    recent_inbox = recent_q.execute().data or []
    for item in recent_inbox:
        item['parsed_data'] = _parse_json_field(item.get('parsed_data'))

    # Players
    players = []
    if squad_id:
        pl = _db().table('squad_players') \
            .select('*') \
            .eq('squad_id', squad_id) \
            .order('player_name') \
            .execute()
        players = pl.data or []

    return render_template('squad/dashboard.html',
        squad=squad,
        upcoming_events=upcoming_events,
        pending_count=pending_count,
        recent_inbox=recent_inbox,
        players=players,
        now=now,
    )


# ── Manager: AI Inbox ─────────────────────────────────────────────────────────

@squad_bp.route('/squad/inbox')
@login_required
def inbox():
    user_id = session.get('user_id')
    tab = request.args.get('tab', 'pending')
    if tab not in ('pending', 'approved', 'dismissed'):
        tab = 'pending'

    squad = _get_squad_for_user(user_id)
    squad_id = squad['id'] if squad else None

    q = _db().table('squad_email_inbox') \
        .select('*') \
        .eq('status', tab) \
        .order('created_at', desc=True) \
        .limit(50)
    if squad_id:
        q = q.eq('squad_id', squad_id)
    items = q.execute().data or []

    for item in items:
        item['parsed_data'] = _parse_json_field(item.get('parsed_data'))

    return render_template('squad/inbox.html', items=items, tab=tab, squad=squad)


@squad_bp.route('/squad/inbox/<item_id>/approve', methods=['POST'])
@login_required
def approve_inbox_item(item_id):
    result = _db().table('squad_email_inbox').select('*').eq('id', item_id).maybe_single().execute()
    if not result.data:
        return jsonify({'error': 'Not found'}), 404

    item = result.data
    parsed = _parse_json_field(item.get('parsed_data'))
    squad_id = item.get('squad_id')
    actions_executed = []

    # Create events for each fixture in the parsed data
    fixtures = parsed.get('fixtures', [])
    for fixture in fixtures:
        if not fixture.get('date'):
            continue
        try:
            _db().table('squad_events').insert({
                'id':              str(uuid.uuid4()),
                'squad_id':        squad_id,
                'event_date':      fixture['date'],
                'event_time':      fixture.get('time'),
                'opponent':        fixture.get('opponent'),
                'venue':           fixture.get('venue'),
                'is_home':         fixture.get('is_home'),
                'event_type':      fixture.get('type', 'game'),
                'source_inbox_id': item_id,
                'created_at':      datetime.now(pytz.UTC).isoformat(),
            }).execute()
            actions_executed.append(
                f"Created event: {fixture['date']} vs {fixture.get('opponent', 'TBD')}"
            )
        except Exception as e:
            actions_executed.append(f"Failed to create event ({fixture.get('date')}): {e}")

    # Also handle cancellations
    for cancel in parsed.get('cancellations', []):
        if cancel.get('date'):
            actions_executed.append(f"Noted cancellation: {cancel['date']} — {cancel.get('description', '')}")

    _db().table('squad_email_inbox').update({
        'status':           'approved',
        'approved_at':      datetime.now(pytz.UTC).isoformat(),
        'actions_executed': actions_executed,
    }).eq('id', item_id).execute()

    return jsonify({'ok': True, 'actions': actions_executed})


@squad_bp.route('/squad/inbox/<item_id>/dismiss', methods=['POST'])
@login_required
def dismiss_inbox_item(item_id):
    _db().table('squad_email_inbox').update({
        'status':       'dismissed',
        'dismissed_at': datetime.now(pytz.UTC).isoformat(),
    }).eq('id', item_id).execute()
    return jsonify({'ok': True})


# ── Manager: Paste Inbox ──────────────────────────────────────────────────────

@squad_bp.route('/squad/inbox/paste', methods=['GET', 'POST'])
@login_required
def paste_inbox():
    if request.method == 'GET':
        return render_template('squad/paste_inbox.html')

    source = request.form.get('source', 'text').strip()
    text   = request.form.get('text', '').strip()

    if not text:
        return render_template('squad/paste_inbox.html', error='Please paste some text first.')

    from anthropic import Anthropic
    from squad_prompts import parse_pasted_text

    anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    parsed = parse_pasted_text(anthropic, source, text)

    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)

    record = {
        'id':            str(uuid.uuid4()),
        'squad_id':      squad['id'] if squad else None,
        'email_from':    f'pasted:{source}',
        'email_subject': f'Pasted {source} — {datetime.now(AEST).strftime("%d %b %Y %H:%M")}',
        'email_body':    text[:10000],
        'email_date':    datetime.now(pytz.UTC).isoformat(),
        'email_hash':    str(uuid.uuid4()),   # Always unique for pasted items
        'email_type':    parsed.get('email_type', 'club_update'),
        'parsed_data':   parsed,
        'status':        'pending',
        'created_at':    datetime.now(pytz.UTC).isoformat(),
    }
    _db().table('squad_email_inbox').insert(record).execute()

    return redirect(url_for('squad.inbox'))


# ── Manager: Team Roster ──────────────────────────────────────────────────────

@squad_bp.route('/squad/team')
@login_required
def team():
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    squad_id = squad['id'] if squad else None

    players = []
    if squad_id:
        pl = _db().table('squad_players') \
            .select('*') \
            .eq('squad_id', squad_id) \
            .order('player_name') \
            .execute()
        players = pl.data or []

    return render_template('squad/team.html', squad=squad, players=players)


# ── Parent: Magic Link View ───────────────────────────────────────────────────

@squad_bp.route('/p/<token>')
def parent_view(token):
    """Passwordless parent view — no login required."""
    link_result = _db().table('squad_parent_links') \
        .select('*, squad_players(player_name, squad_id), squads(name, cal_token)') \
        .eq('magic_token', token) \
        .eq('is_active', True) \
        .maybe_single() \
        .execute()

    if not link_result.data:
        return ('<html><body style="font-family:sans-serif;text-align:center;padding:60px">'
                '<h2>Link not found or expired.</h2>'
                '<p>Please ask your team manager for a new link.</p>'
                '</body></html>'), 404

    link       = link_result.data
    player     = link.get('squad_players') or {}
    squad      = link.get('squads') or {}
    player_name = player.get('player_name', 'Your Player')
    squad_id    = player.get('squad_id')
    squad_name  = squad.get('name', 'Squad')
    cal_token   = squad.get('cal_token') or token

    # Upcoming events
    today = datetime.now(AEST).date().isoformat()
    events_result = _db().table('squad_events') \
        .select('*') \
        .eq('squad_id', squad_id) \
        .gte('event_date', today) \
        .order('event_date') \
        .limit(20) \
        .execute()
    events = events_result.data or []

    # This parent's RSVPs
    rsvps_result = _db().table('squad_rsvps') \
        .select('event_id, status') \
        .eq('parent_link_id', link['id']) \
        .execute()
    rsvp_map = {r['event_id']: r['status'] for r in (rsvps_result.data or [])}
    for event in events:
        event['rsvp_status'] = rsvp_map.get(event['id'])

    # Calendar URL
    app_url   = os.getenv('APP_URL', 'https://www.jottask.app')
    cal_url   = f"{app_url}/squad/cal/{cal_token}.ics"
    webcal_url = cal_url.replace('https://', 'webcal://').replace('http://', 'webcal://')

    return render_template('squad/parent_view.html',
        player_name=player_name,
        squad_name=squad_name,
        events=events,
        token=token,
        link_id=link['id'],
        cal_url=webcal_url,
    )


@squad_bp.route('/p/<token>/rsvp', methods=['POST'])
def parent_rsvp(token):
    """Handle RSVP submission from parent view. Accepts form or JSON."""
    link_result = _db().table('squad_parent_links') \
        .select('id') \
        .eq('magic_token', token) \
        .eq('is_active', True) \
        .maybe_single() \
        .execute()

    if not link_result.data:
        return jsonify({'error': 'Invalid or expired link'}), 403

    link_id = link_result.data['id']

    if request.is_json:
        data     = request.get_json() or {}
        event_id = data.get('event_id')
        status   = data.get('status')
    else:
        event_id = request.form.get('event_id')
        status   = request.form.get('status')

    if not event_id or status not in ('attending', 'not_attending', 'maybe'):
        return jsonify({'error': 'event_id and valid status required'}), 400

    now_iso = datetime.now(pytz.UTC).isoformat()

    existing = _db().table('squad_rsvps') \
        .select('id') \
        .eq('parent_link_id', link_id) \
        .eq('event_id', event_id) \
        .execute()

    if existing.data:
        _db().table('squad_rsvps').update({
            'status':     status,
            'updated_at': now_iso,
        }).eq('id', existing.data[0]['id']).execute()
    else:
        _db().table('squad_rsvps').insert({
            'id':             str(uuid.uuid4()),
            'parent_link_id': link_id,
            'event_id':       event_id,
            'status':         status,
            'created_at':     now_iso,
        }).execute()

    if request.is_json:
        return jsonify({'ok': True, 'status': status})
    return redirect(url_for('squad.parent_view', token=token))


# ── Calendar Feed ─────────────────────────────────────────────────────────────

@squad_bp.route('/squad/cal/<token>.ics')
def squad_cal(token):
    """Live iCal feed — subscribable in Apple/Google/Outlook Calendar."""
    squad_result = _db().table('squads') \
        .select('*') \
        .eq('cal_token', token) \
        .maybe_single() \
        .execute()

    if not squad_result.data:
        return 'Calendar not found', 404

    squad    = squad_result.data
    squad_id = squad['id']

    events_result = _db().table('squad_events') \
        .select('*') \
        .eq('squad_id', squad_id) \
        .order('event_date') \
        .execute()
    events = events_result.data or []

    from squad_cal import generate_ical
    ical_bytes = generate_ical(squad, events)

    safe_name = squad.get('name', 'squad').replace(' ', '_')
    return Response(
        ical_bytes,
        mimetype='text/calendar; charset=utf-8',
        headers={
            'Content-Disposition': f'inline; filename="{safe_name}.ics"',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
        }
    )
