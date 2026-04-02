from flask_socketio import join_room, leave_room
from flask_login import current_user
from ..extensions import socketio


# ── Connection lifecycle ───────────────────────────────────────────────────────

@socketio.on('connect')
def handle_connect():
    if not current_user.is_authenticated:
        return False   # reject unauthenticated connections

    if current_user.is_teacher:
        join_room('teachers')
    elif current_user.is_student and current_user.student_profile:
        join_room(f'student_{current_user.student_profile.id}')


@socketio.on('disconnect')
def handle_disconnect():
    pass   # Flask-SocketIO cleans up rooms automatically


@socketio.on('join')
def handle_join(data):
    """Explicit room join from client (called on page load)."""
    room = data.get('room', '')
    if not room or not current_user.is_authenticated:
        return

    if current_user.is_teacher:
        join_room(room)
    elif current_user.is_student and current_user.student_profile:
        allowed = f'student_{current_user.student_profile.id}'
        if room == allowed:
            join_room(room)


# ── Emit helpers (called by route handlers) ────────────────────────────────────

def emit_phase_updated(student_id, project_id, phase, percentage, overall_pct):
    data = {
        'student_id':          student_id,
        'project_id':          project_id,
        'phase':               phase,
        'percentage':          percentage,
        'overall_percentage':  overall_pct,
    }
    socketio.emit('phase_updated', data, room='teachers')
    socketio.emit('phase_updated', data, room=f'student_{student_id}')


def emit_meeting_event(student_id, event_type, meeting_data):
    data = {'type': event_type, 'meeting': meeting_data}
    socketio.emit('meeting_event', data, room='teachers')
    socketio.emit('meeting_event', data, room=f'student_{student_id}')
