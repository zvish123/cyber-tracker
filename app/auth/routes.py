from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from . import auth_bp
from ..extensions import db
from ..models import User


def _home_url(user):
    if user.is_admin:
        return url_for('admin.dashboard')
    if user.is_teacher:
        return url_for('teacher.dashboard')
    return url_for('student.dashboard')


@auth_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(_home_url(current_user))
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(_home_url(current_user))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember_me'))

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.is_active:
                flash('חשבון זה מושבת. פנה למנהל המערכת.', 'danger')
                return render_template('auth/login.html')
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            return redirect(next_page or _home_url(user))

        flash('שם משתמש או סיסמה שגויים', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        full_name    = request.form.get('full_name',    '').strip()
        email        = request.form.get('email',        '').strip()
        current_pwd  = request.form.get('current_password', '')
        new_pwd      = request.form.get('new_password',     '').strip()
        confirm_pwd  = request.form.get('confirm_password', '').strip()

        if not full_name or not email:
            flash('שם מלא ואימייל הם שדות חובה', 'danger')
            return redirect(url_for('auth.profile'))

        dup = User.query.filter(User.email == email, User.id != current_user.id).first()
        if dup:
            flash('כתובת אימייל כבר קיימת במערכת', 'danger')
            return redirect(url_for('auth.profile'))

        current_user.full_name = full_name
        current_user.email     = email

        if new_pwd:
            if not current_user.check_password(current_pwd):
                flash('הסיסמה הנוכחית שגויה', 'danger')
                return redirect(url_for('auth.profile'))
            if new_pwd != confirm_pwd:
                flash('הסיסמאות החדשות אינן תואמות', 'danger')
                return redirect(url_for('auth.profile'))
            if len(new_pwd) < 6:
                flash('הסיסמה חייבת להכיל לפחות 6 תווים', 'danger')
                return redirect(url_for('auth.profile'))
            current_user.set_password(new_pwd)

        db.session.commit()
        flash('הפרופיל עודכן בהצלחה', 'success')
        return redirect(url_for('auth.profile'))

    return render_template('auth/profile.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
