import json
import os
from datetime import datetime, timedelta
from functools import wraps

from flask import (render_template, redirect, url_for, flash, request,
                   session, current_app, send_file)
from flask_login import login_required, current_user

from sqlalchemy import or_

from . import teacher_bp
from ..extensions import db
import json as _json
from ..models import (User, Student, Project, PhaseProgress, Meeting, ClassGroup,
                      class_teachers, AppSettings, DEFAULT_PHASE_WEIGHTS, PHASE_ORDER, PHASE_DISPLAY)
from ..services.calendar_service import create_event, update_event, delete_event
from ..services.email_service import (
    send_meeting_scheduled, send_meeting_cancelled, send_meeting_rescheduled,
)
from ..sockets.events import emit_phase_updated, emit_meeting_event


# ── Decorator ─────────────────────────────────────────────────────────────────

def teacher_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_teacher:
            flash('גישה מותרת למורים בלבד', 'danger')
            return redirect(url_for('student.dashboard'))
        return f(*args, **kwargs)
    return decorated


# ── Helper ────────────────────────────────────────────────────────────────────

def _teacher_classes(teacher_id):
    """All classes where the user is primary teacher OR co-teacher."""
    co_ids = (db.session.query(class_teachers.c.class_group_id)
              .filter(class_teachers.c.user_id == teacher_id))
    return (ClassGroup.query
            .filter(or_(
                ClassGroup.teacher_id == teacher_id,
                ClassGroup.id.in_(co_ids)
            ))
            .order_by(ClassGroup.name)
            .all())


# ── Dashboard ─────────────────────────────────────────────────────────────────

@teacher_bp.route('/dashboard')
@teacher_required
def dashboard():
    class_id = request.args.get('class_id', type=int)
    classes  = _teacher_classes(current_user.id)
    q = Student.query.join(User, Student.user_id == User.id).order_by(User.full_name)
    if class_id:
        q = q.filter(Student.class_group_id == class_id)
    elif not current_user.is_admin:
        ids = [c.id for c in classes]
        q = q.filter(Student.class_group_id.in_(ids or [-1]))
    students = q.all()
    with_project    = [s for s in students if s.project]
    without_project = [s for s in students if not s.project]
    avg_progress    = (sum(s.project.get_overall_percentage() for s in with_project)
                       // len(with_project)) if with_project else 0
    return render_template('teacher/dashboard.html', students=students,
                           phase_display=PHASE_DISPLAY,
                           classes=classes, selected_class_id=class_id,
                           with_project_count=len(with_project),
                           without_project_count=len(without_project),
                           avg_progress=avg_progress)


# ── Student management ────────────────────────────────────────────────────────

@teacher_bp.route('/students')
@teacher_required
def students():
    class_id = request.args.get('class_id', type=int)
    classes  = _teacher_classes(current_user.id)
    q = Student.query.join(User, Student.user_id == User.id).order_by(User.full_name)
    if class_id:
        q = q.filter(Student.class_group_id == class_id)
    elif not current_user.is_admin:
        ids = [c.id for c in classes]
        q = q.filter(Student.class_group_id.in_(ids or [-1]))
    all_students = q.all()
    return render_template('teacher/students.html', students=all_students,
                           classes=classes, selected_class_id=class_id)


@teacher_bp.route('/students/add', methods=['POST'])
@teacher_required
def add_student():
    if not current_user.is_admin and not _teacher_classes(current_user.id):
        flash('יש ליצור כיתה לפני הוספת תלמידים', 'warning')
        return redirect(url_for('teacher.classes'))

    full_name      = request.form.get('full_name',      '').strip()
    username       = request.form.get('username',       '').strip()
    email          = request.form.get('email',          '').strip()
    password       = request.form.get('password',       '').strip()
    class_group_id = request.form.get('class_group_id', type=int)

    if not all([full_name, username, email, password]):
        flash('יש למלא את כל השדות החובה', 'danger')
        return redirect(url_for('teacher.students'))

    if User.query.filter_by(username=username).first():
        flash('שם משתמש כבר קיים במערכת', 'danger')
        return redirect(url_for('teacher.students'))

    if User.query.filter_by(email=email).first():
        flash('כתובת אימייל כבר קיימת במערכת', 'danger')
        return redirect(url_for('teacher.students'))

    user = User(username=username, full_name=full_name, email=email, role='student')
    user.set_password(password)
    db.session.add(user)
    db.session.flush()

    # Resolve class_name for backward compat
    class_name = ''
    if class_group_id:
        cg = ClassGroup.query.get(class_group_id)
        if cg:
            class_name = cg.name

    student = Student(user_id=user.id, class_name=class_name,
                      class_group_id=class_group_id,
                      teacher_id=current_user.id)
    db.session.add(student)
    db.session.commit()

    flash(f'התלמיד {full_name} נוסף בהצלחה', 'success')
    return redirect(url_for('teacher.students'))


@teacher_bp.route('/students/<int:student_id>/edit', methods=['POST'])
@teacher_required
def edit_student(student_id):
    student   = Student.query.get_or_404(student_id)
    user      = student.user

    full_name      = request.form.get('full_name',      '').strip()
    username       = request.form.get('username',       '').strip()
    email          = request.form.get('email',          '').strip()
    password       = request.form.get('password',       '').strip()
    class_group_id = request.form.get('class_group_id', type=int)

    if not all([full_name, username, email]):
        flash('יש למלא שם מלא, שם משתמש ואימייל', 'danger')
        return redirect(url_for('teacher.students'))

    # Check uniqueness (excluding this user)
    dup_username = User.query.filter(User.username == username, User.id != user.id).first()
    if dup_username:
        flash('שם משתמש כבר קיים במערכת', 'danger')
        return redirect(url_for('teacher.students'))

    dup_email = User.query.filter(User.email == email, User.id != user.id).first()
    if dup_email:
        flash('כתובת אימייל כבר קיימת במערכת', 'danger')
        return redirect(url_for('teacher.students'))

    user.full_name = full_name
    user.username  = username
    user.email     = email
    if password:
        user.set_password(password)

    # Update class group
    student.class_group_id = class_group_id
    if class_group_id:
        cg = ClassGroup.query.get(class_group_id)
        student.class_name = cg.name if cg else ''
    else:
        student.class_name = ''

    db.session.commit()
    flash(f'פרטי התלמיד {full_name} עודכנו', 'success')
    return redirect(url_for('teacher.students'))


@teacher_bp.route('/students/<int:student_id>/delete', methods=['POST'])
@teacher_required
def delete_student(student_id):
    student = Student.query.get_or_404(student_id)
    name = student.user.full_name

    # Cancel Google Calendar events before deleting
    for meeting in student.meetings.filter_by(status='scheduled').all():
        if meeting.google_event_id:
            delete_event(current_user, meeting)

    db.session.delete(student.user)   # cascades to Student → Project → Meetings
    db.session.commit()
    flash(f'התלמיד {name} הוסר מהמערכת', 'success')
    return redirect(url_for('teacher.students'))


# ── Project management ────────────────────────────────────────────────────────

@teacher_bp.route('/students/<int:student_id>/project', methods=['GET', 'POST'])
@teacher_required
def student_project(student_id):
    student = Student.query.get_or_404(student_id)

    # Default weights for the form
    raw_defaults = AppSettings.get('default_phase_weights',
                                   _json.dumps(DEFAULT_PHASE_WEIGHTS))
    default_weights = _json.loads(raw_defaults)

    if request.method == 'POST':
        if student.project:
            flash('לתלמיד זה כבר קיים פרויקט', 'warning')
            return redirect(url_for('teacher.project_detail', project_id=student.project.id))

        title       = request.form.get('title',       '').strip()
        subject     = request.form.get('subject',     '').strip()
        description = request.form.get('description', '').strip()

        if not title or not subject:
            flash('יש למלא שם פרויקט ונושא', 'danger')
            return redirect(url_for('teacher.student_project', student_id=student_id))

        # Collect phase weights from form
        weights = {}
        for phase in PHASE_ORDER:
            w = request.form.get(f'weight_{phase}', type=int) or 0
            weights[phase] = max(0, w)
        if sum(weights.values()) != 100:
            flash('סכום המשקלות חייב להיות 100%', 'danger')
            return render_template('teacher/project_form.html', student=student,
                                   default_weights=weights,
                                   phase_order=PHASE_ORDER, phase_display=PHASE_DISPLAY)

        project = Project(student_id=student_id, title=title, subject=subject,
                          description=description,
                          phase_weights=_json.dumps(weights))
        db.session.add(project)
        db.session.flush()

        for phase in PHASE_ORDER:
            db.session.add(PhaseProgress(
                project_id=project.id, phase=phase, percentage=0,
                updated_by_id=current_user.id,
            ))

        db.session.commit()
        flash('הפרויקט נוצר בהצלחה', 'success')
        return redirect(url_for('teacher.project_detail', project_id=project.id))

    return render_template('teacher/project_form.html', student=student,
                           default_weights=default_weights,
                           phase_order=PHASE_ORDER, phase_display=PHASE_DISPLAY)


@teacher_bp.route('/projects/<int:project_id>')
@teacher_required
def project_detail(project_id):
    project = Project.query.get_or_404(project_id)
    phases_dict = project.get_phases_dict()
    upcoming_meetings = (
        project.meetings
        .filter(Meeting.status == 'scheduled', Meeting.scheduled_at >= datetime.utcnow())
        .order_by(Meeting.scheduled_at)
        .limit(5).all()
    )
    return render_template(
        'teacher/project.html',
        project=project,
        phases_dict=phases_dict,
        phase_display=PHASE_DISPLAY,
        phase_order=PHASE_ORDER,
        upcoming_meetings=upcoming_meetings,
    )


@teacher_bp.route('/projects/<int:project_id>/edit', methods=['POST'])
@teacher_required
def edit_project(project_id):
    project = Project.query.get_or_404(project_id)
    title       = request.form.get('title',       '').strip()
    subject     = request.form.get('subject',     '').strip()
    description = request.form.get('description', '').strip()

    if not title or not subject:
        flash('יש למלא שם פרויקט ונושא', 'danger')
        return redirect(url_for('teacher.project_detail', project_id=project_id))

    project.title       = title
    project.subject     = subject
    project.description = description
    project.updated_at  = datetime.utcnow()

    # Update weights if submitted
    weights = {}
    for phase in PHASE_ORDER:
        w = request.form.get(f'weight_{phase}', type=int)
        if w is not None:
            weights[phase] = max(0, w)
    if weights and sum(weights.values()) == 100:
        project.phase_weights = _json.dumps(weights)
    elif weights:
        flash('המשקלות לא עודכנו — הסכום חייב להיות 100%', 'warning')

    db.session.commit()

    flash('פרטי הפרויקט עודכנו', 'success')
    return redirect(url_for('teacher.project_detail', project_id=project_id))


@teacher_bp.route('/projects/<int:project_id>/delete', methods=['POST'])
@teacher_required
def delete_project(project_id):
    project = Project.query.get_or_404(project_id)
    student_id = project.student_id
    db.session.delete(project)
    db.session.commit()
    flash('הפרויקט נמחק', 'success')
    return redirect(url_for('teacher.student_project', student_id=student_id))


@teacher_bp.route('/projects/<int:project_id>/phase', methods=['POST'])
@teacher_required
def update_phase(project_id):
    project = Project.query.get_or_404(project_id)
    phase      = request.form.get('phase', '')
    percentage = int(request.form.get('percentage', 0))
    notes      = request.form.get('notes', '').strip()

    if phase not in PHASE_ORDER:
        flash('שלב לא תקין', 'danger')
        return redirect(url_for('teacher.project_detail', project_id=project_id))

    pp = PhaseProgress.query.filter_by(project_id=project_id, phase=phase).first_or_404()
    pp.percentage    = max(0, min(100, percentage))
    pp.notes         = notes
    pp.updated_by_id = current_user.id
    pp.updated_at    = datetime.utcnow()
    project.updated_at = datetime.utcnow()
    db.session.commit()

    emit_phase_updated(
        student_id=project.student_id,
        project_id=project_id,
        phase=phase,
        percentage=pp.percentage,
        overall_pct=project.get_overall_percentage(),
    )

    flash(f'שלב {PHASE_DISPLAY.get(phase, phase)} עודכן ל-{pp.percentage}%', 'success')
    return redirect(url_for('teacher.project_detail', project_id=project_id))


# ── Meetings ──────────────────────────────────────────────────────────────────

def _has_overlap(teacher_id, start, duration_minutes, exclude_meeting_id=None):
    """Return conflicting Meeting if the slot overlaps any scheduled meeting for this teacher."""
    end = start + timedelta(minutes=duration_minutes)
    q = (Meeting.query
         .filter(
             Meeting.teacher_id == teacher_id,
             Meeting.status == 'scheduled',
         ))
    if exclude_meeting_id:
        q = q.filter(Meeting.id != exclude_meeting_id)
    for m in q.all():
        m_end = m.scheduled_at + timedelta(minutes=m.duration_minutes)
        if start < m_end and end > m.scheduled_at:
            return m   # return the conflicting meeting
    return None

@teacher_bp.route('/meetings')
@teacher_required
def meetings():
    all_students = Student.query.join(User, Student.user_id == User.id).order_by(User.full_name).all()
    upcoming = (
        Meeting.query
        .filter(Meeting.teacher_id == current_user.id,
                Meeting.status == 'scheduled',
                Meeting.scheduled_at >= datetime.utcnow())
        .order_by(Meeting.scheduled_at)
        .all()
    )
    past = (
        Meeting.query
        .filter(Meeting.teacher_id == current_user.id,
                Meeting.scheduled_at < datetime.utcnow())
        .order_by(Meeting.scheduled_at.desc())
        .limit(20).all()
    )
    return render_template('teacher/meetings.html',
                           upcoming=upcoming, past=past, students=all_students,
                           now=datetime.utcnow())


@teacher_bp.route('/meetings/schedule', methods=['POST'])
@teacher_required
def schedule_meeting():
    student_id         = int(request.form.get('student_id'))
    project_id_raw     = request.form.get('project_id', '').strip()
    title              = request.form.get('title', '').strip()
    scheduled_at_str   = request.form.get('scheduled_at', '')
    duration_minutes   = int(request.form.get('duration_minutes', 30))
    is_recurring       = request.form.get('is_recurring') == 'on'
    recurrence_type    = request.form.get('recurrence_type', 'none')
    recurrence_end_str = request.form.get('recurrence_end_date', '').strip()

    student = Student.query.get_or_404(student_id)

    try:
        scheduled_at = datetime.strptime(scheduled_at_str, '%Y-%m-%dT%H:%M')
    except ValueError:
        flash('תאריך ושעה לא תקינים', 'danger')
        return redirect(url_for('teacher.meetings'))

    recurrence_end_date = None
    if recurrence_end_str:
        try:
            recurrence_end_date = datetime.strptime(recurrence_end_str, '%Y-%m-%d')
        except ValueError:
            pass

    project_id = int(project_id_raw) if project_id_raw else None

    conflict = _has_overlap(current_user.id, scheduled_at, duration_minutes)
    if conflict:
        conflict_end = conflict.scheduled_at + timedelta(minutes=conflict.duration_minutes)
        flash(
            f'חפיפה בלוח הזמנים: פגישה "{conflict.title}" '
            f'קיימת בין {conflict.scheduled_at.strftime("%H:%M")} ל-{conflict_end.strftime("%H:%M")} '
            f'באותו יום',
            'danger'
        )
        return redirect(url_for('teacher.meetings'))

    meeting = Meeting(
        teacher_id          = current_user.id,
        student_id          = student_id,
        project_id          = project_id,
        title               = title or f'פגישה עם {student.user.full_name}',
        scheduled_at        = scheduled_at,
        duration_minutes    = duration_minutes,
        is_recurring        = is_recurring,
        recurrence_type     = recurrence_type if is_recurring else 'none',
        recurrence_end_date = recurrence_end_date,
    )
    db.session.add(meeting)
    db.session.flush()

    event_id, cal_id = create_event(current_user, meeting, student.user.email)
    if event_id:
        meeting.google_event_id    = event_id
        meeting.google_calendar_id = cal_id

    db.session.commit()

    send_meeting_scheduled(student.user, meeting)
    emit_meeting_event(student_id, 'scheduled', {
        'id':           meeting.id,
        'title':        meeting.title,
        'scheduled_at': meeting.scheduled_at.strftime('%d/%m/%Y %H:%M'),
    })

    flash('הפגישה נקבעה בהצלחה', 'success')
    return redirect(url_for('teacher.meetings'))


@teacher_bp.route('/meetings/<int:meeting_id>/reschedule', methods=['GET', 'POST'])
@teacher_required
def reschedule_meeting(meeting_id):
    meeting = Meeting.query.get_or_404(meeting_id)

    if request.method == 'POST':
        new_dt_str = request.form.get('scheduled_at', '')
        try:
            new_dt = datetime.strptime(new_dt_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('תאריך ושעה לא תקינים', 'danger')
            return redirect(url_for('teacher.reschedule_meeting', meeting_id=meeting_id))

        new_duration = int(request.form.get('duration_minutes', meeting.duration_minutes))
        conflict = _has_overlap(current_user.id, new_dt, new_duration, exclude_meeting_id=meeting.id)
        if conflict:
            conflict_end = conflict.scheduled_at + timedelta(minutes=conflict.duration_minutes)
            flash(
                f'חפיפה בלוח הזמנים: פגישה "{conflict.title}" '
                f'קיימת בין {conflict.scheduled_at.strftime("%H:%M")} ל-{conflict_end.strftime("%H:%M")} '
                f'באותו יום',
                'danger'
            )
            return redirect(url_for('teacher.reschedule_meeting', meeting_id=meeting_id))

        old_time = meeting.scheduled_at
        meeting.scheduled_at = new_dt
        meeting.duration_minutes = new_duration
        db.session.commit()

        update_event(current_user, meeting, meeting.student.user.email)
        send_meeting_rescheduled(meeting.student.user, meeting, old_time)
        emit_meeting_event(meeting.student_id, 'rescheduled', {
            'id':           meeting.id,
            'title':        meeting.title,
            'scheduled_at': meeting.scheduled_at.strftime('%d/%m/%Y %H:%M'),
        })

        flash('הפגישה נדחתה בהצלחה', 'success')
        return redirect(url_for('teacher.meetings'))

    return render_template('teacher/reschedule.html', meeting=meeting)


@teacher_bp.route('/meetings/<int:meeting_id>/cancel', methods=['POST'])
@teacher_required
def cancel_meeting(meeting_id):
    meeting = Meeting.query.get_or_404(meeting_id)
    meeting.status = 'cancelled'

    delete_event(current_user, meeting)
    db.session.commit()

    send_meeting_cancelled(meeting.student.user, meeting)
    emit_meeting_event(meeting.student_id, 'cancelled', {
        'id':    meeting.id,
        'title': meeting.title,
    })

    flash('הפגישה בוטלה', 'success')
    return redirect(url_for('teacher.meetings'))


@teacher_bp.route('/meetings/<int:meeting_id>/complete', methods=['POST'])
@teacher_required
def complete_meeting(meeting_id):
    meeting = Meeting.query.get_or_404(meeting_id)
    meeting.status = 'completed'
    db.session.commit()
    flash('הפגישה סומנה כהושלמה', 'success')
    return redirect(url_for('teacher.meetings'))


# ── Reports ───────────────────────────────────────────────────────────────────

@teacher_bp.route('/reports')
@teacher_required
def reports():
    class_id = request.args.get('class_id', type=int)
    classes  = _teacher_classes(current_user.id)
    q = Student.query.join(User, Student.user_id == User.id).order_by(User.full_name)
    if class_id:
        q = q.filter(Student.class_group_id == class_id)
    elif not current_user.is_admin:
        ids = [c.id for c in classes]
        q = q.filter(Student.class_group_id.in_(ids or [-1]))
    students = q.all()
    return render_template('teacher/reports.html', students=students,
                           phase_display=PHASE_DISPLAY, phase_order=PHASE_ORDER,
                           classes=classes, selected_class_id=class_id,
                           now=datetime.utcnow())


@teacher_bp.route('/reports/<int:student_id>')
@teacher_required
def student_report(student_id):
    student = Student.query.get_or_404(student_id)
    next_meeting = None
    if student.project:
        next_meeting = (
            student.project.meetings
            .filter(Meeting.status == 'scheduled',
                    Meeting.scheduled_at >= datetime.utcnow())
            .order_by(Meeting.scheduled_at)
            .first()
        )
    return render_template(
        'teacher/student_report.html',
        student=student,
        phase_display=PHASE_DISPLAY,
        phase_order=PHASE_ORDER,
        next_meeting=next_meeting,
        now=datetime.utcnow(),
    )


# ── Class Groups ──────────────────────────────────────────────────────────────

@teacher_bp.route('/classes')
@teacher_required
def classes():
    all_classes = sorted(_teacher_classes(current_user.id),
                         key=lambda c: (-c.year, c.name))
    return render_template('teacher/classes.html', classes=all_classes, now=datetime.utcnow())


@teacher_bp.route('/classes/add', methods=['POST'])
@teacher_required
def add_class():
    name = request.form.get('name', '').strip()
    year = request.form.get('year', type=int)
    if not name or not year:
        flash('יש למלא שם כיתה ושנה', 'danger')
        return redirect(url_for('teacher.classes'))

    if ClassGroup.query.filter_by(name=name, year=year, teacher_id=current_user.id).first():
        flash('כיתה זו כבר קיימת', 'warning')
        return redirect(url_for('teacher.classes'))

    cg = ClassGroup(name=name, year=year, teacher_id=current_user.id)
    db.session.add(cg)
    db.session.commit()
    flash(f'הכיתה {name} ({year}) נוצרה', 'success')
    return redirect(url_for('teacher.classes'))


@teacher_bp.route('/classes/<int:class_id>/delete', methods=['POST'])
@teacher_required
def delete_class(class_id):
    cg = ClassGroup.query.get_or_404(class_id)
    if cg.teacher_id != current_user.id:
        flash('אין הרשאה', 'danger')
        return redirect(url_for('teacher.classes'))
    # Unlink students
    for s in cg.students:
        s.class_group_id = None
    db.session.delete(cg)
    db.session.commit()
    flash('הכיתה נמחקה', 'success')
    return redirect(url_for('teacher.classes'))


@teacher_bp.route('/classes/<int:class_id>/export')
@teacher_required
def export_class(class_id):
    from ..services.excel_service import export_class_to_excel
    cg = ClassGroup.query.get_or_404(class_id)
    if cg.teacher_id != current_user.id:
        flash('אין הרשאה', 'danger')
        return redirect(url_for('teacher.classes'))
    buf = export_class_to_excel(cg)
    filename = f"כיתה_{cg.name}_{cg.year}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@teacher_bp.route('/import', methods=['GET', 'POST'])
@teacher_required
def import_students():
    if request.method == 'POST':
        f = request.files.get('excel_file')
        if not f or not f.filename.endswith('.xlsx'):
            flash('יש להעלות קובץ xlsx', 'danger')
            return redirect(url_for('teacher.import_students'))
        from io import BytesIO
        from ..services.excel_service import import_students_from_excel
        try:
            buf = BytesIO(f.read())
            imported, errors = import_students_from_excel(buf, current_user)
        except Exception as exc:
            flash(f'שגיאה בקריאת הקובץ: {exc}', 'danger')
            return redirect(url_for('teacher.import_students'))
        return render_template('teacher/import.html',
                               imported=imported, errors=errors, done=True)
    return render_template('teacher/import.html', done=False)


@teacher_bp.route('/import/template')
@teacher_required
def download_template():
    from ..services.excel_service import generate_import_template
    buf = generate_import_template()
    return send_file(
        buf,
        as_attachment=True,
        download_name='תבנית_ייבוא_תלמידים.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@teacher_bp.route('/import/phases', methods=['GET', 'POST'])
@teacher_required
def import_phases():
    if request.method == 'POST':
        f = request.files.get('excel_file')
        if not f or not f.filename.endswith('.xlsx'):
            flash('יש להעלות קובץ xlsx', 'danger')
            return redirect(url_for('teacher.import_phases'))
        from io import BytesIO
        from ..services.excel_service import import_phases_from_excel
        try:
            buf = BytesIO(f.read())
            updated, errors = import_phases_from_excel(buf, current_user)
        except Exception as exc:
            flash(f'שגיאה בקריאת הקובץ: {exc}', 'danger')
            return redirect(url_for('teacher.import_phases'))
        return render_template('teacher/import_phases.html',
                               updated=updated, errors=errors, done=True)
    return render_template('teacher/import_phases.html', done=False)


@teacher_bp.route('/import/phases/template')
@teacher_required
def download_phase_template():
    from ..services.excel_service import generate_phase_template
    buf = generate_phase_template(current_user)
    return send_file(
        buf,
        as_attachment=True,
        download_name='תבנית_סטטוס_שלבים.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


# ── Google Calendar OAuth ─────────────────────────────────────────────────────

@teacher_bp.route('/google/auth')
@teacher_required
def google_auth():
    from google_auth_oauthlib.flow import Flow

    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = (
        '1' if current_app.debug else '0'
    )

    flow = _build_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )
    session['google_oauth_state'] = state
    return redirect(authorization_url)


@teacher_bp.route('/google/callback')
@teacher_required
def google_callback():
    from google_auth_oauthlib.flow import Flow

    state = session.get('google_oauth_state')
    flow = _build_flow(state=state)

    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:
        current_app.logger.error(f'Google OAuth callback error: {exc}')
        flash('שגיאה בהתחברות לגוגל קלנדר', 'danger')
        return redirect(url_for('teacher.meetings'))

    creds = flow.credentials
    current_user.google_credentials = json.dumps({
        'token':         creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri':     creds.token_uri,
        'scopes':        list(creds.scopes) if creds.scopes else [],
    })
    db.session.commit()
    flash('Google Calendar חובר בהצלחה', 'success')
    return redirect(url_for('teacher.meetings'))


@teacher_bp.route('/google/disconnect', methods=['POST'])
@teacher_required
def google_disconnect():
    current_user.google_credentials = None
    db.session.commit()
    flash('Google Calendar נותק', 'info')
    return redirect(url_for('teacher.meetings'))


def _build_flow(state=None):
    from google_auth_oauthlib.flow import Flow
    client_config = {
        'web': {
            'client_id':     current_app.config['GOOGLE_CLIENT_ID'],
            'client_secret': current_app.config['GOOGLE_CLIENT_SECRET'],
            'auth_uri':      'https://accounts.google.com/o/oauth2/auth',
            'token_uri':     'https://oauth2.googleapis.com/token',
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=['https://www.googleapis.com/auth/calendar.events'],
        state=state,
    )
    flow.redirect_uri = current_app.config['GOOGLE_REDIRECT_URI']
    return flow
