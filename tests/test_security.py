import pytest

from app import create_app
from app.extensions import db
from app.models.user import User
from tests.conftest import TEST_PASSWORD, get_csrf_token


def test_password_is_not_stored_in_plain_text(app, staff_id):
    with app.app_context():
        user = db.session.get(User, staff_id)
        assert user.password_hash != TEST_PASSWORD
        assert TEST_PASSWORD not in user.password_hash
        assert user.password_hash.startswith(("scrypt:", "pbkdf2:"))
        assert user.check_password(TEST_PASSWORD)
        assert not user.check_password("baska-parola")


def test_csrf_is_enabled_and_blocks_login_without_token(csrf_app, make_user):
    make_user(csrf_app, username="personel")
    client = csrf_app.test_client()
    response = client.post("/giris", data={"username": "personel", "password": TEST_PASSWORD})
    assert response.status_code == 400
    assert "Form doğrulaması başarısız" in response.get_data(as_text=True)


def test_csrf_token_allows_login_and_logout_requires_token(csrf_app, make_user):
    make_user(csrf_app, username="personel")
    client = csrf_app.test_client()
    token = get_csrf_token(client)
    response = client.post("/giris", data={"username": "personel", "password": TEST_PASSWORD, "csrf_token": token})
    assert response.status_code == 302
    assert client.post("/cikis").status_code == 400  # belirteçsiz çıkış reddedilir
    token = get_csrf_token(client, "/")
    assert client.post("/cikis", data={"csrf_token": token}).status_code == 302


def test_security_headers_are_present(client):
    response = client.get("/giris")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert response.headers["Cache-Control"] == "no-store"


def test_session_cookie_flags(client, staff_id):
    response = client.post("/giris", data={"username": "personel", "password": TEST_PASSWORD})
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_app_refuses_to_start_without_secret_key():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app("testing", {"SECRET_KEY": None})


def test_short_secret_key_rejected_outside_testing():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app("production", {"SECRET_KEY": "kisa", "SQLALCHEMY_DATABASE_URI": "sqlite://", "LOG_TO_FILE": False})


def test_production_config_is_hardened():
    app = create_app(
        "production",
        {"SECRET_KEY": "x" * 40, "SQLALCHEMY_DATABASE_URI": "sqlite://", "LOG_TO_FILE": False},
    )
    assert app.config["DEBUG"] is False
    assert app.config["SESSION_COOKIE_SECURE"] is True
    response = app.test_client().get("/giris")  # HTTP -> HTTPS yönlendirmesi
    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://")


def test_404_page_is_friendly_and_leaks_nothing(client):
    response = client.get("/olmayan-sayfa")
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    assert "Sayfa bulunamadı" in html
    assert "Traceback" not in html


def test_413_for_oversized_request():
    app = create_app("testing", {"MAX_CONTENT_LENGTH": 100})
    response = app.test_client().post("/giris", data={"username": "a" * 500, "password": "x"})
    assert response.status_code == 413
    assert "İstek çok büyük" in response.get_data(as_text=True)


def test_500_page_hides_error_details():
    app = create_app("testing", {"PROPAGATE_EXCEPTIONS": False})

    @app.route("/test-hata")
    def boom():
        raise ValueError("cok-gizli-ayrinti")

    response = app.test_client().get("/test-hata")
    assert response.status_code == 500
    html = response.get_data(as_text=True)
    assert "cok-gizli-ayrinti" not in html
    assert "Traceback" not in html
