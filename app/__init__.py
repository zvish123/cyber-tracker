import os
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from .extensions import db, login_manager, mail, socketio
from config import Config

csrf = CSRFProtect()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # ── Extensions ────────────────────────────────────────────────────────────
    csrf.init_app(app)
    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    socketio.init_app(
        app,
        cors_allowed_origins='*',
        async_mode='eventlet',
        logger=False,
        engineio_logger=False,
    )

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'יש להתחבר כדי לגשת לעמוד זה'
    login_manager.login_message_category = 'warning'

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ── Blueprints ────────────────────────────────────────────────────────────
    from .auth    import auth_bp
    from .teacher import teacher_bp
    from .student import student_bp
    from .admin   import admin_bp
    from .sockets import sockets_bp   # registers SocketIO event handlers

    app.register_blueprint(auth_bp)
    app.register_blueprint(teacher_bp, url_prefix='/teacher')
    app.register_blueprint(student_bp, url_prefix='/student')
    app.register_blueprint(admin_bp,   url_prefix='/admin')
    app.register_blueprint(sockets_bp)

    # ── Template filters ──────────────────────────────────────────────────────
    @app.template_filter('hdate')
    def hebrew_date(dt):
        return dt.strftime('%d/%m/%Y %H:%M') if dt else '—'

    # ── DB + seed ─────────────────────────────────────────────────────────────
    with app.app_context():
        db.create_all()
        _migrate_db()
        _seed_defaults()
        _enable_wal()

    # ── Scheduler (24h reminders) ─────────────────────────────────────────────
    # Guard against double-start in Flask debug mode (two processes)
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        from .services.scheduler import start_scheduler
        start_scheduler(app)

    return app


def _enable_wal():
    """Switch SQLite to WAL mode — reduces lock contention with eventlet."""
    from sqlalchemy import text
    if db.engine.url.drivername.startswith('sqlite'):
        with db.engine.connect() as conn:
            conn.execute(text('PRAGMA journal_mode=WAL'))
            conn.execute(text('PRAGMA synchronous=NORMAL'))


def _migrate_db():
    """Add any missing columns — safe to run on every startup."""
    from sqlalchemy import text, inspect as sa_inspect
    inspector = sa_inspect(db.engine)

    student_cols = [c['name'] for c in inspector.get_columns('students')]
    if 'class_group_id' not in student_cols:
        db.session.execute(text(
            'ALTER TABLE students ADD COLUMN class_group_id INTEGER REFERENCES class_groups(id)'
        ))
    if 'teacher_id' not in student_cols:
        db.session.execute(text(
            'ALTER TABLE students ADD COLUMN teacher_id INTEGER REFERENCES users(id)'
        ))

    user_cols = [c['name'] for c in inspector.get_columns('users')]
    if 'is_active' not in user_cols:
        db.session.execute(text(
            'ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1'
        ))

    project_cols = [c['name'] for c in inspector.get_columns('projects')]
    if 'phase_weights' not in project_cols:
        db.session.execute(text(
            'ALTER TABLE projects ADD COLUMN phase_weights TEXT'
        ))

    db.session.commit()


def _seed_defaults():
    """Seed default teacher, admin account, and app settings."""
    import json
    from .models import User, AppSettings, DEFAULT_PHASE_WEIGHTS

    # Default teacher
    if not User.query.filter_by(role='teacher').first():
        teacher = User(
            username='teacher', full_name='המורה',
            email='teacher@school.local', role='teacher',
        )
        teacher.set_password('teacher123')
        db.session.add(teacher)
        db.session.commit()

    # Default admin
    if not User.query.filter_by(role='admin').first():
        admin = User(
            username='admin', full_name='מנהל מערכת',
            email='admin@school.local', role='admin',
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()

    # Default phase weights
    if not AppSettings.query.filter_by(key='default_phase_weights').first():
        AppSettings.set('default_phase_weights', json.dumps(DEFAULT_PHASE_WEIGHTS))
        db.session.commit()
