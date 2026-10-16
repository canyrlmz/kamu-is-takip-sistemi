"""Asama 5: roller, kidem seviyesi ve modul izinleri

Revision ID: c5d8f2b7a461
Revises: b4c7e1a9d352
Create Date: 2026-10-16 14:50:00

Bu migration ``users`` tablosunu (rol kisiti degistigi icin) YENIDEN OLUSTURUR; bu nedenle uygulamadan once
veritabani yedegi alinmalidir. Eski rolleri su sekilde donusturur:

* tek aktif ``admin``          -> ``superadmin``      (birden fazla aktif admin varsa migration HIC DEGISIKLIK
                                                        yapmadan durur; kimin super admin olacagina siz karar verirsiniz)
* pasif ``admin``              -> ``birim_sorumlusu`` (verilebilir tum izinlerle; bugunku yetkisini kaybetmez)
* ``staff``                    -> ``ariza_kontrol``   (bugunku yetkilerinin birebir karsiligi 6 izinle)

Donusturulen her hesap icin denetim kaydi (``user_role_changed``) yazilir. Migration modul sabitlerini
iceri aktarmaz (gecmis migration'in ileride degisen koddan etkilenmemesi icin degerler burada sabittir).
"""
from alembic import op
import sqlalchemy as sa

revision = 'c5d8f2b7a461'
down_revision = 'b4c7e1a9d352'
branch_labels = None
depends_on = None

LEGACY_STAFF_PERMISSIONS = (
    "assets.view", "vehicles.view", "maintenance.view", "tickets.view", "tickets.create", "tickets.update",
)
ALL_GRANTABLE_PERMISSIONS = (
    "audit.view", "assets.view", "assets.create", "assets.update", "assets.assign",
    "tickets.view", "tickets.view_all", "tickets.create", "tickets.update", "tickets.manage", "tickets.assign",
    "vehicles.view", "vehicles.create", "vehicles.update",
    "maintenance.view", "maintenance.create", "maintenance.void",
)
NEW_ROLES = "'superadmin', 'birim_sorumlusu', 'bakim_personeli', 'ariza_kontrol', 'goruntuleyici'"
OLD_ROLES = "'admin', 'staff'"
USER_COLUMNS = "id, username, full_name, password_hash, department, active, created_at, updated_at, last_login_at"


def _values(keys):
    return " UNION ALL ".join(f"SELECT '{k}' AS permission" for k in keys)


def _users_columns(role_length=20):
    return [
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('full_name', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=role_length), nullable=False),
    ]


def _users_tail():
    return [
        sa.Column('department', sa.String(length=100), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
    ]


def _require_safe_table_rebuild():
    """users tablosu baska tablolarca referans edilir. SQLite'ta yabanci anahtar denetimi AÇIKKEN tabloyu
    silip yeniden olusturmak baglantili kayitlari bozar; bu yuzden migrations/env.py denetimi kapatir.
    Kapatilamadiysa (eski env.py veya acik islem) HICBIR degisiklik yapmadan durulur. Ayrica mevcut
    verilerde onceden var olan bir yabanci anahtar ihlali varsa yine durulur."""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    if not getattr(bind.connection.driver_connection, "in_transaction", False):
        raise RuntimeError(
            "Migration tek bir veritabani islemi icinde calismiyor (migrations/env.py eski surum olabilir); tablo "
            "silme/yeniden adlandirma adimlari hata durumunda geri alinamaz. Hicbir degisiklik yapilmadi. "
            "Guncelleme paketindeki migrations/env.py dosyasinin kopyalandigindan emin olun.")
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError(
            "Yabanci anahtar denetimi kapatilamadi; users tablosu guvenle yeniden olusturulamaz. "
            "Hicbir degisiklik yapilmadi. Guncelleme paketindeki migrations/env.py dosyasinin kopyalandigindan emin olun.")
    if bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError(
            "Veritabaninda onceden var olan yabanci anahtar tutarsizliklari bulundu (PRAGMA foreign_key_check). "
            "Hicbir degisiklik yapilmadi; once veri tutarliligi duzeltilmelidir.")


def upgrade():
    bind = op.get_bind()

    # 1) ON KONTROL (hicbir sey degismeden once)
    active_admins = bind.execute(sa.text("SELECT count(*) FROM users WHERE role = 'admin' AND active = 1")).scalar()
    if active_admins > 1:
        raise RuntimeError(
            f"{active_admins} aktif 'admin' hesabi var. Sistemde yalniz bir super admin olabilir ve hangi hesabin "
            "super admin olacagi otomatik secilmez. Hicbir degisiklik yapilmadi. Gerekmeyen yonetici hesaplarini "
            "'Kullanicilar' ekranindan devre disi birakip (tek aktif yonetici kalsin) migration'i yeniden calistirin."
        )
    _require_safe_table_rebuild()

    # Tum adimlar env.py'nin actigi TEK islem icindedir: herhangi bir adimda (CREATE/INSERT/DROP/RENAME/INDEX/
    # alembic_version) hata olursa islemin tamami geri alinir; yarim tablo veya veri birakilmaz.
    # 2) Yeni nesneler
    op.create_table(
        'users_new',
        *_users_columns(),
        sa.Column('seniority_level', sa.Integer(), server_default='1', nullable=False),
        *_users_tail(),
        sa.CheckConstraint(f"role IN ({NEW_ROLES})", name='ck_users_role'),
        sa.CheckConstraint("seniority_level >= 1 AND seniority_level <= 5", name='ck_users_seniority_level'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'user_permissions',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('permission', sa.String(length=50), nullable=False),
        sa.Column('granted_by_id', sa.Integer(), nullable=True),
        sa.Column('granted_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(permission) > 0", name='ck_user_permissions_not_empty'),
        sa.CheckConstraint("permission <> 'users.manage'", name='ck_user_permissions_not_users_manage'),
        sa.ForeignKeyConstraint(['granted_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('user_id', 'permission'),
    )

    # 3) Veri donusumu (tek SQL ifadesi: kullanicilar birebir kopyalanir, yalniz rol ve kidem eklenir)
    op.execute(
        f"INSERT INTO users_new ({USER_COLUMNS}, role, seniority_level) "
        f"SELECT {USER_COLUMNS}, "
        "CASE WHEN role = 'admin' AND active = 1 THEN 'superadmin' "
        "WHEN role = 'admin' THEN 'birim_sorumlusu' ELSE 'ariza_kontrol' END, 1 FROM users"
    )
    # Izinler: staff -> bugunku 6 izin; pasif admin -> verilebilir tum izinler
    op.execute(
        "INSERT INTO user_permissions (user_id, permission, granted_by_id, granted_at) "
        f"SELECT o.id, p.permission, NULL, CURRENT_TIMESTAMP FROM users o JOIN ({_values(LEGACY_STAFF_PERMISSIONS)}) p "
        "WHERE o.role = 'staff'"
    )
    op.execute(
        "INSERT INTO user_permissions (user_id, permission, granted_by_id, granted_at) "
        f"SELECT o.id, p.permission, NULL, CURRENT_TIMESTAMP FROM users o JOIN ({_values(ALL_GRANTABLE_PERMISSIONS)}) p "
        "WHERE o.role = 'admin' AND o.active = 0"
    )
    # Denetim: donusturulen her hesap icin kayit
    op.execute(
        "INSERT INTO audit_logs (created_at, user_id, action, entity_type, entity_id, details) "
        "SELECT CURRENT_TIMESTAMP, NULL, 'user_role_changed', 'user', o.id, "
        "'Asama 5 gecisi: ' || o.role || ' -> ' || "
        "CASE WHEN o.role = 'admin' AND o.active = 1 THEN 'superadmin' "
        "WHEN o.role = 'admin' THEN 'birim_sorumlusu' ELSE 'ariza_kontrol' END FROM users o"
    )

    # 4) Eski tabloyu degistir
    op.drop_table('users')
    op.rename_table('users_new', 'users')

    op.create_index('ix_users_username', 'users', ['username'], unique=True)
    op.create_index(
        'ux_users_single_active_superadmin', 'users', ['role'], unique=True,
        sqlite_where=sa.text("role = 'superadmin' AND active = 1"),
    )


def downgrade():
    """Roller eski ikili modele doner: superadmin -> admin, diger tum roller -> staff.
    Rol ayrintilari, kidem seviyesi ve izinler KAYBOLUR (birim sorumlusu gibi roller yonetici yetkisini kaybeder).
    Upgrade gibi tek islem icinde calisir: hata olursa tamami geri alinir."""
    _require_safe_table_rebuild()
    op.create_table(
        'users_old',
        *_users_columns(),
        *_users_tail(),
        sa.CheckConstraint(f"role IN ({OLD_ROLES})", name='ck_users_role'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.execute(
        f"INSERT INTO users_old ({USER_COLUMNS}, role) SELECT {USER_COLUMNS}, "
        "CASE WHEN role = 'superadmin' THEN 'admin' ELSE 'staff' END FROM users"
    )
    op.drop_table('user_permissions')
    op.drop_table('users')
    op.rename_table('users_old', 'users')
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
