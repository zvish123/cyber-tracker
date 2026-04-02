from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import pytz

_scheduler = None


def start_scheduler(app):
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    tz = pytz.timezone('Asia/Jerusalem')
    _scheduler = BackgroundScheduler(timezone=tz)
    _scheduler.add_job(
        func=lambda: _check_reminders(app),
        trigger=IntervalTrigger(minutes=30),
        id='meeting_reminder_check',
        replace_existing=True,
    )
    _scheduler.start()


def _check_reminders(app):
    """
    Find meetings starting in 20–28 hours and send reminder emails.
    The 20–28 hour window is idempotent — wide enough to survive a restart
    without sending duplicates if the scheduler fires twice in that window.
    """
    from datetime import datetime, timedelta
    from ..models import Meeting
    from ..services.email_service import send_meeting_reminder

    with app.app_context():
        now = datetime.utcnow()
        window_start = now + timedelta(hours=20)
        window_end   = now + timedelta(hours=28)

        upcoming = Meeting.query.filter(
            Meeting.scheduled_at >= window_start,
            Meeting.scheduled_at <= window_end,
            Meeting.status == 'scheduled',
        ).all()

        for meeting in upcoming:
            try:
                send_meeting_reminder(meeting.student.user, meeting)
            except Exception as exc:
                app.logger.error(f'Reminder send failed for meeting {meeting.id}: {exc}')
