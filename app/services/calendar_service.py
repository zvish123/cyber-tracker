import json
from datetime import timedelta
from flask import current_app
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request, AuthorizedSession
import requests as _requests

SCOPES = ['https://www.googleapis.com/auth/calendar.events']
CALENDAR_API = 'https://www.googleapis.com/calendar/v3'


def _get_session(user):
    """Return an authorized requests session, refreshing token if needed."""
    if not user.google_credentials:
        return None

    creds_data = json.loads(user.google_credentials)
    creds = Credentials(
        token=creds_data.get('token'),
        refresh_token=creds_data.get('refresh_token'),
        token_uri=creds_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=current_app.config['GOOGLE_CLIENT_ID'],
        client_secret=current_app.config['GOOGLE_CLIENT_SECRET'],
        scopes=SCOPES,
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request(_requests.Session()))
            from ..extensions import db
            user.google_credentials = json.dumps({
                'token':         creds.token,
                'refresh_token': creds.refresh_token,
                'token_uri':     creds.token_uri,
                'scopes':        list(creds.scopes) if creds.scopes else SCOPES,
            })
            db.session.commit()
        except Exception as exc:
            current_app.logger.error(f'Google token refresh failed: {exc}')
            return None

    return AuthorizedSession(creds)


def create_event(user, meeting, student_email):
    """Create a Google Calendar event. Returns (event_id, calendar_id) or (None, None)."""
    session = _get_session(user)
    if not session:
        return None, None

    start = meeting.scheduled_at
    end   = start + timedelta(minutes=meeting.duration_minutes)

    body = {
        'summary':     meeting.title,
        'description': f'פגישת מעקב עם {meeting.student.user.full_name}',
        'start': {'dateTime': start.isoformat(), 'timeZone': 'Asia/Jerusalem'},
        'end':   {'dateTime': end.isoformat(),   'timeZone': 'Asia/Jerusalem'},
        'attendees': [{'email': student_email}],
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'email',  'minutes': 24 * 60},
                {'method': 'popup',  'minutes': 30},
            ],
        },
    }

    if meeting.is_recurring and meeting.recurrence_type != 'none':
        rrule = _build_rrule(meeting)
        if rrule:
            body['recurrence'] = [rrule]

    try:
        resp = session.post(
            f'{CALENDAR_API}/calendars/primary/events',
            params={'sendUpdates': 'all'},
            json=body,
        )
        resp.raise_for_status()
        event = resp.json()
        return event['id'], 'primary'
    except Exception as exc:
        current_app.logger.error(f'Calendar create_event failed: {exc}')
        return None, None


def update_event(user, meeting, student_email):
    """Update an existing Google Calendar event after rescheduling."""
    session = _get_session(user)
    if not session or not meeting.google_event_id:
        return False

    start  = meeting.scheduled_at
    end    = start + timedelta(minutes=meeting.duration_minutes)
    cal_id = meeting.google_calendar_id or 'primary'

    try:
        # Fetch existing event
        resp = session.get(f'{CALENDAR_API}/calendars/{cal_id}/events/{meeting.google_event_id}')
        resp.raise_for_status()
        event = resp.json()

        event['summary'] = meeting.title
        event['start']   = {'dateTime': start.isoformat(), 'timeZone': 'Asia/Jerusalem'}
        event['end']     = {'dateTime': end.isoformat(),   'timeZone': 'Asia/Jerusalem'}

        resp = session.put(
            f'{CALENDAR_API}/calendars/{cal_id}/events/{meeting.google_event_id}',
            params={'sendUpdates': 'all'},
            json=event,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        current_app.logger.error(f'Calendar update_event failed: {exc}')
        return False


def delete_event(user, meeting):
    """Delete a Google Calendar event when a meeting is cancelled."""
    session = _get_session(user)
    if not session or not meeting.google_event_id:
        return False

    cal_id = meeting.google_calendar_id or 'primary'
    try:
        resp = session.delete(
            f'{CALENDAR_API}/calendars/{cal_id}/events/{meeting.google_event_id}',
            params={'sendUpdates': 'all'},
        )
        if resp.status_code == 410:   # already deleted — treat as success
            return True
        resp.raise_for_status()
        return True
    except Exception as exc:
        current_app.logger.error(f'Calendar delete_event failed: {exc}')
        return False


def _build_rrule(meeting):
    freq_map = {
        'weekly':   'RRULE:FREQ=WEEKLY',
        'biweekly': 'RRULE:FREQ=WEEKLY;INTERVAL=2',
        'monthly':  'RRULE:FREQ=MONTHLY',
    }
    rrule = freq_map.get(meeting.recurrence_type)
    if not rrule:
        return None
    if meeting.recurrence_end_date:
        until = meeting.recurrence_end_date.strftime('%Y%m%dT%H%M%SZ')
        rrule += f';UNTIL={until}'
    return rrule
