from datetime import datetime
from functools import wraps

from flask import render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from . import student_bp
from ..models import Meeting, PHASE_DISPLAY, PHASE_ORDER


def student_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_student:
            return redirect(url_for('teacher.dashboard'))
        if not current_user.student_profile:
            flash('חשבון תלמיד לא מוגדר כראוי', 'danger')
            return redirect(url_for('auth.logout'))
        return f(*args, **kwargs)
    return decorated


@student_bp.route('/dashboard')
@student_required
def dashboard():
    student = current_user.student_profile
    project = student.project
    phases_dict = project.get_phases_dict() if project else {}
    return render_template(
        'student/dashboard.html',
        student=student,
        project=project,
        phases_dict=phases_dict,
        phase_display=PHASE_DISPLAY,
        phase_order=PHASE_ORDER,
    )


@student_bp.route('/meetings')
@student_required
def meetings():
    student = current_user.student_profile
    upcoming = (
        student.meetings
        .filter(Meeting.status == 'scheduled',
                Meeting.scheduled_at >= datetime.utcnow())
        .order_by(Meeting.scheduled_at)
        .all()
    )
    past = (
        student.meetings
        .filter(Meeting.scheduled_at < datetime.utcnow())
        .order_by(Meeting.scheduled_at.desc())
        .limit(10).all()
    )
    return render_template('student/meetings.html',
                           upcoming=upcoming, past=past, now=datetime.utcnow())
