"""Aşama 5.2: `transfer-superadmin` komutu — atomik, denetlenebilir süper admin devri; `create-admin` kuralları."""
import pytest

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.permission import UserPermission
from app.models.user import User
from app.permissions import (
    GRANTABLE,
    ROLE_BAKIM_PERSONELI,
    ROLE_BIRIM_SORUMLUSU,
    ROLE_CEILINGS,
    ROLE_GORUNTULEYICI,
    ROLE_SUPERADMIN,
)
from tests.authz_helpers import make_profile
from tests.conftest import TEST_PASSWORD, login


def run(app, *args, **kwargs):
    return app.test_cli_runner().invoke(args=list(args), **kwargs)


def snapshot(app):
    with app.app_context():
        return {
            "users": db.session.execute(db.text(
                "SELECT id, username, role, seniority_level, active FROM users ORDER BY id")).all(),
            "grants": db.session.execute(db.text(
                "SELECT user_id, permission FROM user_permissions ORDER BY user_id, permission")).all(),
            "audit": db.session.scalar(db.select(db.func.count()).select_from(AuditLog)),
        }


def active_superadmins(app):
    with app.app_context():
        return [u.username for u in db.session.scalars(
            db.select(User).where(User.role == ROLE_SUPERADMIN, User.active.is_(True)))]


@pytest.fixture()
def pair(app):
    old = make_profile(app, "eskikok", ROLE_SUPERADMIN, seniority=5)
    new = make_profile(app, "yenikok", ROLE_GORUNTULEYICI, {"assets.view"}, seniority=3)
    return {"old": old, "new": new}


def test_transfer_moves_superadmin_atomically_and_keeps_exactly_one(app, pair):
    result = run(app, "transfer-superadmin", "--to", "yenikok", "--yes")
    assert result.exit_code == 0, result.output
    assert "eskikok -> yenikok" in result.output
    assert active_superadmins(app) == ["yenikok"]
    with app.app_context():
        old, new = db.session.get(User, pair["old"]), db.session.get(User, pair["new"])
        assert (old.role, old.seniority_level, old.active) == (ROLE_BIRIM_SORUMLUSU, 5, True)
        assert old.granted_permissions == GRANTABLE  # iş kesintisi olmasın: rol tavanındaki tüm izinler
        assert (new.role, new.seniority_level, new.active) == (ROLE_SUPERADMIN, 3, True)
        assert new.granted_permissions == frozenset()  # süper adminin tabloda izin satırı tutulmaz
        assert new.has_permission("users.manage") and not old.has_permission("users.manage")
        assert db.session.scalar(db.select(db.func.count()).select_from(UserPermission).where(
            UserPermission.permission == "users.manage")) == 0


def test_transfer_writes_audit_entries_without_secrets(app, pair):
    run(app, "transfer-superadmin", "--to", "yenikok", "--yes")
    with app.app_context():
        entries = db.session.scalars(db.select(AuditLog).order_by(AuditLog.id)).all()
        actions = [e.action for e in entries]
        assert actions.count("user_role_changed") == 2 and actions.count("superadmin_transferred") == 1
        transferred = next(e for e in entries if e.action == "superadmin_transferred")
        assert "eskikok -> yenikok" in transferred.details and transferred.entity_id == pair["new"]
        assert transferred.user_id == pair["old"]
        for entry in entries:
            assert TEST_PASSWORD not in (entry.details or "") and "scrypt" not in (entry.details or "")


def test_transfer_with_custom_former_role_gives_that_roles_ceiling(app, pair):
    assert run(app, "transfer-superadmin", "--to", "yenikok", "--former-role", ROLE_BAKIM_PERSONELI,
               "--yes").exit_code == 0
    with app.app_context():
        old = db.session.get(User, pair["old"])
        assert old.role == ROLE_BAKIM_PERSONELI and old.granted_permissions == ROLE_CEILINGS[ROLE_BAKIM_PERSONELI]


def test_former_role_superadmin_is_rejected(app, pair):
    before = snapshot(app)
    assert run(app, "transfer-superadmin", "--to", "yenikok", "--former-role", "superadmin", "--yes").exit_code != 0
    assert snapshot(app) == before


def test_confirmation_prompt_can_cancel_without_changes(app, pair):
    before = snapshot(app)
    result = run(app, "transfer-superadmin", "--to", "yenikok", input="n\n")
    assert result.exit_code != 0 and "iptal" in result.output.lower()
    assert snapshot(app) == before
    assert run(app, "transfer-superadmin", "--to", "yenikok", input="y\n").exit_code == 0
    assert active_superadmins(app) == ["yenikok"]


@pytest.mark.parametrize("target, message", [
    ("yok.kisi", "bulunamadı"), ("eskikok", "zaten süper admin"), ("pasif", "devre dışı"),
])
def test_invalid_targets_are_refused_and_nothing_changes(app, pair, target, message):
    make_profile(app, "pasif", ROLE_BIRIM_SORUMLUSU, set(), active=False)
    before = snapshot(app)
    result = run(app, "transfer-superadmin", "--to", target, "--yes")
    assert result.exit_code != 0 and message in result.output
    assert snapshot(app) == before and active_superadmins(app) == ["eskikok"]


def test_transfer_without_an_active_superadmin_is_refused(app):
    make_profile(app, "eskikok", ROLE_SUPERADMIN, active=False)
    make_profile(app, "yenikok", ROLE_BIRIM_SORUMLUSU, set())
    before = snapshot(app)
    result = run(app, "transfer-superadmin", "--to", "yenikok", "--yes")
    assert result.exit_code != 0 and "tam olarak bir aktif süper admin" in result.output
    assert snapshot(app) == before


def test_failure_in_the_middle_leaves_no_half_transfer(app, pair, monkeypatch):
    """Denetim kaydı yazılırken hata olursa (roller ve izinler zaten değişmişken) tüm işlem geri alınır."""
    from app.services import users as users_service

    real = users_service.log_event

    def failing(action, **kwargs):
        if action == "superadmin_transferred":
            raise RuntimeError("denetim yazılamadı")
        return real(action, **kwargs)

    monkeypatch.setattr(users_service, "log_event", failing)
    before = snapshot(app)
    result = run(app, "transfer-superadmin", "--to", "yenikok", "--yes")
    assert result.exit_code != 0
    assert snapshot(app) == before and active_superadmins(app) == ["eskikok"]


def test_failed_post_condition_rolls_back(app, pair, monkeypatch):
    from app.services import users as users_service

    monkeypatch.setattr(users_service, "_active_superadmin_count", lambda: 2)
    before = snapshot(app)
    result = run(app, "transfer-superadmin", "--to", "yenikok", "--yes")
    assert result.exit_code != 0 and "iptal edildi" in result.output
    assert snapshot(app) == before


def test_old_session_loses_admin_rights_immediately_and_new_superadmin_gains_them(app, pair):
    old_client, new_client = app.test_client(), app.test_client()
    login(old_client, "eskikok")
    login(new_client, "yenikok")
    assert old_client.get("/yonetim/kullanicilar").status_code == 200
    assert new_client.get("/yonetim/kullanicilar").status_code == 403
    run(app, "transfer-superadmin", "--to", "yenikok", "--yes")
    assert old_client.get("/yonetim/kullanicilar").status_code == 403
    assert old_client.get("/demirbaslar/").status_code == 200  # çalışma yetkileri korunur
    assert new_client.get("/yonetim/kullanicilar").status_code == 200


def test_transfer_can_be_reversed(app, pair):
    assert run(app, "transfer-superadmin", "--to", "yenikok", "--yes").exit_code == 0
    assert run(app, "transfer-superadmin", "--to", "eskikok", "--yes").exit_code == 0
    assert active_superadmins(app) == ["eskikok"]
    with app.app_context():
        assert db.session.get(User, pair["old"]).granted_permissions == frozenset()
        assert db.session.get(User, pair["new"]).role == ROLE_BIRIM_SORUMLUSU


def test_database_rejects_a_second_active_superadmin_even_during_a_crafted_sql_transfer(app, pair):
    with app.app_context():
        with pytest.raises(Exception):
            db.session.execute(db.text("UPDATE users SET role='superadmin' WHERE username='yenikok'"))
            db.session.commit()
        db.session.rollback()
    assert active_superadmins(app) == ["eskikok"]


# --- create-admin -----------------------------------------------------------------------------------
def test_create_admin_refuses_when_an_active_superadmin_exists(app, pair):
    before = snapshot(app)
    result = run(app, "create-admin", "--username", "ucuncu", "--full-name", "Üçüncü", "--password", TEST_PASSWORD)
    assert result.exit_code != 0 and "Zaten bir yönetici" in result.output and "transfer-superadmin" in result.output
    assert snapshot(app) == before


def test_create_admin_is_allowed_when_only_an_inactive_superadmin_exists(app):
    make_profile(app, "eskikok", ROLE_SUPERADMIN, active=False)
    result = run(app, "create-admin", "--username", "yenikok", "--full-name", "Yeni Kök", "--password", TEST_PASSWORD)
    assert result.exit_code == 0, result.output
    assert active_superadmins(app) == ["yenikok"]
