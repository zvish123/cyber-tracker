from flask import current_app, render_template
from flask_mail import Message
from ..extensions import mail


def send_meeting_scheduled(student_user, meeting):
    _send(
        to=student_user.email,
        subject=f'פגישה נקבעה: {meeting.title}',
        template='emails/meeting_scheduled.html',
        student=student_user,
        meeting=meeting,
    )


def send_meeting_cancelled(student_user, meeting):
    _send(
        to=student_user.email,
        subject=f'פגישה בוטלה: {meeting.title}',
        template='emails/meeting_cancelled.html',
        student=student_user,
        meeting=meeting,
    )


def send_meeting_rescheduled(student_user, meeting, old_time):
    _send(
        to=student_user.email,
        subject=f'פגישה נדחתה: {meeting.title}',
        template='emails/meeting_rescheduled.html',
        student=student_user,
        meeting=meeting,
        old_time=old_time,
    )


def send_meeting_reminder(student_user, meeting):
    _send(
        to=student_user.email,
        subject=f'תזכורת: פגישה מחר — {meeting.title}',
        template='emails/meeting_reminder.html',
        student=student_user,
        meeting=meeting,
    )


def _send(to, subject, template, **kwargs):
    try:
        html_body = render_template(template, **kwargs)
        msg = Message(subject=subject, recipients=[to], html=html_body)
        mail.send(msg)
    except Exception as exc:
        # Never let email failure break the main request flow
        current_app.logger.error(f'Email send failed to {to}: {exc}')
