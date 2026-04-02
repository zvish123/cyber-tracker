import json
from functools import wraps
from datetime import datetime

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from . import admin_bp
from ..extensions import db
from ..models import (User, Student, Project, ClassGroup, AppSettings,
                      DEFAULT_PHASE_WEIGHTS, PHASE_ORDER, PHASE_DISPLAY)


# ── Decorator ─────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('גישה מותרת למנהלי מערכת בלבד', 'danger')
            return redirect(url_for('auth.index'))
        return f(*args, **kwargs)
    return decorated


# ── Dashboard ─────────────────────────────────────────────────────────────────

@admin_bp.route('/')
@admin_required
def dashboard():
    teacher_count = User.query.filter(User.role.in_(['teacher', 'admin'])).count()
    student_count = Student.query.count()
    project_count = Project.query.count()
    inactive_count = User.query.filter(
        User.role.in_(['teacher', 'admin']), User.is_active == False
    ).count()
    return render_template('admin/dashboard.html',
                           teacher_count=teacher_count,
                           student_count=student_count,
                           project_count=project_count,
                           inactive_count=inactive_count)


# ── Teacher management ────────────────────────────────────────────────────────

@admin_bp.route('/teachers')
@admin_required
def teachers():
    all_teachers = (User.query
                    .filter(User.role.in_(['teacher', 'admin']))
                    .order_by(User.full_name).all())
    return render_template('admin/teachers.html', teachers=all_teachers)


@admin_bp.route('/teachers/add', methods=['POST'])
@admin_required
def add_teacher():
    full_name = request.form.get('full_name', '').strip()
    username  = request.form.get('username',  '').strip()
    email     = request.form.get('email',     '').strip()
    password  = request.form.get('password',  '').strip()

    if not all([full_name, username, email, password]):
        flash('יש למלא את כל השדות', 'danger')
        return redirect(url_for('admin.teachers'))

    if User.query.filter_by(username=username).first():
        flash('שם משתמש כבר קיים', 'danger')
        return redirect(url_for('admin.teachers'))

    if User.query.filter_by(email=email).first():
        flash('אימייל כבר קיים', 'danger')
        return redirect(url_for('admin.teachers'))

    user = User(username=username, full_name=full_name, email=email, role='teacher')
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'המורה {full_name} נוסף בהצלחה', 'success')
    return redirect(url_for('admin.teachers'))


@admin_bp.route('/teachers/<int:teacher_id>/edit', methods=['POST'])
@admin_required
def edit_teacher(teacher_id):
    user      = User.query.get_or_404(teacher_id)
    full_name = request.form.get('full_name', '').strip()
    email     = request.form.get('email',     '').strip()
    password  = request.form.get('password',  '').strip()

    if not full_name or not email:
        flash('שם מלא ואימייל הם שדות חובה', 'danger')
        return redirect(url_for('admin.teachers'))

    dup = User.query.filter(User.email == email, User.id != user.id).first()
    if dup:
        flash('כתובת אימייל כבר קיימת במערכת', 'danger')
        return redirect(url_for('admin.teachers'))

    user.full_name = full_name
    user.email     = email
    if password:
        if len(password) < 6:
            flash('הסיסמה חייבת להכיל לפחות 6 תווים', 'danger')
            return redirect(url_for('admin.teachers'))
        user.set_password(password)

    db.session.commit()
    flash(f'פרטי {full_name} עודכנו', 'success')
    return redirect(url_for('admin.teachers'))


@admin_bp.route('/teachers/<int:teacher_id>/toggle', methods=['POST'])
@admin_required
def toggle_teacher(teacher_id):
    user = User.query.get_or_404(teacher_id)
    if user.id == current_user.id:
        flash('לא ניתן לשנות את סטטוס החשבון שלך', 'danger')
        return redirect(url_for('admin.teachers'))
    user.is_active = not user.is_active
    db.session.commit()
    status = 'הופעל' if user.is_active else 'הושבת'
    flash(f'החשבון של {user.full_name} {status}', 'success' if user.is_active else 'warning')
    return redirect(url_for('admin.teachers'))


@admin_bp.route('/teachers/<int:teacher_id>/promote', methods=['POST'])
@admin_required
def promote_teacher(teacher_id):
    user = User.query.get_or_404(teacher_id)
    if user.id == current_user.id:
        flash('לא ניתן לשנות את תפקידך', 'danger')
        return redirect(url_for('admin.teachers'))
    user.role = 'admin' if user.role == 'teacher' else 'teacher'
    db.session.commit()
    role_label = 'מנהל מערכת' if user.role == 'admin' else 'מורה'
    flash(f'תפקיד {user.full_name} שונה ל-{role_label}', 'success')
    return redirect(url_for('admin.teachers'))


# ── Class management ──────────────────────────────────────────────────────────

@admin_bp.route('/classes')
@admin_required
def classes():
    all_classes = (ClassGroup.query
                   .order_by(ClassGroup.year.desc(), ClassGroup.name).all())
    all_teachers = (User.query
                    .filter(User.role.in_(['teacher', 'admin']), User.is_active == True)
                    .order_by(User.full_name).all())
    return render_template('admin/classes.html',
                           classes=all_classes, all_teachers=all_teachers)


@admin_bp.route('/classes/<int:class_id>/set-teacher', methods=['POST'])
@admin_required
def set_class_teacher(class_id):
    cg         = ClassGroup.query.get_or_404(class_id)
    teacher_id = request.form.get('teacher_id', type=int)
    if not teacher_id:
        flash('יש לבחור מורה', 'danger')
        return redirect(url_for('admin.classes'))
    teacher = User.query.get_or_404(teacher_id)
    cg.teacher_id = teacher.id
    # Reassign all students in this class to the new primary teacher
    Student.query.filter_by(class_group_id=cg.id).update({'teacher_id': teacher.id})
    db.session.commit()
    flash(f'המורה הראשי של {cg.display_name} שונה ל-{teacher.full_name}', 'success')
    return redirect(url_for('admin.classes'))


@admin_bp.route('/classes/<int:class_id>/add-teacher', methods=['POST'])
@admin_required
def add_class_teacher(class_id):
    cg         = ClassGroup.query.get_or_404(class_id)
    teacher_id = request.form.get('teacher_id', type=int)
    if not teacher_id:
        flash('יש לבחור מורה', 'danger')
        return redirect(url_for('admin.classes'))
    teacher = User.query.get_or_404(teacher_id)
    if teacher.id == cg.teacher_id:
        flash(f'{teacher.full_name} כבר המורה הראשי של כיתה זו', 'warning')
        return redirect(url_for('admin.classes'))
    if teacher in cg.extra_teachers:
        flash(f'{teacher.full_name} כבר משויך לכיתה זו', 'warning')
        return redirect(url_for('admin.classes'))
    cg.extra_teachers.append(teacher)
    db.session.commit()
    flash(f'{teacher.full_name} נוסף לכיתה {cg.display_name}', 'success')
    return redirect(url_for('admin.classes'))


@admin_bp.route('/classes/<int:class_id>/remove-teacher/<int:teacher_id>', methods=['POST'])
@admin_required
def remove_class_teacher(class_id, teacher_id):
    cg      = ClassGroup.query.get_or_404(class_id)
    teacher = User.query.get_or_404(teacher_id)
    if teacher in cg.extra_teachers:
        cg.extra_teachers.remove(teacher)
        db.session.commit()
        flash(f'{teacher.full_name} הוסר מכיתה {cg.display_name}', 'success')
    return redirect(url_for('admin.classes'))


# ── Settings (phase weights) ──────────────────────────────────────────────────

@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    if request.method == 'POST':
        weights = {}
        for phase in PHASE_ORDER:
            w = request.form.get(f'weight_{phase}', type=int) or 0
            weights[phase] = max(0, w)
        total = sum(weights.values())
        if total != 100:
            flash(f'סכום המשקלות חייב להיות בדיוק 100% (כרגע: {total}%)', 'danger')
            return render_template('admin/settings.html',
                                   weights=weights,
                                   phase_order=PHASE_ORDER,
                                   phase_display=PHASE_DISPLAY)

        AppSettings.set('default_phase_weights',
                        json.dumps(weights), current_user.id)
        db.session.commit()
        flash('משקלות ברירת המחדל נשמרו בהצלחה', 'success')
        return redirect(url_for('admin.settings'))

    raw = AppSettings.get('default_phase_weights',
                          json.dumps(DEFAULT_PHASE_WEIGHTS))
    current_weights = json.loads(raw)
    return render_template('admin/settings.html',
                           weights=current_weights,
                           phase_order=PHASE_ORDER,
                           phase_display=PHASE_DISPLAY)
