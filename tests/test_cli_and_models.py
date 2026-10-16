import pytest

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.user import ROLE_ADMIN, User
from app.services.passwords import validate_password_strength, validate_username
from tests.conftest import TEST_PASSWORD


def run(app, *args, **kwargs):
    return app.test_cli_runner().invoke(args=list(args), **kwargs)


def test_create_admin_creates_hashed_admin_and_audit_entry(app):
    result = run(app, "create-admin", "--username", "kurulum", "--full-name", "Kurulum Yöneticisi", "--password", TEST_PASSWORD)
    assert result.exit_code == 0, result.output
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "kurulum"))
        assert user.role == ROLE_ADMIN
        assert user.password_hash != TEST_PASSWORD
        entry = db.session.scalar(db.select(AuditLog))
        assert entry.action == "user_created" and entry.entity_id == user.id


def test_create_admin_refuses_second_admin(app, admin_id):
    result = run(app, "create-admin", "--username", "ikinci", "--full-name", "İkinci", "--password", TEST_PASSWORD)
    assert result.exit_code != 0
    assert "Zaten bir yönetici" in result.output


def test_create_admin_rejects_weak_password(app):
    result = run(app, "create-admin", "--username", "kurulum", "--full-name", "Kurulum", "--password", "kisa")
    assert result.exit_code != 0
    with app.app_context():
        assert db.session.scalar(db.select(User)) is None


def test_create_admin_rejects_invalid_username(app):
    result = run(app, "create-admin", "--username", "Bad Name!", "--full-name", "X", "--password", TEST_PASSWORD)
    assert result.exit_code != 0


def test_create_admin_reads_password_from_environment(app):
    result = run(app, "create-admin", "--username", "envyonetici", "--full-name", "Env Yönetici",
                 env={"KARABUK_ADMIN_PASSWORD": TEST_PASSWORD})
    assert result.exit_code == 0, result.output


def test_create_staff_works_and_rejects_duplicates(app):
    args = ("create-staff", "--username", "personel1", "--full-name", "Personel Bir", "--password", TEST_PASSWORD)
    assert run(app, *args).exit_code == 0
    assert run(app, *args).exit_code != 0


def test_create_admin_gives_clear_hint_when_tables_missing():
    from app import create_app

    bare = create_app("testing")  # create_all çağrılmadı
    result = bare.test_cli_runner().invoke(
        args=["create-admin", "--username", "kurulum", "--full-name", "K", "--password", TEST_PASSWORD]
    )
    assert result.exit_code != 0
    assert "db upgrade" in result.output


@pytest.mark.parametrize("password", ["kisa1A!", "sadecekucukharfler1!", "SADECEBUYUKHARFLER1!", "BuyukKucukAmaRakamsiz!", "BuyukKucukRakam12345"])
def test_weak_passwords_are_rejected(password):
    assert validate_password_strength(password, "ayse")


def test_strong_password_is_accepted_and_username_not_allowed_inside():
    assert validate_password_strength(TEST_PASSWORD, "ayse") == []
    assert validate_password_strength("Ayse-Parola-123!", "ayse")


def test_username_validation():
    assert validate_username("ayse.yilmaz") == []
    assert validate_username("Ayşe") and validate_username("a") and validate_username("a b")


def test_audit_log_cannot_be_modified_or_deleted(app):
    with app.app_context():
        entry = AuditLog(action="login_success")
        db.session.add(entry)
        db.session.commit()
        entry.action = "baska"
        with pytest.raises(RuntimeError):
            db.session.commit()
        db.session.rollback()
        db.session.delete(db.session.scalar(db.select(AuditLog)))
        with pytest.raises(RuntimeError):
            db.session.commit()
        db.session.rollback()


def test_disabled_user_is_not_authenticated(app, make_user):
    uid = make_user(app, username="pasif", active=False)
    with app.app_context():
        assert db.session.get(User, uid).is_authenticated is False
