"""
Squad Routes
Flask blueprint for Squad — AI-native youth soccer team management.

Blueprint prefix: none (routes use explicit /squad/ and /p/ prefixes)
Routes: /squad/, /squad/inbox, /squad/inbox/paste, /squad/team, /p/<token>
"""

import json
import os
import secrets
import uuid
from collections import defaultdict
from datetime import datetime, date, timedelta

import pytz
from flask import (Blueprint, Response, jsonify, redirect, render_template,
                   request, session, url_for)
from supabase import create_client, Client

from auth import login_required

squad_bp = Blueprint('squad', __name__)

AEST = pytz.timezone('Australia/Brisbane')

# ── Lazy Supabase init ────────────────────────────────────────────────────────

_supabase = None
_supabase_admin = None  # service-role client (bypasses RLS) — used for public routes

def _db() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
    return _supabase


def _admin_db() -> Client:
    """Service-role Supabase client — bypasses RLS for public unauthenticated routes.

    Prefers SUPABASE_SERVICE_KEY if set; falls back to SUPABASE_KEY.
    If SUPABASE_KEY is already the service-role key this is a no-op.
    """
    global _supabase_admin
    if _supabase_admin is None:
        key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_KEY')
        _supabase_admin = create_client(os.getenv('SUPABASE_URL'), key)
    return _supabase_admin


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


# ── Fruit Roster ──────────────────────────────────────────────────────────────
# One player per game brings fruit. Rotation is alphabetical by player_name and
# wraps after everyone has had a turn. Stored on squad_events.fruit_player_id
# so swaps and history are first-class. Migration 028 adds the column.

def _is_missing_column_error(exc) -> bool:
    """True if this is a 'column does not exist' Postgres error — i.e. the
    028 migration hasn't been applied yet. Lets us fail soft and keep
    creating events without fruit duty until Rob runs the SQL."""
    msg = str(exc).lower()
    return 'fruit_player_id' in msg and ('does not exist' in msg or 'column' in msg)


def _pick_next_fruit_player(squad_id: str, exclude_event_id: str = None):
    """Return the player_id whose turn it is on fruit duty for this squad.

    Algorithm: list players alphabetically, find the most recent
    fruit_player_id assignment in this squad's events (any date), and pick
    the next player after that one — wrapping at end. If no prior assignment
    exists, return the first player. Returns None when there are no players
    or the migration isn't applied yet.
    """
    pl = _db().table('squad_players').select('id, player_name')\
        .eq('squad_id', squad_id).order('player_name').execute()
    players = pl.data or []
    if not players:
        return None

    last_fruit_id = None
    try:
        q = _db().table('squad_events').select('fruit_player_id, created_at')\
            .eq('squad_id', squad_id).not_.is_('fruit_player_id', 'null')\
            .order('created_at', desc=True).limit(1)
        if exclude_event_id:
            q = q.neq('id', exclude_event_id)
        r = q.execute()
        if r.data:
            last_fruit_id = r.data[0].get('fruit_player_id')
    except Exception as e:
        if _is_missing_column_error(e):
            return None  # Migration 028 not applied yet — skip auto-assign
        raise

    if not last_fruit_id:
        return players[0]['id']

    ids = [p['id'] for p in players]
    if last_fruit_id not in ids:
        # Previous assignee was removed from squad — start from the top
        return players[0]['id']
    nxt = (ids.index(last_fruit_id) + 1) % len(ids)
    return players[nxt]['id']


def _safe_insert_event(payload: dict):
    """Insert into squad_events. If the fruit_player_id column doesn't exist
    yet (pre-migration), retry without that key so event creation still
    succeeds.
    """
    try:
        return _db().table('squad_events').insert(payload).execute()
    except Exception as e:
        if 'fruit_player_id' in payload and _is_missing_column_error(e):
            payload = {k: v for k, v in payload.items() if k != 'fruit_player_id'}
            return _db().table('squad_events').insert(payload).execute()
        raise


# ── Manager: Dashboard ────────────────────────────────────────────────────────

@squad_bp.route('/squad')
@login_required
def squad_landing():
    """iPhone home-screen shortcut entry point — redirects to the Squad dashboard."""
    return redirect(url_for('squad.dashboard'))


@squad_bp.route('/squad/')
@squad_bp.route('/squad/dashboard')
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
            .eq('is_cancelled', False) \
            .gte('event_date', today) \
            .order('event_date') \
            .order('event_time') \
            .limit(10) \
            .execute()
        upcoming_events = ev.data or []

    # Annotate events with day of week
    for ev in upcoming_events:
        try:
            d = date.fromisoformat(str(ev['event_date'])[:10])
            ev['day_name'] = d.strftime('%A')   # Monday, Tuesday…
            ev['day_short'] = d.strftime('%a')  # Mon, Tue…
        except Exception:
            ev['day_name'] = ''
            ev['day_short'] = ''

    # Past events (most recent first, last 20)
    past_events = []
    if squad_id:
        pe = _db().table('squad_events') \
            .select('*') \
            .eq('squad_id', squad_id) \
            .eq('is_cancelled', False) \
            .lt('event_date', today) \
            .order('event_date', desc=True) \
            .order('event_time', desc=True) \
            .limit(20) \
            .execute()
        past_events = pe.data or []
    for ev in past_events:
        try:
            d = date.fromisoformat(str(ev['event_date'])[:10])
            ev['day_name'] = d.strftime('%A')
            ev['day_short'] = d.strftime('%a')
        except Exception:
            ev['day_name'] = ''
            ev['day_short'] = ''

    # Poll stats for upcoming events (batch fetch — avoids N+1 queries)
    if squad_id and upcoming_events:
        event_ids = [ev['id'] for ev in upcoming_events]
        sep_result = _db().table('squad_event_players') \
            .select('event_id, player_id, status, squad_players(player_name, shirt_number)') \
            .in_('event_id', event_ids) \
            .execute()
        sep_by_event = defaultdict(lambda: {'yes': 0, 'no': 0, 'pending': 0, 'players': []})
        for sep in (sep_result.data or []):
            eid = sep['event_id']
            sep_by_event[eid][sep['status']] += 1
            pl = sep.get('squad_players') or {}
            sep_by_event[eid]['players'].append({
                'player_name':  pl.get('player_name', '?'),
                'shirt_number': pl.get('shirt_number'),
                'status':       sep['status'],
            })
        for ev in upcoming_events:
            ev['poll'] = sep_by_event.get(ev['id'], {'yes': 0, 'no': 0, 'pending': 0, 'players': []})
    else:
        for ev in upcoming_events:
            ev['poll'] = {'yes': 0, 'no': 0, 'pending': 0, 'players': []}

    # Fruit roster: resolve fruit_player_id → player_name for each event.
    # Pre-028 the column is absent and ev.get returns None — no-op.
    fruit_ids = [ev.get('fruit_player_id') for ev in upcoming_events if ev.get('fruit_player_id')]
    fruit_ids += [ev.get('fruit_player_id') for ev in past_events if ev.get('fruit_player_id')]
    fruit_name_by_id = {}
    if fruit_ids:
        fp = _db().table('squad_players').select('id, player_name')\
            .in_('id', list(set(fruit_ids))).execute()
        fruit_name_by_id = {p['id']: p['player_name'] for p in (fp.data or [])}
    for ev in upcoming_events + past_events:
        fpid = ev.get('fruit_player_id')
        ev['fruit_player_name'] = fruit_name_by_id.get(fpid) if fpid else None

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

    # Live iCal subscription URL — auto-generate token if missing
    webcal_url = None
    ical_https_url = None
    if squad:
        if not squad.get('cal_token'):
            import secrets
            token = secrets.token_hex(16)
            _db().table('squads').update({'cal_token': token}).eq('id', squad['id']).execute()
            squad['cal_token'] = token
        base = os.environ.get('APP_URL', 'https://www.jottask.app').rstrip('/')
        ical_https_url = f"{base}/squad/cal/{squad['cal_token']}.ics"
        webcal_url = ical_https_url.replace('https://', 'webcal://')

    return render_template('squad/dashboard.html',
        squad=squad,
        upcoming_events=upcoming_events,
        past_events=past_events,
        pending_count=pending_count,
        recent_inbox=recent_inbox,
        players=players,
        now=now,
        webcal_url=webcal_url,
        ical_https_url=ical_https_url,
    )


# QLD school holiday periods — used to skip training generation
# Format: (start_date, end_date) inclusive
QLD_SCHOOL_HOLIDAYS_2026 = [
    (date(2026, 3, 28), date(2026, 4, 12)),   # Easter / Autumn break
    (date(2026, 6, 27), date(2026, 7, 12)),   # Winter break
    (date(2026, 9, 19), date(2026, 10, 4)),   # Spring break
    (date(2026, 12, 5), date(2027, 1, 26)),   # Summer break
]

def _in_school_holidays(d: date, extra_holidays: list = None) -> bool:
    periods = QLD_SCHOOL_HOLIDAYS_2026 + (extra_holidays or [])
    return any(start <= d <= end for start, end in periods)


@squad_bp.route('/squad/events/generate-training', methods=['POST'])
@login_required
def generate_training():
    """Generate recurring weekly training sessions, skipping school holidays."""
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return redirect(url_for('squad.dashboard'))

    squad_id     = squad['id']
    weekday      = int(request.form.get('weekday', 2))     # 0=Mon…6=Sun, default Wed
    time_str     = request.form.get('time', '18:00').strip()
    venue        = request.form.get('venue', '').strip() or None
    start_str    = request.form.get('start_date', '').strip()
    end_str      = request.form.get('end_date', '').strip()
    skip_holidays = request.form.get('skip_holidays', 'true') == 'true'

    try:
        start = date.fromisoformat(start_str)
        end   = date.fromisoformat(end_str)
    except (ValueError, TypeError):
        return redirect(url_for('squad.dashboard'))

    if end < start or (end - start).days > 365:
        return redirect(url_for('squad.dashboard'))

    # Advance to first matching weekday
    current = start
    while current.weekday() != weekday:
        current += timedelta(days=1)

    now_iso = datetime.now(pytz.UTC).isoformat()
    created = 0

    while current <= end:
        if not (skip_holidays and _in_school_holidays(current)):
            # Check if this event already exists (avoid duplicates)
            existing = _db().table('squad_events') \
                .select('id') \
                .eq('squad_id', squad_id) \
                .eq('event_date', current.isoformat()) \
                .eq('event_type', 'training') \
                .execute()
            if not existing.data:
                _db().table('squad_events').insert({
                    'id':         str(uuid.uuid4()),
                    'squad_id':   squad_id,
                    'event_date': current.isoformat(),
                    'event_time': time_str + ':00' if len(time_str) == 5 else time_str,
                    'event_type': 'training',
                    'venue':      venue,
                    'is_cancelled': False,
                    'created_at': now_iso,
                }).execute()
                created += 1

        current += timedelta(days=7)

    return redirect(url_for('squad.dashboard'))


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

    # Use edited fixtures from request body if provided, else fall back to parsed data
    req_json = request.get_json(silent=True) or {}
    fixtures = req_json.get('fixtures') if 'fixtures' in req_json else parsed.get('fixtures', [])
    for fixture in fixtures:
        if not fixture.get('date'):
            continue
        try:
            event_type = fixture.get('type', 'game')
            payload = {
                'id':              str(uuid.uuid4()),
                'squad_id':        squad_id,
                'event_date':      fixture['date'],
                'event_time':      fixture.get('time'),
                'opponent':        fixture.get('opponent'),
                'venue':           fixture.get('venue'),
                'is_home':         fixture.get('is_home'),
                'event_type':      event_type,
                'notes':           fixture.get('notes'),
                'round':           fixture.get('round'),
                'source_inbox_id': item_id,
                'created_at':      datetime.now(pytz.UTC).isoformat(),
            }
            # Auto-assign fruit duty for games (not training/other)
            if event_type == 'game':
                payload['fruit_player_id'] = _pick_next_fruit_player(squad_id)
            _safe_insert_event(payload)
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
    squad_id = squad['id'] if squad else None

    fixtures = parsed.get('fixtures', [])
    cancellations = parsed.get('cancellations', [])

    # ── Fast path: auto-save when the paste is simple (1–3 events, no
    #    cancellations, every fixture has a date). No inbox step needed.
    simple = (
        1 <= len(fixtures) <= 3
        and not cancellations
        and all(f.get('date') for f in fixtures)
    )

    if simple and squad_id:
        saved = []
        for fixture in fixtures:
            event_type = fixture.get('type', 'game')
            payload = {
                'id':         str(uuid.uuid4()),
                'squad_id':   squad_id,
                'event_date': fixture['date'],
                'event_time': fixture.get('time'),
                'opponent':   fixture.get('opponent'),
                'venue':      fixture.get('venue'),
                'is_home':    fixture.get('is_home'),
                'event_type': event_type,
                'notes':      fixture.get('notes'),
                'round':      fixture.get('round'),
                'created_at': datetime.now(pytz.UTC).isoformat(),
            }
            if event_type == 'game':
                payload['fruit_player_id'] = _pick_next_fruit_player(squad_id)
            _safe_insert_event(payload)
            saved.append(fixture['date'])
        # Still log to inbox (status=approved) so there's an audit trail
        _db().table('squad_email_inbox').insert({
            'id':            str(uuid.uuid4()),
            'squad_id':      squad_id,
            'email_from':    f'pasted:{source}',
            'email_subject': f'Pasted {source} — {datetime.now(AEST).strftime("%d %b %Y %H:%M")}',
            'email_body':    text[:10000],
            'email_date':    datetime.now(pytz.UTC).isoformat(),
            'email_hash':    str(uuid.uuid4()),
            'email_type':    parsed.get('email_type', 'club_update'),
            'parsed_data':   parsed,
            'status':        'approved',
            'created_at':    datetime.now(pytz.UTC).isoformat(),
        }).execute()
        return redirect(url_for('squad.dashboard'))

    # ── Complex paste (many fixtures, cancellations, or missing dates) ──
    # Send to inbox for manual review before committing.
    _db().table('squad_email_inbox').insert({
        'id':            str(uuid.uuid4()),
        'squad_id':      squad_id,
        'email_from':    f'pasted:{source}',
        'email_subject': f'Pasted {source} — {datetime.now(AEST).strftime("%d %b %Y %H:%M")}',
        'email_body':    text[:10000],
        'email_date':    datetime.now(pytz.UTC).isoformat(),
        'email_hash':    str(uuid.uuid4()),
        'email_type':    parsed.get('email_type', 'club_update'),
        'parsed_data':   parsed,
        'status':        'pending',
        'created_at':    datetime.now(pytz.UTC).isoformat(),
    }).execute()

    return redirect(url_for('squad.inbox'))


# ── Events ────────────────────────────────────────────────────────────────────

@squad_bp.route('/squad/events/<event_id>/update', methods=['POST'])
@login_required
def update_event(event_id):
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 403

    # Verify event belongs to this squad
    check = _db().table('squad_events').select('id, squad_id') \
        .eq('id', event_id).maybe_single().execute()
    if not check.data or check.data['squad_id'] != squad['id']:
        return jsonify({'error': 'Not found'}), 404

    data = request.get_json(silent=True) or {}
    update = {}
    for field in ('event_date', 'event_time', 'opponent', 'venue', 'is_home',
                  'event_type', 'notes', 'round', 'fruit_player_id'):
        if field in data:
            update[field] = data[field] or None

    if not update:
        return jsonify({'error': 'Nothing to update'}), 400

    try:
        _db().table('squad_events').update(update).eq('id', event_id).execute()
    except Exception as e:
        # Pre-028 retry without fruit_player_id rather than 500
        if 'fruit_player_id' in update and _is_missing_column_error(e):
            update.pop('fruit_player_id', None)
            if update:
                _db().table('squad_events').update(update).eq('id', event_id).execute()
        else:
            raise
    return jsonify({'ok': True})


@squad_bp.route('/squad/events/<event_id>/delete', methods=['POST'])
@login_required
def delete_event(event_id):
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return redirect(url_for('squad.dashboard'))
    # Verify event belongs to this squad before deleting
    result = _db().table('squad_events').select('id, squad_id').eq('id', event_id).maybe_single().execute()
    if result.data and result.data['squad_id'] == squad['id']:
        _db().table('squad_events').delete().eq('id', event_id).execute()
    return redirect(url_for('squad.dashboard'))


# ── Manager: Team Roster ──────────────────────────────────────────────────────

@squad_bp.route('/squad/rename', methods=['POST'])
@login_required
def rename_squad():
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 404
    name = request.get_json(silent=True, force=True).get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    _db().table('squads').update({'name': name}).eq('id', squad['id']).execute()
    return jsonify({'ok': True, 'name': name})


@squad_bp.route('/squad/team')
@login_required
def team():
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    squad_id = squad['id'] if squad else None

    players = []
    parents = []
    if squad_id:
        pl = _db().table('squad_players') \
            .select('*') \
            .eq('squad_id', squad_id) \
            .order('player_name') \
            .execute()
        players = pl.data or []

        pa = _db().table('squad_parent_links') \
            .select('*, squad_players(player_name)') \
            .eq('squad_id', squad_id) \
            .eq('is_active', True) \
            .order('parent_name') \
            .execute()
        parents = pa.data or []

    app_url = os.getenv('APP_URL', 'https://www.jottask.app')
    return render_template('squad/team.html',
        squad=squad, players=players, parents=parents, app_url=app_url)


@squad_bp.route('/squad/team/players/add', methods=['POST'])
@login_required
def add_player():
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad found'}), 404

    player_name  = request.form.get('player_name', '').strip()
    shirt_number = request.form.get('shirt_number', '').strip()
    position     = request.form.get('position', '').strip()

    if not player_name:
        return redirect(url_for('squad.team'))

    _db().table('squad_players').insert({
        'id':           str(uuid.uuid4()),
        'squad_id':     squad['id'],
        'player_name':  player_name,
        'shirt_number': int(shirt_number) if shirt_number.isdigit() else None,
        'position':     position or None,
        'created_at':   datetime.now(pytz.UTC).isoformat(),
    }).execute()

    return redirect(url_for('squad.team'))


@squad_bp.route('/squad/team/players/<player_id>/remove', methods=['POST'])
@login_required
def remove_player(player_id):
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 404

    # Only delete if player belongs to this manager's squad
    _db().table('squad_players') \
        .delete() \
        .eq('id', player_id) \
        .eq('squad_id', squad['id']) \
        .execute()

    return redirect(url_for('squad.team'))


@squad_bp.route('/squad/team/parents/add', methods=['POST'])
@login_required
def add_parent():
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 404

    parent_name  = request.form.get('parent_name', '').strip()
    parent_email = request.form.get('parent_email', '').strip()
    parent_phone = request.form.get('parent_phone', '').strip()
    player_id    = request.form.get('player_id', '').strip() or None

    if not parent_name:
        return redirect(url_for('squad.team'))

    _db().table('squad_parent_links').insert({
        'id':           str(uuid.uuid4()),
        'squad_id':     squad['id'],
        'player_id':    player_id or None,
        'parent_name':  parent_name,
        'parent_email': parent_email or None,
        'parent_phone': parent_phone or None,
        'magic_token':  secrets.token_hex(24),
        'is_active':    True,
        'created_at':   datetime.now(pytz.UTC).isoformat(),
    }).execute()

    return redirect(url_for('squad.team'))


@squad_bp.route('/squad/team/parents/<parent_id>/send-link', methods=['POST'])
@login_required
def send_parent_link(parent_id):
    """Email the magic link to the parent."""
    from email_utils import send_email

    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 404

    pa = _db().table('squad_parent_links') \
        .select('*, squad_players(player_name)') \
        .eq('id', parent_id) \
        .eq('squad_id', squad['id']) \
        .maybe_single() \
        .execute()

    if not pa.data:
        return jsonify({'error': 'Not found'}), 404

    link     = pa.data
    email    = link.get('parent_email', '').strip()
    if not email:
        return jsonify({'error': 'No email address for this parent'}), 400

    token       = link.get('magic_token')
    parent_name = link.get('parent_name', 'there')
    player      = link.get('squad_players') or {}
    player_name = player.get('player_name', 'your player')
    squad_name  = squad.get('name', 'the team')
    app_url     = os.getenv('APP_URL', 'https://www.jottask.app')
    magic_url   = f"{app_url}/p/{token}"
    webcal_url  = magic_url.replace('https://', 'webcal://').replace('http://', 'webcal://')

    html = f"""
    <html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
                        max-width:560px;margin:0 auto;padding:20px;background:#f0fdf4;">
      <div style="background:linear-gradient(135deg,#15803d,#14532d);padding:28px 32px;border-radius:14px 14px 0 0;">
        <h1 style="color:white;margin:0;font-size:24px;">⚽ {squad_name}</h1>
        <p style="color:rgba(255,255,255,.85);margin:6px 0 0;font-size:15px;">Season schedule &amp; updates</p>
      </div>
      <div style="background:white;padding:28px 32px;border-radius:0 0 14px 14px;box-shadow:0 4px 8px rgba(0,0,0,.06);">
        <p style="font-size:16px;color:#1a2e1a;">Hi {parent_name},</p>
        <p style="font-size:14px;color:#374151;margin-top:10px;line-height:1.6;">
          Here's your personal link to view {player_name}'s upcoming fixtures, RSVP to games,
          and subscribe to the team calendar.
        </p>
        <div style="text-align:center;margin:28px 0;">
          <a href="{magic_url}" style="display:inline-block;background:#15803d;color:white;
             padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:16px;">
            View {player_name}'s Schedule
          </a>
        </div>
        <p style="font-size:13px;color:#6b7280;text-align:center;">
          You can also subscribe to the calendar directly:<br>
          <a href="{webcal_url}" style="color:#15803d;">Add to Apple / Google Calendar</a>
        </p>
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
        <p style="font-size:12px;color:#9ca3af;text-align:center;">
          This link is personal to you — no password needed.<br>
          Powered by <a href="https://www.jottask.app" style="color:#6b7280;">Jottask Squad</a>
        </p>
      </div>
    </body></html>
    """

    success, error = send_email(
        email,
        f"{squad_name} — {player_name}'s schedule link",
        html,
        category='squad_parent_link'
    )

    if success:
        return jsonify({'ok': True})
    else:
        return jsonify({'error': error or 'Send failed'}), 500


@squad_bp.route('/squad/team/parents/<parent_id>/remove', methods=['POST'])
@login_required
def remove_parent(parent_id):
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 404

    _db().table('squad_parent_links') \
        .update({'is_active': False}) \
        .eq('id', parent_id) \
        .eq('squad_id', squad['id']) \
        .execute()

    return redirect(url_for('squad.team'))


@squad_bp.route('/squad/team/players/<player_id>/update', methods=['POST'])
@login_required
def update_player(player_id):
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 404

    data = request.get_json(silent=True) or {}
    shirt_raw = str(data.get('shirt_number', '')).strip()
    updates = {
        'player_name':  data.get('player_name', '').strip() or None,
        'shirt_number': int(shirt_raw) if shirt_raw.isdigit() else None,
        'position':     data.get('position', '').strip() or None,
    }
    if not updates['player_name']:
        return jsonify({'error': 'Name required'}), 400

    _db().table('squad_players') \
        .update(updates) \
        .eq('id', player_id) \
        .eq('squad_id', squad['id']) \
        .execute()

    return jsonify({'ok': True})


@squad_bp.route('/squad/team/parents/<parent_id>/update', methods=['POST'])
@login_required
def update_parent(parent_id):
    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 404

    data = request.get_json(silent=True) or {}
    player_id = data.get('player_id', '').strip() or None
    updates = {
        'parent_name':  data.get('parent_name', '').strip() or None,
        'parent_email': data.get('parent_email', '').strip() or None,
        'parent_phone': data.get('parent_phone', '').strip() or None,
        'player_id':    player_id,
    }
    if not updates['parent_name']:
        return jsonify({'error': 'Name required'}), 400

    _db().table('squad_parent_links') \
        .update(updates) \
        .eq('id', parent_id) \
        .eq('squad_id', squad['id']) \
        .execute()

    return jsonify({'ok': True})


@squad_bp.route('/squad/team/upload', methods=['POST'])
@login_required
def upload_team_sheet():
    """Parse an uploaded team sheet (image or text/CSV) with Claude."""
    import base64
    from anthropic import Anthropic
    from squad_prompts import parse_team_sheet

    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 404

    file = request.files.get('team_sheet')
    if not file or not file.filename:
        return redirect(url_for('squad.team'))

    anthropic = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    filename  = file.filename.lower()
    content   = file.read()

    # Images → Claude vision; text/csv → plain text
    if any(filename.endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp')):
        b64 = base64.standard_b64encode(content).decode('utf-8')
        ext_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                   '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}
        media_type = next((v for k, v in ext_map.items() if filename.endswith(k)), 'image/png')
        parsed = parse_team_sheet(anthropic, image_b64=b64, media_type=media_type)
    else:
        text = content.decode('utf-8', errors='replace')
        parsed = parse_team_sheet(anthropic, text=text)

    squad_id  = squad['id']
    now_iso   = datetime.now(pytz.UTC).isoformat()
    added_players = 0
    added_parents = 0

    for p in parsed.get('players', []):
        name = (p.get('player_name') or '').strip()
        if not name:
            continue
        shirt = p.get('shirt_number')
        _db().table('squad_players').insert({
            'id':           str(uuid.uuid4()),
            'squad_id':     squad_id,
            'player_name':  name,
            'shirt_number': int(shirt) if shirt and str(shirt).isdigit() else None,
            'position':     p.get('position') or None,
            'created_at':   now_iso,
        }).execute()
        added_players += 1

    # Look up player IDs by name for parent linking
    pl_result = _db().table('squad_players').select('id, player_name').eq('squad_id', squad_id).execute()
    player_map = {r['player_name'].lower(): r['id'] for r in (pl_result.data or [])}

    for pa in parsed.get('parents', []):
        pname = (pa.get('parent_name') or '').strip()
        if not pname:
            continue
        linked_player = (pa.get('player_name') or '').strip().lower()
        player_id = player_map.get(linked_player)
        _db().table('squad_parent_links').insert({
            'id':           str(uuid.uuid4()),
            'squad_id':     squad_id,
            'player_id':    player_id,
            'parent_name':  pname,
            'parent_email': pa.get('parent_email') or None,
            'magic_token':  secrets.token_hex(24),
            'is_active':    True,
            'created_at':   now_iso,
        }).execute()
        added_parents += 1

    return redirect(url_for('squad.team'))


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
    """Live iCal feed — subscribable in Apple/Google/Outlook Calendar.

    Public endpoint (no login required). Uses _admin_db() which prefers
    SUPABASE_SERVICE_KEY so RLS doesn't block the unauthenticated lookup.
    Migration 026 also adds public SELECT policies as a belt-and-braces fix.
    """
    import logging
    try:
        squad_result = _admin_db().table('squads') \
            .select('*') \
            .eq('cal_token', token) \
            .maybe_single() \
            .execute()
    except Exception as e:
        logging.error(f'[squad_cal] squads lookup error for token {token!r}: {e}')
        return 'Calendar unavailable', 500

    if not squad_result.data:
        logging.warning(f'[squad_cal] no squad found for cal_token {token!r}')
        return 'Calendar not found', 404

    squad    = squad_result.data
    squad_id = squad['id']

    try:
        events_result = _admin_db().table('squad_events') \
            .select('*') \
            .eq('squad_id', squad_id) \
            .eq('is_cancelled', False) \
            .order('event_date') \
            .execute()
        events = events_result.data or []
    except Exception as e:
        logging.error(f'[squad_cal] events lookup error for squad {squad_id}: {e}')
        events = []

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


# ── Availability Poll ─────────────────────────────────────────────────────────

@squad_bp.route('/squad/events/<event_id>/poll', methods=['POST'])
@login_required
def poll_event(event_id):
    """Send availability poll emails to all parents for every player in the squad.

    One poll_token per player — shared by all parents linked to that player.
    Players who have already responded (yes/no) are skipped.
    """
    from email_utils import send_email

    user_id = session.get('user_id')
    squad = _get_squad_for_user(user_id)
    if not squad:
        return jsonify({'error': 'No squad'}), 404

    # Verify event belongs to this squad
    ev_result = _db().table('squad_events').select('*') \
        .eq('id', event_id).eq('squad_id', squad['id']).maybe_single().execute()
    if not ev_result.data:
        return jsonify({'error': 'Event not found'}), 404

    event     = ev_result.data
    squad_id  = squad['id']
    squad_name = squad.get('name', 'Squad')
    app_url   = os.getenv('APP_URL', 'https://www.jottask.app')

    # All players in squad
    players_result = _db().table('squad_players') \
        .select('id, player_name, shirt_number') \
        .eq('squad_id', squad_id).execute()
    players = players_result.data or []
    if not players:
        return jsonify({'error': 'No players in squad'}), 400

    # All active parents with emails, keyed by player_id
    parents_result = _db().table('squad_parent_links') \
        .select('id, parent_name, parent_email, player_id') \
        .eq('squad_id', squad_id).eq('is_active', True).execute()
    parents_by_player = defaultdict(list)
    for pa in (parents_result.data or []):
        if pa.get('parent_email') and pa.get('player_id'):
            parents_by_player[pa['player_id']].append(pa)

    # Format event description
    try:
        ev_date    = date.fromisoformat(str(event['event_date'])[:10])
        ev_date_str = ev_date.strftime(f'%A, {ev_date.day} %B %Y')
    except Exception:
        ev_date_str = str(event.get('event_date', ''))

    ev_time_str = str(event['event_time'])[:5] if event.get('event_time') else ''

    if event.get('opponent'):
        event_title = ('vs ' if event.get('is_home') else '@ ') + event['opponent']
    elif event.get('event_type') == 'training':
        event_title = f"{squad_name} Training"
    else:
        event_title = (event.get('event_type') or 'Event').title()

    # Fruit duty: lookup the assigned player's first name (if any) so we can
    # add the P.S. line to that player's parents only.
    fruit_player_id = event.get('fruit_player_id')
    fruit_first_name = ''
    if fruit_player_id:
        for p in players:
            if p['id'] == fruit_player_id:
                fruit_first_name = (p['player_name'] or '').split()[0] if p.get('player_name') else ''
                break

    sent    = 0
    now_iso = datetime.now(pytz.UTC).isoformat()

    for player in players:
        player_id   = player['id']
        player_name = player['player_name']

        # Get or create squad_event_players row
        sep_result = _db().table('squad_event_players') \
            .select('id, status, poll_token') \
            .eq('event_id', event_id).eq('player_id', player_id) \
            .maybe_single().execute()

        if sep_result.data:
            sep = sep_result.data
            if sep['status'] in ('yes', 'no'):
                continue          # already responded — don't re-email
            poll_token = sep['poll_token']
        else:
            poll_token = secrets.token_hex(24)
            _db().table('squad_event_players').insert({
                'id':         str(uuid.uuid4()),
                'event_id':   event_id,
                'player_id':  player_id,
                'status':     'pending',
                'poll_token': poll_token,
                'created_at': now_iso,
            }).execute()

        player_parents = parents_by_player.get(player_id, [])
        if not player_parents:
            continue

        yes_url = f"{app_url}/squad/rsvp/{poll_token}/yes"
        no_url  = f"{app_url}/squad/rsvp/{poll_token}/no"

        # Only add the fruit-duty P.S. for the parents of the assigned player.
        is_fruit_player = (fruit_player_id == player_id)
        fruit_note = fruit_first_name if is_fruit_player else ''

        for pa in player_parents:
            html = _poll_email_html(
                squad_name, player_name, pa.get('parent_name', 'there'),
                event_title, ev_date_str, ev_time_str,
                event.get('venue') or '', yes_url, no_url,
                fruit_player_first_name=fruit_note,
            )
            ok, _ = send_email(
                pa['parent_email'],
                f"{squad_name} — Is {player_name} available? {ev_date_str}",
                html,
                category='squad_poll',
            )
            if ok:
                sent += 1

    return jsonify({'ok': True, 'sent': sent})


def _poll_email_html(squad_name, player_name, parent_name,
                     event_title, ev_date_str, ev_time_str, venue,
                     yes_url, no_url, fruit_player_first_name=''):
    time_venue = ''
    if ev_time_str:
        time_venue += ev_time_str
    if venue:
        time_venue += (' · ' if time_venue else '') + venue
    tv_line = (f"<div style='font-size:13px;color:#4b5563;margin-top:2px;'>{time_venue}</div>"
               if time_venue else '')
    fruit_ps = (
        f'<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;'
        f'padding:12px 16px;margin:14px 0;font-size:13px;color:#9a3412;line-height:1.5;">'
        f'<strong>P.S.</strong> {fruit_player_first_name} is on fruit duty this game — '
        f'please bring enough fruit for the whole team to share at half time! 🍊</div>'
        if fruit_player_first_name else ''
    )
    return f"""
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
                    max-width:560px;margin:0 auto;padding:20px;background:#f0fdf4;">
  <div style="background:linear-gradient(135deg,#15803d,#14532d);padding:24px 32px;
              border-radius:14px 14px 0 0;">
    <h1 style="color:white;margin:0;font-size:22px;">&#x26BD; {squad_name}</h1>
    <p style="color:rgba(255,255,255,.8);margin:6px 0 0;font-size:14px;">Availability check</p>
  </div>
  <div style="background:white;padding:28px 32px;border-radius:0 0 14px 14px;
              box-shadow:0 4px 8px rgba(0,0,0,.06);">
    <p style="font-size:16px;color:#1a2e1a;">Hi {parent_name},</p>
    <p style="font-size:14px;color:#374151;margin-top:10px;line-height:1.6;">
      Can <strong>{player_name}</strong> make this one?
    </p>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;
                padding:14px 18px;margin:20px 0;">
      <div style="font-size:16px;font-weight:700;color:#15803d;">{event_title}</div>
      <div style="font-size:13px;color:#374151;margin-top:4px;">{ev_date_str}</div>
      {tv_line}
    </div>
    {fruit_ps}
    <table width="100%" style="margin:24px 0;border-collapse:collapse;">
      <tr>
        <td style="padding-right:8px;">
          <a href="{yes_url}" style="display:block;background:#15803d;color:white;
             text-align:center;padding:16px;border-radius:10px;text-decoration:none;
             font-weight:700;font-size:16px;">&#x2705; Yes, we&#x2019;re in!</a>
        </td>
        <td style="padding-left:8px;">
          <a href="{no_url}" style="display:block;background:#fef2f2;color:#dc2626;
             text-align:center;padding:16px;border-radius:10px;text-decoration:none;
             font-weight:700;font-size:16px;border:1.5px solid #fecaca;">
            &#x274C; Can&#x2019;t make it</a>
        </td>
      </tr>
    </table>
    <p style="font-size:12px;color:#9ca3af;text-align:center;margin-top:8px;">
      Tap once — no login needed. First response counts.
    </p>
    <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
    <p style="font-size:12px;color:#9ca3af;text-align:center;">
      Powered by <a href="https://www.jottask.app" style="color:#6b7280;">Jottask Squad</a>
    </p>
  </div>
</body></html>"""


# ── One-click RSVP from email ─────────────────────────────────────────────────

@squad_bp.route('/squad/rsvp/<token>/<action>')
def quick_rsvp(token, action):
    """Handle a YES or NO tap from a poll email. No login required.

    First response wins — if already answered, show a friendly message.
    """
    if action not in ('yes', 'no'):
        return _rsvp_page('&#x2753; Invalid link', 'This link is not valid.', '#6b7280'), 400

    sep_result = _admin_db().table('squad_event_players') \
        .select('id, status, player_id, event_id') \
        .eq('poll_token', token).maybe_single().execute()

    if not sep_result.data:
        return _rsvp_page(
            '&#x2753; Link not found',
            'This availability link is no longer valid. Ask your coach for a new one.',
            '#6b7280',
        ), 404

    sep       = sep_result.data
    player_id = sep['player_id']

    # Fetch player name for friendly messages
    pl_result = _admin_db().table('squad_players') \
        .select('player_name').eq('id', player_id).maybe_single().execute()
    player_name = (pl_result.data or {}).get('player_name', 'your player')

    # Already responded?
    if sep['status'] != 'pending':
        prev = sep['status']
        icon = '&#x2705;' if prev == 'yes' else '&#x274C;'
        return _rsvp_page(
            f'{icon} Already recorded',
            f"We already have a <strong>{prev}</strong> for <strong>{player_name}</strong> — no action needed.",
            '#15803d' if prev == 'yes' else '#dc2626',
        )

    # Record response
    now_iso = datetime.now(pytz.UTC).isoformat()
    _admin_db().table('squad_event_players').update({
        'status':       action,
        'responded_at': now_iso,
    }).eq('id', sep['id']).execute()

    if action == 'yes':
        return _rsvp_page(
            '&#x2705; Thanks!',
            f"Got it — <strong>{player_name} is coming</strong>. See you there!",
            '#15803d',
        )
    else:
        return _rsvp_page(
            '&#x274C; Thanks for letting us know',
            f"No worries — we&#x2019;ll note that <strong>{player_name} can&#x2019;t make it</strong>.",
            '#dc2626',
        )


def _rsvp_page(title, body, color):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Squad — RSVP</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f0fdf4; display: flex; align-items: center; justify-content: center;
    min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box;
  }}
  .card {{
    background: white; border-radius: 16px; padding: 40px 32px;
    text-align: center; max-width: 420px; width: 100%;
    box-shadow: 0 4px 16px rgba(0,0,0,.10);
  }}
  .icon {{ font-size: 56px; margin-bottom: 16px; display: block; }}
  h1 {{ font-size: 22px; color: {color}; margin: 0 0 12px; line-height: 1.3; }}
  p  {{ font-size: 15px; color: #374151; line-height: 1.6; margin: 0; }}
</style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <p>{body}</p>
  </div>
</body>
</html>"""
