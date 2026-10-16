from sqlalchemy.orm import validates

from app.extensions import db
from app.permissions import GRANTABLE, USERS_MANAGE
from app.utils import utcnow


class UserPermission(db.Model):
    """Kullanıcıya atanmış modül izni. Etkin yetki bu satırlar ile rol tavanının kesişimidir
    (bkz. `app.permissions.effective_permissions`). `users.manage` hiçbir zaman satır olamaz."""

    __tablename__ = "user_permissions"
    __table_args__ = (
        db.CheckConstraint(f"permission <> '{USERS_MANAGE}'", name="ck_user_permissions_not_users_manage"),
        db.CheckConstraint("length(permission) > 0", name="ck_user_permissions_not_empty"),
    )

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    permission = db.Column(db.String(50), primary_key=True)
    granted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    granted_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    @validates("permission")
    def _validate_permission(self, key, value):
        if value == USERS_MANAGE:
            raise ValueError("users.manage hiçbir kullanıcıya atanamaz; yalnızca süper admin kullanabilir.")
        if value not in GRANTABLE:
            raise ValueError(f"Bilinmeyen izin: {value!r}")
        return value
