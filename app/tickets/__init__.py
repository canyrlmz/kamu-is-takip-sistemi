from flask import Blueprint

bp = Blueprint("tickets", __name__, url_prefix="/talepler")

from app.tickets import routes  # noqa: E402,F401
