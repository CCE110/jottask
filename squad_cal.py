"""
Squad Calendar
Generates .ics calendar feeds from squad_events using the icalendar library.

Usage:
    from squad_cal import generate_ical
    ical_bytes = generate_ical(squad_row, events_list)
"""

import uuid as uuid_module
from datetime import date, datetime, timedelta

import pytz
from icalendar import Calendar, Event, vText

AEST = pytz.timezone('Australia/Brisbane')


def generate_ical(squad: dict, events: list) -> bytes:
    """
    Build an iCal (.ics) bytes object for the squad calendar.

    Args:
        squad:  Supabase `squads` row dict (must have at least 'name')
        events: List of Supabase `squad_events` row dicts

    Returns:
        UTF-8 encoded iCal content as bytes
    """
    squad_name = squad.get('name', 'Squad')

    cal = Calendar()
    cal.add('prodid', f'-//Jottask Squad//{squad_name}//EN')
    cal.add('version', '2.0')
    cal.add('calscale', 'GREGORIAN')
    cal.add('method', 'PUBLISH')
    cal.add('x-wr-calname', vText(f'{squad_name} Fixtures'))
    cal.add('x-wr-timezone', 'Australia/Brisbane')
    cal.add('x-wr-caldesc', vText(f'Fixtures and training schedule for {squad_name}'))
    cal.add('refresh-interval;value=duration', 'PT1H')  # Hint clients to refresh hourly

    now_utc = datetime.now(pytz.UTC)

    for row in events:
        event_date_str = row.get('event_date')
        if not event_date_str:
            continue

        try:
            event_date = date.fromisoformat(str(event_date_str)[:10])
        except (ValueError, TypeError):
            continue

        event = Event()

        # ── Summary (title) ──────────────────────────────────────────────
        opponent   = row.get('opponent')
        event_type = row.get('event_type', 'game')
        is_home    = row.get('is_home')

        if event_type == 'training':
            summary = f'{squad_name} Training'
        elif opponent:
            if is_home is True:
                summary = f'{squad_name} vs {opponent} (H)'
            elif is_home is False:
                summary = f'{squad_name} @ {opponent} (A)'
            else:
                summary = f'{squad_name} vs {opponent}'
        else:
            summary = f'{squad_name} {event_type.replace("_", " ").title()}'

        event.add('summary', summary)

        # ── Date / time ──────────────────────────────────────────────────
        time_str = row.get('event_time')
        if time_str:
            try:
                parts  = str(time_str).split(':')
                hour   = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
                dt_start = AEST.localize(datetime(
                    event_date.year, event_date.month, event_date.day,
                    hour, minute, 0
                ))
                duration = timedelta(hours=2 if event_type in ('game', 'cup', 'friendly') else 1.5)
                dt_end = dt_start + duration
                event.add('dtstart', dt_start)
                event.add('dtend', dt_end)
            except (ValueError, IndexError):
                event.add('dtstart', event_date)
        else:
            event.add('dtstart', event_date)

        # ── Location ─────────────────────────────────────────────────────
        venue = row.get('venue')
        if venue:
            event.add('location', venue)

        # ── Description ──────────────────────────────────────────────────
        desc_parts = []
        if opponent:
            home_away = 'Home' if is_home is True else 'Away' if is_home is False else ''
            desc_parts.append(f'vs {opponent}' + (f' ({home_away})' if home_away else ''))
        if venue:
            desc_parts.append(f'Venue: {venue}')
        if row.get('round'):
            desc_parts.append(f'Round: {row["round"]}')
        if row.get('notes'):
            desc_parts.append(row['notes'])
        if desc_parts:
            event.add('description', '\n'.join(desc_parts))

        # ── UID & timestamps ──────────────────────────────────────────────
        event_id = row.get('id') or str(uuid_module.uuid4())
        event.add('uid', f'{event_id}@jottask.squad')

        created_str = row.get('created_at')
        if created_str:
            try:
                dtstamp = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                dtstamp = now_utc
        else:
            dtstamp = now_utc
        event.add('dtstamp', dtstamp)

        # Mark cancelled events
        if row.get('is_cancelled'):
            event.add('status', 'CANCELLED')

        cal.add_component(event)

    return cal.to_ical()
