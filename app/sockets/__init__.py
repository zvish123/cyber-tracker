from flask import Blueprint

sockets_bp = Blueprint('sockets', __name__)

from . import events  # noqa: F401, E402 — registers SocketIO event handlers
