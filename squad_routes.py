"""
Squad Routes
Flask blueprint for Squad — AI-native youth soccer team management.

Blueprint prefix: none (routes use explicit /squad/ and /p/ prefixes)
Routes: /squad/, /squad/inbox, /squad/inbox/paste, /squad/team, /p/<token>
"""

import json
import os
import uuid
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
            _db().table('squad_events').insert({
                'id':              str(uuid.uuid4()),
                'squad_id':        squad_id,
                'event_date':      fixture['date'],
                'event_time':      fixture.get('time'),
                'opponent':        fixture.get('opponent'),
                'venue':           fixture.get('venue'),
                'is_home':         fixture.get('is_home'),
                'event_type':      fixture.get('type', 'game'),
                'notes':           fixture.get('notes'),
                'round':           fixture.get('round'),
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


# ── Events ────────────────────────────────────────────────────────────────────

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
