from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.permissions import (
    GRANTABLE,
    MAX_SENIORITY,
    MIN_SENIORITY,
    ROLE_ARIZA_KONTROL,
    ROLE_LABELS,
    ROLE_SUPERADMIN,
    effective_permissions,
)
from app.utils import utcnow

# Eski adlar (yalnızca geriye dönük uyumluluk ve testler için): uygulama kodu bunları KULLANMAZ, yetki kontrolleri
# izin tabanlıdır (app/decorators.py, app/services/authz.py). Eski "admin" = süper admin, eski "staff" = arıza kontrol.
ROLE_ADMIN = ROLE_SUPERADMIN
ROLE_STAFF = ROLE_ARIZA_KONTROL

_ROLES_SQL = ", ".join(repr(r) for r in ROLE_LABELS)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        db.CheckConstraint(f"role IN ({_ROLES_SQL})", name="ck_users_role"),
        db.CheckConstraint(
            f"seniority_level >= {MIN_SENIORITY} AND seniority_level <= {MAX_SENIORITY}", name="ck_users_seniority_level"),
        # Sistemde en fazla BİR aktif süper admin olabilir (SQL ile bile ikincisi eklenemez/etkinleştirilemez).
        db.Index("ux_users_single_active_superadmin", "role", unique=True,
                 sqlite_where=db.text("role = 'superadmin' AND active = 1")),
    )

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_ARIZA_KONTROL)
    # Kurumdaki hiyerarşik seviye (1-5). Yalnızca bilgi amaçlıdır; hiçbir yetki vermez.
    seniority_level = db.Column(db.Integer, nullable=False, default=MIN_SENIORITY, server_default=str(MIN_SENIORITY))
    # İleride birim bazlı yetkilendirme için ayrılmış alan (ilk sürümde kullanılmaz).
    department = db.Column(db.String(100), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    # --- Flask-Login: devre dışı hesap "kimliği doğrulanmış" sayılmaz ---
    @property
    def is_active(self) -> bool:
        return bool(self.active)

    permission_grants = db.relationship(
        "UserPermission", foreign_keys="UserPermission.user_id", cascade="all, delete-orphan", lazy="selectin")

    @property
    def is_superadmin(self) -> bool:
        return self.role == ROLE_SUPERADMIN

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    # --- İzinler: veritabanındaki güncel satırlardan hesaplanır, önbelleğe alınmaz ---
    @property
    def granted_permissions(self) -> frozenset:
        return frozenset(g.permission for g in self.permission_grants)

    @property
    def effective_permissions(self) -> frozenset:
        return effective_permissions(self.role, self.granted_permissions)

    def has_permission(self, permission: str) -> bool:
        """Devre dışı hesabın hiçbir izni yoktur. Süper admin tüm izinlere sahiptir."""
        return bool(self.active) and permission in self.effective_permissions

    def set_permissions(self, permissions, granted_by_id: int | None = None) -> None:
        """Atanmış izinleri verilen kümeyle değiştirir (rol tavanı dışı veya geçersiz izin ValueError verir)."""
        wanted = set(permissions)
        invalid = wanted - GRANTABLE
        if invalid:
            raise ValueError(f"Atanamayan veya bilinmeyen izinler: {', '.join(sorted(invalid))}")
        from app.models.permission import UserPermission

        current = {g.permission: g for g in self.permission_grants}
        for key in set(current) - wanted:
            self.permission_grants.remove(current[key])
        for key in wanted - set(current):
            self.permission_grants.append(UserPermission(permission=key, granted_by_id=granted_by_id))

    # --- Parola ---
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @staticmethod
    def burn_password_check(password: str) -> None:
        """Kullanıcı yokken de aynı sürede hash doğrulaması yaparak zamanlama farkını azaltır."""
        global _DUMMY_HASH
        if _DUMMY_HASH is None:
            _DUMMY_HASH = generate_password_hash("zamanlama-icin-sahte-deger")
        check_password_hash(_DUMMY_HASH, password)

    def __repr__(self) -> str:
        return f"<User {self.username}>"


_DUMMY_HASH: str | None = None


@login_manager.user_loader
def load_user(user_id: str):
    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    if user is None or not user.active:
        return None
    return user
