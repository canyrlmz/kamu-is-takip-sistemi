from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.user import User
from tests.conftest import TEST_PASSWORD, login


def test_anonymous_is_redirected_from_protected_pages(client):
    for url in ("/", "/yonetim/denetim-kayitlari"):
        response = client.get(url)
        assert response.status_code == 302
        assert "/giris" in response.headers["Location"]


def test_wrong_password_fails_with_generic_message(client, staff_id):
    response = login(client, "personel", "Yanlis-Parola-1!")
    assert response.status_code == 401
    assert "Kullanıcı adı veya parola hatalı." in response.get_data(as_text=True)
    assert client.get("/").status_code == 302  # oturum açılmadı


def test_unknown_user_gets_same_message_as_wrong_password(client, staff_id):
    unknown = login(client, "yok-boyle-biri").get_data(as_text=True)
    wrong = login(client, "personel", "Yanlis-Parola-1!").get_data(as_text=True)
    msg = "Kullanıcı adı veya parola hatalı."
    assert msg in unknown and msg in wrong


def test_successful_login_and_dashboard(client, staff_id):
    response = login(client, "personel")
    assert response.status_code == 302
    page = client.get("/")
    assert page.status_code == 200
    assert "Personel Örnek" in page.get_data(as_text=True)


def test_username_is_case_insensitive(client, staff_id):
    assert login(client, "  PERSONEL ").status_code == 302


def test_already_logged_in_user_is_redirected_from_login(client, staff_id):
    login(client, "personel")
    assert client.get("/giris").status_code == 302


def test_disabled_user_cannot_login(app, client, make_user):
    make_user(app, username="pasif", active=False)
    response = login(client, "pasif")
    assert response.status_code == 401
    assert client.get("/").status_code == 302


def test_existing_session_of_disabled_user_is_rejected(app, client, staff_id):
    login(client, "personel")
    assert client.get("/").status_code == 200
    with app.app_context():
        user = db.session.get(User, staff_id)
        user.active = False
        db.session.commit()
    assert client.get("/").status_code == 302


def test_open_redirect_is_blocked(client, staff_id):
    response = login(client, "personel")  # next yok
    assert response.headers["Location"].endswith("/")
    client.post("/cikis")
    for bad in ("https://evil.example/", "//evil.example", "/\\evil.example", "javascript:alert(1)"):
        response = client.post("/giris", query_string={"next": bad}, data={"username": "personel", "password": TEST_PASSWORD})
        assert "evil" not in response.headers["Location"]
        assert "javascript" not in response.headers["Location"]
        client.post("/cikis")


def test_safe_next_is_honoured(client, staff_id):
    response = client.post("/giris", query_string={"next": "/yonetim/denetim-kayitlari"},
                           data={"username": "personel", "password": TEST_PASSWORD})
    assert response.headers["Location"].endswith("/yonetim/denetim-kayitlari")


def test_login_form_validation_rejects_empty_fields(client):
    response = client.post("/giris", data={"username": "", "password": ""})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Kullanıcı adı zorunludur." in html
    assert "Parola zorunludur." in html


def test_login_form_rejects_overlong_input(client):
    response = client.post("/giris", data={"username": "a" * 500, "password": "x"})
    assert response.status_code == 200  # çökmez, doğrulama hatası döner


def test_logout_is_post_only_and_ends_session_for_both_roles(client, admin_id, staff_id):
    for username in ("yonetici", "personel"):
        login(client, username)
        assert client.get("/").status_code == 200
        assert client.get("/cikis").status_code == 405  # GET ile çıkış yok
        assert client.post("/cikis").status_code == 302
        assert client.get("/").status_code == 302


def test_login_failure_is_rate_limited(app):
    from app import create_app

    limited = create_app("testing", {"RATELIMIT_ENABLED": True, "LOGIN_RATE_LIMIT": "3 per minute"})
    with limited.app_context():
        db.create_all()
    c = limited.test_client()
    codes = [login(c, "yok", "Yanlis-1234567!").status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401]
    assert codes[3] == 429


def test_audit_entries_for_login_and_logout(app, client, staff_id):
    login(client, "personel", "Yanlis-Parola-1!")
    login(client, "yok-boyle-biri", "x")
    login(client, "personel")
    client.post("/cikis")
    with app.app_context():
        entries = db.session.scalars(db.select(AuditLog).order_by(AuditLog.id)).all()
        assert [e.action for e in entries] == ["login_failed", "login_failed", "login_success", "logout"]
        assert entries[0].user_id == staff_id
        assert entries[1].user_id is None
        # Girilen kullanıcı adı / parola loglanmaz
        assert all("yok-boyle-biri" not in (e.details or "") for e in entries)
        assert all(TEST_PASSWORD not in (e.details or "") for e in entries)
