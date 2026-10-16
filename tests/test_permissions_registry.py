"""Aşama 5.1: izin kayıt defteri, rol tavanları ve kullanıcı modeli kuralları."""
import inspect as pyinspect

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import permissions as perm
from app.extensions import db
from app.models.permission import UserPermission
from app.models.user import User


# --- Kayıt defteri (saf veri) --------------------------------------------------------------------
def test_roles_are_the_five_required_ones():
    assert set(perm.ROLE_LABELS) == {"superadmin", "birim_sorumlusu", "bakim_personeli", "ariza_kontrol", "goruntuleyici"}
    assert set(perm.ROLE_CEILINGS) == set(perm.ROLE_LABELS) - {perm.ROLE_SUPERADMIN}


def test_users_manage_is_system_only_and_in_no_ceiling_or_preset():
    assert perm.USERS_MANAGE in perm.PERMISSIONS
    assert perm.USERS_MANAGE in perm.SYSTEM_ONLY and perm.USERS_MANAGE not in perm.GRANTABLE
    for role, ceiling in perm.ROLE_CEILINGS.items():
        assert perm.USERS_MANAGE not in ceiling, role
    for role, preset in perm.ROLE_PRESETS.items():
        assert perm.USERS_MANAGE not in preset, role


def test_viewer_can_be_granted_view_all_separately_but_never_write_permissions():
    role = perm.ROLE_GORUNTULEYICI
    preset = perm.ROLE_PRESETS[role]
    assert "tickets.view_all" not in perm.effective_permissions(role, preset)
    assert "tickets.view_all" in perm.effective_permissions(role, preset | {"tickets.view_all"})
    ok, rejected = perm.split_grants(role, {"tickets.view_all", "tickets.create", "tickets.update", "assets.create",
                                           "maintenance.void", "audit.view", "users.manage"})
    assert ok == {"tickets.view_all"}
    assert rejected == {"tickets.create", "tickets.update", "assets.create", "maintenance.void", "audit.view",
                        "users.manage"}


def test_presets_and_legacy_grants_fit_inside_ceilings():
    for role, preset in perm.ROLE_PRESETS.items():
        assert preset <= perm.ROLE_CEILINGS[role], role
    assert perm.LEGACY_STAFF_PERMISSIONS <= perm.ROLE_CEILINGS[perm.ROLE_ARIZA_KONTROL]
    assert all(c <= perm.GRANTABLE for c in perm.ROLE_CEILINGS.values())


def test_ceilings_match_the_approved_design():
    viewer = perm.ROLE_CEILINGS[perm.ROLE_GORUNTULEYICI]
    assert viewer == {"assets.view", "tickets.view", "tickets.view_all", "vehicles.view", "maintenance.view"}
    assert perm.AUDIT_VIEW not in viewer
    # Görüntüleyici: yalnızca görüntüleme izinleri; hiçbir oluşturma/düzenleme/atama/iptal izni tavanda yok.
    assert all(p.endswith((".view", ".view_all")) for p in viewer)
    # tickets.view_all tavanda (ayrıca atanabilir) ama önerilen sette değil.
    assert "tickets.view_all" in viewer
    assert perm.ROLE_PRESETS[perm.ROLE_GORUNTULEYICI] == {"assets.view", "tickets.view", "vehicles.view",
                                                          "maintenance.view"}
    assert perm.ROLE_CEILINGS[perm.ROLE_BIRIM_SORUMLUSU] == perm.GRANTABLE
    maint = perm.ROLE_CEILINGS[perm.ROLE_BAKIM_PERSONELI]
    assert {"vehicles.create", "vehicles.update", "maintenance.create", "maintenance.void"} <= maint
    assert not {"assets.create", "assets.update", "assets.assign", "tickets.assign", "tickets.manage",
                "audit.view"} & maint
    tickets = perm.ROLE_CEILINGS[perm.ROLE_ARIZA_KONTROL]
    assert {"tickets.assign", "tickets.manage", "tickets.update"} <= tickets
    assert not {"assets.update", "vehicles.update", "maintenance.create", "audit.view"} & tickets


def test_legacy_staff_grants_equal_todays_staff_capabilities():
    assert perm.LEGACY_STAFF_PERMISSIONS == {
        "assets.view", "vehicles.view", "maintenance.view", "tickets.view", "tickets.create", "tickets.update"}


def test_superadmin_has_everything_including_users_manage():
    assert perm.effective_permissions(perm.ROLE_SUPERADMIN, set()) == set(perm.PERMISSIONS)
    assert perm.has_permission(perm.ROLE_SUPERADMIN, set(), perm.USERS_MANAGE)


@pytest.mark.parametrize("role", sorted(perm.ROLE_CEILINGS))
def test_nobody_but_superadmin_can_have_users_manage(role):
    everything = set(perm.PERMISSIONS)
    effective = perm.effective_permissions(role, everything)
    assert perm.USERS_MANAGE not in effective
    assert effective == perm.ROLE_CEILINGS[role]  # her şey verilse bile tavanı aşamaz
    assert not perm.has_permission(role, everything, perm.USERS_MANAGE)


def test_grants_outside_ceiling_or_unknown_are_inert():
    assert perm.effective_permissions(perm.ROLE_GORUNTULEYICI, {"vehicles.update", "bogus.key", "assets.view"}) == {
        "assets.view"}
    assert perm.effective_permissions("bilinmeyen_rol", {"assets.view"}) == frozenset()


def test_split_grants_reports_rejected_permissions():
    ok, rejected = perm.split_grants(perm.ROLE_GORUNTULEYICI, {"assets.view", "vehicles.update", "users.manage", "x.y"})
    assert ok == {"assets.view"} and rejected == {"vehicles.update", "users.manage", "x.y"}
    ok, rejected = perm.split_grants(perm.ROLE_SUPERADMIN, {"assets.view"})
    assert ok == frozenset() and rejected == {"assets.view"}  # süper admin için satır tutulmaz


def test_seniority_has_no_influence_on_permissions():
    assert "seniority" not in " ".join(pyinspect.signature(perm.effective_permissions).parameters)
    assert (perm.MIN_SENIORITY, perm.MAX_SENIORITY) == (1, 5) and len(perm.SENIORITY_LABELS) == 5


# --- Kullanıcı modeli ----------------------------------------------------------------------------------
def _user(app, username="u1", role="goruntuleyici", active=True, seniority=1, grants=()):
    with app.app_context():
        user = User(username=username, full_name="Test Kullanıcı", role=role, active=active, seniority_level=seniority)
        user.set_password("Test-Parola-123!")
        db.session.add(user)
        db.session.flush()
        user.set_permissions(grants)
        db.session.commit()
        return user.id


def test_high_seniority_grants_nothing(app):
    uid = _user(app, seniority=5)
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.seniority_level == 5
        assert user.effective_permissions == frozenset()
        assert not any(user.has_permission(k) for k in perm.PERMISSIONS)


def test_has_permission_reads_current_rows_and_respects_ceiling(app):
    uid = _user(app, role="goruntuleyici", grants={"assets.view", "vehicles.update"})
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.granted_permissions == {"assets.view", "vehicles.update"}
        assert user.has_permission("assets.view")
        assert not user.has_permission("vehicles.update")  # tavan dışı satır etkisiz
        user.set_permissions({"vehicles.view"})
        db.session.commit()
    with app.app_context():
        user = db.session.get(User, uid)  # yeni oturum: önbellek yok, güncel satırlar
        assert user.effective_permissions == {"vehicles.view"}


def test_inactive_user_has_no_permissions(app):
    uid = _user(app, role="birim_sorumlusu", active=False, grants={"assets.view"})
    with app.app_context():
        assert not db.session.get(User, uid).has_permission("assets.view")


def test_superadmin_has_all_permissions_without_rows(app):
    uid = _user(app, role="superadmin")
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.granted_permissions == frozenset()
        assert user.has_permission("users.manage") and user.has_permission("maintenance.void")


def test_set_permissions_rejects_users_manage_and_unknown_keys(app):
    uid = _user(app, role="birim_sorumlusu")
    with app.app_context():
        user = db.session.get(User, uid)
        for bad in ({"users.manage"}, {"bogus.key"}, {"assets.view", "users.manage"}):
            with pytest.raises(ValueError):
                user.set_permissions(bad)
        db.session.rollback()
        assert db.session.scalar(db.select(db.func.count()).select_from(UserPermission)) == 0


def test_orm_validator_rejects_users_manage_row(app):
    uid = _user(app)
    with app.app_context():
        with pytest.raises(ValueError, match="users.manage"):
            UserPermission(user_id=uid, permission="users.manage")


def test_database_rejects_users_manage_row_even_via_raw_sql(app):
    uid = _user(app)
    with app.app_context():
        with pytest.raises(IntegrityError):
            db.session.execute(text(
                "INSERT INTO user_permissions (user_id, permission, granted_at) VALUES (:u, 'users.manage', '2026-01-01')"),
                {"u": uid})
        db.session.rollback()
        with pytest.raises(IntegrityError):
            db.session.execute(text(
                "INSERT INTO user_permissions (user_id, permission, granted_at) VALUES (:u, '', '2026-01-01')"), {"u": uid})
        db.session.rollback()


def test_database_rejects_bad_roles_and_seniority(app):
    for role, level in (("admin", 1), ("staff", 1), ("bilinmeyen", 1), ("goruntuleyici", 0), ("goruntuleyici", 6)):
        with app.app_context():
            with pytest.raises(IntegrityError):
                db.session.execute(text(
                    "INSERT INTO users (username, full_name, password_hash, role, seniority_level, active, created_at, "
                    "updated_at) VALUES ('x', 'X', 'h', :r, :l, 1, '2026-01-01', '2026-01-01')"), {"r": role, "l": level})
            db.session.rollback()


# --- Tek aktif süper admin (veritabanı düzeyi) -----------------------------------------------------------------
def test_database_allows_only_one_active_superadmin(app):
    _user(app, "ilk", role="superadmin")
    with app.app_context():
        with pytest.raises(IntegrityError):  # ORM ile ikinci aktif süper admin
            db.session.add(User(username="ikinci", full_name="İkinci", role="superadmin", password_hash="h"))
            db.session.commit()
        db.session.rollback()
        with pytest.raises(IntegrityError):  # SQL ile bile
            db.session.execute(text(
                "INSERT INTO users (username, full_name, password_hash, role, active, created_at, updated_at) "
                "VALUES ('ucuncu', 'Üçüncü', 'h', 'superadmin', 1, '2026-01-01', '2026-01-01')"))
        db.session.rollback()
        db.session.add(User(username="diger", full_name="Diğer", role="goruntuleyici", password_hash="h"))
        db.session.commit()
        with pytest.raises(IntegrityError):  # mevcut bir kullanıcıyı ikinci süper admin yapmak
            db.session.execute(text("UPDATE users SET role = 'superadmin' WHERE username = 'diger'"))
        db.session.rollback()
        assert db.session.scalar(db.select(db.func.count()).select_from(User).where(User.role == "superadmin")) == 1


def test_inactive_superadmin_rows_may_coexist_but_cannot_be_activated(app):
    _user(app, "ilk", role="superadmin")
    pasif = _user(app, "pasif", role="superadmin", active=False)
    with app.app_context():
        with pytest.raises(IntegrityError):
            db.session.execute(text("UPDATE users SET active = 1 WHERE id = :i"), {"i": pasif})
        db.session.rollback()


def test_controlled_handover_sequence_is_allowed(app):
    old = _user(app, "eski", role="superadmin")
    new = _user(app, "yeni", role="birim_sorumlusu")
    with app.app_context():
        db.session.execute(text("UPDATE users SET role = 'birim_sorumlusu' WHERE id = :i"), {"i": old})
        db.session.execute(text("UPDATE users SET role = 'superadmin' WHERE id = :i"), {"i": new})
        db.session.commit()
        assert db.session.get(User, new).is_superadmin and not db.session.get(User, old).is_superadmin
