from flask import Blueprint

student_bp = Blueprint('student', __name__)

from . import routes  # noqa: F401, E402
