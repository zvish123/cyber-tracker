import json as _json
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db


# ── Phase definitions ──────────────────────────────────────────────────────────

PHASE_ORDER = [
    'initiation',
    'characterization',
    'analysis_design',
    'implementation',
    'testing',
    'final_submission',
]

PHASE_DISPLAY = {
    'initiation':       'פתיחה',
    'characterization': 'אפיון',
    'analysis_design':  'ניתוח ועיצוב',
    'implementation':   'מימוש',
    'testing':          'בדיקות',
    'final_submission': 'הגשה סופית',
}

DEFAULT_PHASE_WEIGHTS = {
    'initiation':       10,
    'characterization': 15,
    'analysis_design':  20,
    'implementation':   30,
    'testing':          15,
    'final_submission': 10,
}


# ── Models ─────────────────────────────────────────────────────────────────────

class AppSettings(db.Model):
    __tablename__ = 'app_settings'

    id            = db.Column(db.Integer, primary_key=True)
    key           = db.Column(db.String(100), unique=True, nullable=False)
    value         = db.Column(db.Text, nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    updated_by = db.relationship('User', foreign_keys=[updated_by_id])

    @staticmethod
    def get(key, default=None):
        row = AppSettings.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def set(key, value, updated_by_id=None):
        row = AppSettings.query.filter_by(key=key).first()
        if row:
            row.value         = value
            row.updated_by_id = updated_by_id
            row.updated_at    = datetime.utcnow()
        else:
            row = AppSettings(key=key, value=value, updated_by_id=updated_by_id)
            db.session.add(row)


# Many-to-many: class_groups ↔ co-teachers (users)
class_teachers = db.Table(
    'class_teachers',
    db.Column('class_group_id', db.Integer, db.ForeignKey('class_groups.id'), primary_key=True),
    db.Column('user_id',        db.Integer, db.ForeignKey('users.id'),        primary_key=True),
)


class ClassGroup(db.Model):
    __tablename__ = 'class_groups'

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(50),  nullable=False)
    year       = db.Column(db.Integer,     nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teacher       = db.relationship('User', foreign_keys=[teacher_id], backref='class_groups')
    extra_teachers = db.relationship('User', secondary=class_teachers, backref='co_classes')
    students      = db.relationship('Student', back_populates='class_group')

    __table_args__ = (
        db.UniqueConstraint('name', 'year', 'teacher_id', name='uq_class_name_year_teacher'),
    )

    @property
    def display_name(self):
        return f"{self.name} ({self.year})"

    @property
    def all_teacher_ids(self):
        ids = {self.teacher_id}
        ids.update(t.id for t in self.extra_teachers)
        return ids


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id                 = db.Column(db.Integer, primary_key=True)
    username           = db.Column(db.String(80),  unique=True, nullable=False)
    full_name          = db.Column(db.String(120), nullable=False)
    email              = db.Column(db.String(120), unique=True, nullable=False)
    password_hash      = db.Column(db.String(256), nullable=False)
    role               = db.Column(db.String(20),  nullable=False, default='student')
    is_active          = db.Column(db.Boolean, default=True, nullable=False)
    google_credentials = db.Column(db.Text, nullable=True)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)

    student_profile = db.relationship('Student', backref='user', uselist=False,
                                      foreign_keys='Student.user_id')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def is_teacher(self):
        return self.role in ('teacher', 'admin')

    @property
    def is_student(self):
        return self.role == 'student'


class Student(db.Model):
    __tablename__ = 'students'

    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    class_name     = db.Column(db.String(50), nullable=True)
    class_group_id = db.Column(db.Integer, db.ForeignKey('class_groups.id'), nullable=True)
    teacher_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    project     = db.relationship('Project', backref='student', uselist=False,
                                   cascade='all, delete-orphan')
    meetings    = db.relationship('Meeting', foreign_keys='Meeting.student_id',
                                   backref='student', lazy='dynamic')
    class_group = db.relationship('ClassGroup', back_populates='students')
    owner       = db.relationship('User', foreign_keys=[teacher_id], backref='students_taught')


class Project(db.Model):
    __tablename__ = 'projects'

    id            = db.Column(db.Integer, primary_key=True)
    student_id    = db.Column(db.Integer, db.ForeignKey('students.id'), unique=True, nullable=False)
    title         = db.Column(db.String(200), nullable=False)
    subject       = db.Column(db.String(100), nullable=False)
    description   = db.Column(db.Text, nullable=True)
    phase_weights = db.Column(db.Text, nullable=True)   # JSON {phase: weight}
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    phases   = db.relationship('PhaseProgress', backref='project',
                                cascade='all, delete-orphan', lazy='dynamic')
    meetings = db.relationship('Meeting', backref='project', lazy='dynamic')

    def get_phase_weights(self):
        if self.phase_weights:
            try:
                return _json.loads(self.phase_weights)
            except (ValueError, TypeError):
                pass
        raw = AppSettings.get('default_phase_weights')
        if raw:
            try:
                return _json.loads(raw)
            except (ValueError, TypeError):
                pass
        return dict(DEFAULT_PHASE_WEIGHTS)

    def get_overall_percentage(self):
        rows = {p.phase: p for p in self.phases.all()}
        if not rows:
            return 0
        weights = self.get_phase_weights()
        total_weight = sum(weights.get(ph, 0) for ph in PHASE_ORDER)
        if total_weight == 0:
            return 0
        weighted_sum = sum(
            (rows[ph].percentage if ph in rows else 0) * weights.get(ph, 0)
            for ph in PHASE_ORDER
        )
        return round(weighted_sum / total_weight)

    @property
    def overall_percentage(self):
        return self.get_overall_percentage()

    def get_current_phase_display(self):
        rows = {p.phase: p for p in self.phases.all()}
        current = 'initiation'
        for phase in PHASE_ORDER:
            if phase in rows and rows[phase].percentage > 0:
                current = phase
        return PHASE_DISPLAY.get(current, current)

    def get_phases_dict(self):
        rows = {p.phase: p for p in self.phases.all()}
        return {phase: rows.get(phase) for phase in PHASE_ORDER}


class PhaseProgress(db.Model):
    __tablename__ = 'phase_progress'

    id             = db.Column(db.Integer, primary_key=True)
    project_id     = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    phase          = db.Column(db.String(50), nullable=False)
    percentage     = db.Column(db.Integer, default=0)
    notes          = db.Column(db.Text, nullable=True)
    updated_by_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    updated_by = db.relationship('User', foreign_keys=[updated_by_id])

    __table_args__ = (
        db.UniqueConstraint('project_id', 'phase', name='uq_project_phase'),
    )


class Meeting(db.Model):
    __tablename__ = 'meetings'

    id                   = db.Column(db.Integer, primary_key=True)
    teacher_id           = db.Column(db.Integer, db.ForeignKey('users.id'),    nullable=False)
    student_id           = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    project_id           = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    title                = db.Column(db.String(200), nullable=False)
    scheduled_at         = db.Column(db.DateTime, nullable=False)
    duration_minutes     = db.Column(db.Integer, default=30)
    status               = db.Column(db.String(20), default='scheduled')
    is_recurring         = db.Column(db.Boolean, default=False)
    recurrence_type      = db.Column(db.String(20), default='none')
    recurrence_end_date  = db.Column(db.DateTime, nullable=True)
    google_event_id      = db.Column(db.String(200), nullable=True)
    google_calendar_id   = db.Column(db.String(200), nullable=True)
    parent_meeting_id    = db.Column(db.Integer, db.ForeignKey('meetings.id'), nullable=True)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)

    teacher  = db.relationship('User',    foreign_keys=[teacher_id])
    children = db.relationship('Meeting', backref=db.backref('parent', remote_side=[id]))
