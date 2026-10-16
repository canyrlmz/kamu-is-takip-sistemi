import pytest

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.user import ROLE_ADMIN, ROLE_STAFF, User
from tests.conftest import TEST_PASSWORD, get_csrf_token, login

NEW_PASSWORD = "Yeni-Hesap-Parola-77!"


def new_user_data(**overrides):
    data = {
        "username": "yenipersonel",
        "full_name": "Yeni Personel",
        "password": NEW_PASSWORD,
        "password_confirm": NEW_PASSWORD,
    }
    data.update(overrides)
    return data


def get_user(app, username):
    with app.app_context():
        return db.session.scalar(db.select(User).where(User.username == username))


def audit_actions(app):
    with app.app_context():
        return [e.action for e in db.session.scalars(db.select(AuditLog).order_by(AuditLog.id)).all()]


# --- Erişim kontrolü ---------------------------------------------------------

USER_PAGES = ["/yonetim/kullanicilar", "/yonetim/kullanicilar/yeni"]


def test_anonymous_is_redirected_to_login(client):
    for url in USER_PAGES:
        response = client.get(url)
        assert response.status_code == 302 and "/giris" in response.headers["Location"]
    assert client.post("/yonetim/kullanicilar/yeni", data=new_user_data()).status_code == 302
    assert client.post("/yonetim/kullanicilar/1/devre-disi").status_code == 302
    assert client.post("/yonetim/kullanicilar/1/etkinlestir").status_code == 302


def test_staff_gets_403_everywhere(app, client, admin_id, staff_id):
    login(client, "personel")
    for url in USER_PAGES:
        assert client.get(url).status_code == 403
    assert client.post("/yonetim/kullanicilar/yeni", data=new_user_data()).status_code == 403
    assert client.post(f"/yonetim/kullanicilar/{admin_id}/devre-disi").status_code == 403
    assert client.post(f"/yonetim/kullanicilar/{staff_id}/etkinlestir").status_code == 403
    assert get_user(app, "yenipersonel") is None
    assert get_user(app, "yonetici").active is True


def test_sidebar_link_only_for_admin(client, admin_id, staff_id):
    login(client, "personel")
    assert "/yonetim/kullanicilar" not in client.get("/").get_data(as_text=True)
    client.post("/cikis")
    login(client, "yonetici")
    assert "/yonetim/kullanicilar" in client.get("/").get_data(as_text=True)


def test_admin_sees_user_list(client, admin_id, staff_id):
    login(client, "yonetici")
    response = client.get("/yonetim/kullanicilar")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "personel" in html and "yonetici" in html
    assert "password_hash" not in html and "scrypt" not in html


def test_user_list_pagination_handles_bad_page_numbers(client, admin_id):
    login(client, "yonetici")
    for value in ("abc", "-5", "9999"):
        assert client.get("/yonetim/kullanicilar", query_string={"sayfa": value}).status_code == 200


# --- Hesap oluşturma ---------------------------------------------------------

def test_admin_creates_staff_account(app, client, admin_id):
    login(client, "yonetici")
    response = client.post("/yonetim/kullanicilar/yeni", data=new_user_data())
    assert response.status_code == 302
    user = get_user(app, "yenipersonel")
    assert user.role == ROLE_STAFF and user.active is True
    assert user.password_hash != NEW_PASSWORD and NEW_PASSWORD not in user.password_hash
    assert user.password_hash.startswith(("scrypt:", "pbkdf2:"))
    assert user.check_password(NEW_PASSWORD)
    with app.app_context():
        entry = db.session.scalars(db.select(AuditLog).where(AuditLog.action == "user_created")).one()
        assert entry.user_id == admin_id and entry.entity_id == user.id and entry.entity_type == "user"
        assert NEW_PASSWORD not in (entry.details or "")


def test_new_account_can_log_in_but_is_not_admin(client, app, admin_id):
    login(client, "yonetici")
    client.post("/yonetim/kullanicilar/yeni", data=new_user_data())
    client.post("/cikis")
    assert login(client, "yenipersonel", NEW_PASSWORD).status_code == 302
    assert client.get("/yonetim/kullanicilar").status_code == 403


def test_account_without_role_field_defaults_to_non_privileged_role(app, client, admin_id):
    # Aşama 5: formda artık rol seçimi vardır; alan gönderilmezse hesap yetkisiz varsayılan rolle açılır.
    login(client, "yonetici")
    client.post("/yonetim/kullanicilar/yeni", data=new_user_data(is_admin="1"))
    user = get_user(app, "yenipersonel")
    assert user.role == ROLE_STAFF and user.effective_permissions == frozenset()  # izinler açıkça seçilmedikçe verilmez


@pytest.mark.parametrize("role_value", ["superadmin", "SUPERADMIN", "admin", "ADMIN", "staff", "1", "yonetici"])
def test_privileged_role_cannot_be_chosen_through_request(app, client, admin_id, role_value):
    # Eski testin amacı korunur: istekle süper admin (veya geçersiz/eski bir rol) verilemez. Hiçbir hesap açılmaz,
    # ikinci süper admin oluşmaz.
    login(client, "yonetici")
    client.post("/yonetim/kullanicilar/yeni", data=new_user_data(role=role_value, is_admin="1"))
    assert get_user(app, "yenipersonel") is None
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 1
        assert db.session.scalar(
            db.select(db.func.count()).select_from(User).where(User.role == ROLE_ADMIN)) == 1
    assert "user_created" not in audit_actions(app)


def test_duplicate_username_is_rejected_case_insensitively(app, client, admin_id, staff_id):
    login(client, "yonetici")
    response = client.post("/yonetim/kullanicilar/yeni", data=new_user_data(username="  PERSONEL "))
    assert response.status_code == 200
    assert "zaten kullanılıyor" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 2


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"password": "kisa1A!", "password_confirm": "kisa1A!"}, "en az 12 karakter"),
        ({"password": "sadecekucukharfler1!", "password_confirm": "sadecekucukharfler1!"}, "büyük harf"),
        ({"password_confirm": "Baska-Parola-88!"}, "eşleşmiyor"),
        ({"username": "Bad Name!"}, "Kullanıcı adı"),
        ({"username": "ab"}, "Kullanıcı adı"),
        ({"full_name": ""}, "Ad soyad zorunludur"),
        ({"full_name": "x" * 121}, "En fazla 120"),
        ({"password": "Ayse-Parola-123!x", "password_confirm": "Ayse-Parola-123!x", "username": "ayse"}, "kullanıcı adını"),
    ],
)
def test_invalid_input_is_rejected_and_nothing_is_created(app, client, admin_id, overrides, message):
    login(client, "yonetici")
    response = client.post("/yonetim/kullanicilar/yeni", data=new_user_data(**overrides))
    assert response.status_code == 200
    assert message in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 1
    assert "user_created" not in audit_actions(app)


def test_password_is_never_echoed_back(client, admin_id):
    login(client, "yonetici")
    html = client.post("/yonetim/kullanicilar/yeni", data=new_user_data(password_confirm="uyusmuyor")).get_data(as_text=True)
    assert NEW_PASSWORD not in html


# --- Etkinleştirme / devre dışı bırakma -------------------------------------

def test_deactivate_and_reactivate_staff_with_audit(app, client, admin_id, staff_id):
    login(client, "yonetici")
    assert client.post(f"/yonetim/kullanicilar/{staff_id}/devre-disi").status_code == 302
    assert get_user(app, "personel").active is False

    other = app.test_client()
    assert login(other, "personel").status_code == 401  # devre dışı hesap giriş yapamaz

    assert client.post(f"/yonetim/kullanicilar/{staff_id}/etkinlestir").status_code == 302
    assert get_user(app, "personel").active is True
    assert login(other, "personel").status_code == 302

    with app.app_context():
        entries = db.session.scalars(
            db.select(AuditLog).where(AuditLog.action.in_(["user_deactivated", "user_activated"])).order_by(AuditLog.id)
        ).all()
        assert [e.action for e in entries] == ["user_deactivated", "user_activated"]
        assert all(e.user_id == admin_id and e.entity_id == staff_id for e in entries)


def test_deactivation_ends_active_session_of_target(app, client, admin_id, staff_id):
    staff_client = app.test_client()
    login(staff_client, "personel")
    assert staff_client.get("/").status_code == 200
    login(client, "yonetici")
    client.post(f"/yonetim/kullanicilar/{staff_id}/devre-disi")
    assert staff_client.get("/").status_code == 302


def test_last_active_admin_cannot_be_deactivated(app, client, admin_id):
    login(client, "yonetici")
    response = client.post(f"/yonetim/kullanicilar/{admin_id}/devre-disi", follow_redirects=True)
    assert "Son aktif yönetici" in response.get_data(as_text=True)
    assert get_user(app, "yonetici").active is True
    assert "user_deactivated" not in audit_actions(app)
    assert client.get("/yonetim/kullanicilar").status_code == 200  # oturum hâlâ geçerli


def test_sole_active_superadmin_cannot_be_deactivated(app, client, admin_id, make_user):
    # Aşama 5: sistemde tek aktif süper admin vardır (ikincisi veritabanı düzeyinde de engellenir).
    # Eski kural "son aktif yönetici devre dışı bırakılamaz" bu durumda süper admin için her zaman geçerlidir.
    second_id = make_user(app, username="yonetici2", role=ROLE_ADMIN, full_name="İkinci Yönetici", active=False)
    login(client, "yonetici")
    client.post(f"/yonetim/kullanicilar/{admin_id}/devre-disi")
    assert get_user(app, "yonetici").active is True
    # Pasif süper admin kaydı etkinleştirilemez: ikinci aktif süper admin kuralı ihlal edilirdi
    page = client.post(f"/yonetim/kullanicilar/{second_id}/etkinlestir", follow_redirects=True).get_data(as_text=True)
    assert "yalnızca bir aktif süper admin" in page
    assert get_user(app, "yonetici2").active is False


def test_inactive_second_admin_does_not_count_as_active_admin(app, client, admin_id, make_user):
    make_user(app, username="pasifyonetici", role=ROLE_ADMIN, active=False)
    login(client, "yonetici")
    client.post(f"/yonetim/kullanicilar/{admin_id}/devre-disi")
    assert get_user(app, "yonetici").active is True


def test_state_changes_require_post(client, admin_id, staff_id, app):
    login(client, "yonetici")
    assert client.get(f"/yonetim/kullanicilar/{staff_id}/devre-disi").status_code == 405
    assert client.get(f"/yonetim/kullanicilar/{staff_id}/etkinlestir").status_code == 405
    assert get_user(app, "personel").active is True


def test_unknown_user_id_returns_404(client, admin_id):
    login(client, "yonetici")
    assert client.post("/yonetim/kullanicilar/99999/devre-disi").status_code == 404
    assert client.post("/yonetim/kullanicilar/99999/etkinlestir").status_code == 404


def test_repeating_an_action_is_harmless(app, client, admin_id, staff_id):
    login(client, "yonetici")
    client.post(f"/yonetim/kullanicilar/{staff_id}/etkinlestir")  # zaten etkin
    client.post(f"/yonetim/kullanicilar/{staff_id}/devre-disi")
    client.post(f"/yonetim/kullanicilar/{staff_id}/devre-disi")  # zaten pasif
    assert audit_actions(app).count("user_deactivated") == 1
    assert "user_activated" not in audit_actions(app)


# --- CSRF --------------------------------------------------------------------

def _csrf_login(app, make_user):
    make_user(app, username="yonetici", role=ROLE_ADMIN)
    client = app.test_client()
    token = get_csrf_token(client)
    response = client.post("/giris", data={"username": "yonetici", "password": TEST_PASSWORD, "csrf_token": token})
    assert response.status_code == 302
    return client


def test_user_management_posts_require_csrf_token(csrf_app, make_user):
    staff_id = make_user(csrf_app, username="personel")
    client = _csrf_login(csrf_app, make_user)

    assert client.post("/yonetim/kullanicilar/yeni", data=new_user_data()).status_code == 400
    assert client.post(f"/yonetim/kullanicilar/{staff_id}/devre-disi").status_code == 400
    assert get_user(csrf_app, "yenipersonel") is None
    assert get_user(csrf_app, "personel").active is True


def test_user_management_works_with_valid_csrf_token(csrf_app, make_user):
    staff_id = make_user(csrf_app, username="personel")
    client = _csrf_login(csrf_app, make_user)

    token = get_csrf_token(client, "/yonetim/kullanicilar/yeni")
    response = client.post("/yonetim/kullanicilar/yeni", data=new_user_data(csrf_token=token))
    assert response.status_code == 302
    assert get_user(csrf_app, "yenipersonel").role == ROLE_STAFF

    token = get_csrf_token(client, "/yonetim/kullanicilar")
    response = client.post(f"/yonetim/kullanicilar/{staff_id}/devre-disi", data={"csrf_token": token})
    assert response.status_code == 302
    assert get_user(csrf_app, "personel").active is False
