from flask import render_template
from flask_login import current_user, login_required
from sqlalchemy import func

from app.dashboard import bp
from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.permissions import (
    ASSETS_VIEW,
    AUDIT_VIEW,
    TICKETS_VIEW,
    TICKETS_VIEW_ALL,
    USERS_MANAGE,
    VEHICLES_VIEW,
)
from app.services.assets import asset_summary
from app.services.tickets import ticket_summary
from app.services.vehicles import vehicle_summary


@bp.route("/")
@login_required
def index():
    # Tüm değerler veritabanından hesaplanır; sabit örnek sayı gösterilmez.
    # Her özet yalnızca ilgili izne sahip kullanıcı için hesaplanır (yetkisiz kullanıcıya veri hiç gönderilmez).
    user = current_user
    active_users = None
    recent_events = []
    if user.has_permission(USERS_MANAGE):
        active_users = db.session.scalar(
            db.select(func.count()).select_from(User).where(User.active.is_(True))
        )
    if user.has_permission(AUDIT_VIEW):
        recent_events = db.session.scalars(
            db.select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(8)
        ).all()
    return render_template(
        "dashboard/index.html",
        active_users=active_users,
        recent_events=recent_events,
        asset_stats=asset_summary() if user.has_permission(ASSETS_VIEW) else None,
        ticket_stats=ticket_summary(user)
        if user.has_permission(TICKETS_VIEW) or user.has_permission(TICKETS_VIEW_ALL) else None,
        vehicle_stats=vehicle_summary() if user.has_permission(VEHICLES_VIEW) else None,
    )
