from flask import Blueprint

bp = Blueprint("vehicles", __name__, url_prefix="/araclar")

from app.vehicles import routes  # noqa: E402,F401
