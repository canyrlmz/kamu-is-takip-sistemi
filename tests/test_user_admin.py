"""Aşama 5.2: personel yönetimi (rol, kıdem, izin, aktiflik), süper admin güvenliği ve denetim kayıtları."""
import pytest

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.permission import UserPermission
from app.models.user import User
from app.permissions import (
    GRANTABLE,
    ROLE_ARIZA_KONTROL,
    ROLE_BAKIM_PERSONELI,
    ROLE_BIRIM_SORUMLUSU,
    ROLE_GORUNTULEYICI,
    ROLE_SUPERADMIN,
)
from tests.authz_helpers import make_profile
from tests.conftest import TEST_PASSWORD, get_csrf_token, login

PASSWORD = "Yeni-Hesap-Parola-77!"


def new_data(**overrides):
    data = {"username": "yeni.kisi", "full_name": "Yeni Kişi", "password": PASSWORD, "password_confirm": PASSWORD,
            "role": ROLE_BAKIM_PERSONELI, "seniority_level": "3",
            "permissions": ["vehicles.view", "maintenance.view", "maintenance.create"]}
    data.update(overrides)
    return data


def edit_data(**overrides):
    data = {"full_name": "Değişik Ad", "role": ROLE_BAKIM_PERSONELI, "seniority_level": "2",
            "permissions": ["vehicles.view"], "active": "y"}
    data.update(overrides)
    return data


def user(app, username):
    with app.app_context():
        u = db.session.scalar(db.select(User).where(User.username == username))
        if u is not None:
            db.session.expunge(u)
        return u


def perms_of(app, username):
    with app.app_context():
        u = db.session.scalar(db.select(User).where(User.username == username))
        return set(u.granted_permissions)


def audit(app, *actions):
    with app.app_context():
        stmt = db.select(AuditLog).order_by(AuditLog.id)
        if actions:
            stmt = stmt.where(AuditLog.action.in_(actions))
        return [(e.action, e.user_id, e.entity_id, e.details) for e in db.session.scalars(stmt)]


def count_users(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count()).select_from(User))


@pytest.fixture()
def root(app):
    return make_profile(app, "kok", ROLE_SUPERADMIN)


@pytest.fixture()
def admin_client(app, root):
    client = app.test_client()
    assert login(client, "kok").status_code == 302
    return client


# --- Oluşturma ---------------------------------------------------------------------------------------
def test_create_account_with_role_seniority_and_permissions(app, admin_client, root):
    response = admin_client.post("/yonetim/kullanicilar/yeni", data=new_data())
    assert response.status_code == 302
    created = user(app, "yeni.kisi")
    assert (created.role, created.seniority_level, created.active) == (ROLE_BAKIM_PERSONELI, 3, True)
    assert perms_of(app, "yeni.kisi") == {"vehicles.view", "maintenance.view", "maintenance.create"}
    created_log = audit(app, "user_created")
    assert len(created_log) == 1 and created_log[0][1] == root
    assert "rol: bakim_personeli" in created_log[0][3] and "kıdem: 3" in created_log[0][3]
    granted_log = audit(app, "user_permissions_changed")
    assert len(granted_log) == 1 and "maintenance.create" in granted_log[0][3]
    for _, _, _, details in audit(app):
        assert PASSWORD not in (details or "") and "scrypt" not in (details or "")


@pytest.mark.parametrize("seniority", ["0", "6", "-1", "abc", "", "3.5"])
def test_invalid_seniority_is_rejected(app, admin_client, seniority):
    response = admin_client.post("/yonetim/kullanicilar/yeni", data=new_data(seniority_level=seniority))
    assert response.status_code == 200
    assert user(app, "yeni.kisi") is None and "user_created" not in [a[0] for a in audit(app)]


@pytest.mark.parametrize("level", ["1", "2", "3", "4", "5"])
def test_every_seniority_level_is_accepted(app, admin_client, level):
    assert admin_client.post("/yonetim/kullanicilar/yeni", data=new_data(seniority_level=level)).status_code == 302
    assert user(app, "yeni.kisi").seniority_level == int(level)


def test_permissions_above_role_ceiling_are_rejected_not_silently_dropped(app, admin_client):
    response = admin_client.post("/yonetim/kullanicilar/yeni", data=new_data(
        role=ROLE_GORUNTULEYICI, permissions=["assets.view", "assets.create"]))
    assert response.status_code == 200
    assert "verilemeyen izinler" in response.get_data(as_text=True)
    assert user(app, "yeni.kisi") is None and count_users(app) == 1


@pytest.mark.parametrize("role", [ROLE_BIRIM_SORUMLUSU, ROLE_BAKIM_PERSONELI, ROLE_ARIZA_KONTROL, ROLE_GORUNTULEYICI])
def test_users_manage_can_never_be_assigned_to_any_role(app, admin_client, role):
    response = admin_client.post("/yonetim/kullanicilar/yeni", data=new_data(
        role=role, permissions=["users.manage"]))
    assert response.status_code == 200 and user(app, "yeni.kisi") is None
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(UserPermission)) == 0


@pytest.mark.parametrize("junk", ["assets.typo", "users.manage", "", "ASSETS.VIEW"])
def test_unknown_permission_keys_are_rejected(app, admin_client, junk):
    response = admin_client.post("/yonetim/kullanicilar/yeni", data=new_data(permissions=["vehicles.view", junk]))
    assert response.status_code == 200 and user(app, "yeni.kisi") is None


def test_high_seniority_grants_nothing(app, client):
    make_profile(app, "kidemli", ROLE_BIRIM_SORUMLUSU, set(), seniority=5)
    login(client, "kidemli")
    for url in ("/yonetim/kullanicilar", "/yonetim/denetim-kayitlari", "/demirbaslar/", "/araclar/", "/talepler/"):
        assert client.get(url).status_code == 403, url
    assert client.post("/yonetim/kullanicilar/yeni", data=new_data()).status_code == 403


# --- Düzenleme ---------------------------------------------------------------------------------------
def test_edit_changes_role_seniority_permissions_name_and_writes_each_audit_entry(app, admin_client, root):
    target = make_profile(app, "hedef", ROLE_ARIZA_KONTROL, {"tickets.view", "tickets.create"}, seniority=1)
    response = admin_client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data())
    assert response.status_code == 302
    changed = user(app, "hedef")
    assert (changed.full_name, changed.role, changed.seniority_level) == ("Değişik Ad", ROLE_BAKIM_PERSONELI, 2)
    assert perms_of(app, "hedef") == {"vehicles.view"}
    log = {a[0]: a for a in audit(app, "user_updated", "user_role_changed", "user_seniority_changed")}
    assert set(log) == {"user_updated", "user_role_changed", "user_seniority_changed"}
    assert "ariza_kontrol -> bakim_personeli" in log["user_role_changed"][3]
    assert "1 -> 2" in log["user_seniority_changed"][3]
    perm_log = audit(app, "user_permissions_changed")
    joined = " | ".join(p[3] for p in perm_log)
    assert "eklenen: vehicles.view" in joined and "çıkarılan: tickets.create, tickets.view" in joined
    assert all(e[1] == root and e[2] == target for e in log.values())


def test_edit_without_changes_writes_nothing(app, admin_client):
    target = make_profile(app, "hedef", ROLE_BAKIM_PERSONELI, {"vehicles.view"}, seniority=2, full_name="Değişik Ad")
    before = len(audit(app))
    response = admin_client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data(), follow_redirects=True)
    assert "Herhangi bir değişiklik yapılmadı" in response.get_data(as_text=True)
    assert len(audit(app)) == before


def test_role_change_with_permissions_not_valid_for_new_role_changes_nothing(app, admin_client):
    target = make_profile(app, "hedef", ROLE_BIRIM_SORUMLUSU, {"assets.create", "assets.view"}, seniority=4)
    response = admin_client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data(
        role=ROLE_GORUNTULEYICI, permissions=["assets.view", "assets.create"], full_name="Yeni Ad"))
    assert response.status_code == 200 and "verilemeyen izinler" in response.get_data(as_text=True)
    unchanged = user(app, "hedef")
    assert (unchanged.role, unchanged.full_name, unchanged.seniority_level) == (ROLE_BIRIM_SORUMLUSU, "Hedef", 4)
    assert perms_of(app, "hedef") == {"assets.create", "assets.view"}
    assert audit(app, "user_role_changed", "user_updated", "user_permissions_changed") == []


def test_edit_can_deactivate_and_reactivate_with_audit(app, admin_client):
    target = make_profile(app, "hedef", ROLE_BAKIM_PERSONELI, {"vehicles.view"}, seniority=2, full_name="Değişik Ad")
    admin_client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data(active=""))
    assert user(app, "hedef").active is False
    other = app.test_client()
    assert login(other, "hedef").status_code == 401
    admin_client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data())
    assert user(app, "hedef").active is True
    assert [a[0] for a in audit(app, "user_deactivated", "user_activated")] == ["user_deactivated", "user_activated"]


def test_permission_changes_take_effect_on_the_users_next_request(app, admin_client):
    target = make_profile(app, "hedef", ROLE_BAKIM_PERSONELI, {"vehicles.view"}, seniority=2, full_name="Değişik Ad")
    session = app.test_client()
    login(session, "hedef")
    assert session.get("/araclar/").status_code == 200
    admin_client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data(permissions=[]))
    assert session.get("/araclar/").status_code == 403  # önbellek yok: yetki anında geri alındı


def test_unknown_user_edit_returns_404(admin_client):
    assert admin_client.get("/yonetim/kullanicilar/9999/duzenle").status_code == 404
    assert admin_client.post("/yonetim/kullanicilar/9999/duzenle", data=edit_data()).status_code == 404


def test_all_grantable_permissions_fit_in_audit_entries_without_truncation(app, admin_client):
    target = make_profile(app, "hedef", ROLE_BIRIM_SORUMLUSU, set())
    admin_client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data(
        role=ROLE_BIRIM_SORUMLUSU, permissions=sorted(GRANTABLE)))
    entries = audit(app, "user_permissions_changed")
    assert len(entries) >= 2 and all(len(e[3]) <= 255 for e in entries)
    assert {p for e in entries for p in e[3].split("eklenen: ")[1].split(", ")} == GRANTABLE


# --- Süper admin güvenliği ---------------------------------------------------------------------------
def test_superadmin_edit_page_hides_role_permission_and_active_fields(admin_client, root):
    html = admin_client.get(f"/yonetim/kullanicilar/{root}/duzenle").get_data(as_text=True)
    assert 'name="full_name"' in html and 'name="seniority_level"' in html
    assert 'name="role"' not in html and 'name="permissions"' not in html and 'name="active"' not in html
    assert "transfer-superadmin" in html


def test_superadmin_cannot_demote_or_deactivate_self_through_a_crafted_request(app, admin_client, root):
    response = admin_client.post(f"/yonetim/kullanicilar/{root}/duzenle", data={
        "full_name": "Kök Yeni Ad", "seniority_level": "4", "role": ROLE_ARIZA_KONTROL,
        "permissions": ["tickets.view"], "active": ""})
    assert response.status_code == 302
    me = user(app, "kok")
    assert me.role == ROLE_SUPERADMIN and me.active is True  # gönderilen rol/aktiflik yok sayıldı
    assert (me.full_name, me.seniority_level) == ("Kök Yeni Ad", 4)  # ad ve kıdem değişebilir
    assert perms_of(app, "kok") == set()
    assert admin_client.get("/yonetim/kullanicilar").status_code == 200  # hâlâ yönetici
    assert audit(app, "user_role_changed", "user_deactivated") == []


def test_superadmin_cannot_deactivate_self_via_deactivate_route(app, admin_client, root):
    page = admin_client.post(f"/yonetim/kullanicilar/{root}/devre-disi", follow_redirects=True).get_data(as_text=True)
    assert "Son aktif yönetici" in page and user(app, "kok").active is True


def test_users_page_offers_no_deactivate_button_for_superadmin(admin_client, root):
    html = admin_client.get("/yonetim/kullanicilar").get_data(as_text=True)
    assert f"/yonetim/kullanicilar/{root}/devre-disi" not in html and "Tümü" in html


def test_second_active_superadmin_cannot_be_created_or_promoted_by_any_flow(app, admin_client, root):
    target = make_profile(app, "hedef", ROLE_BIRIM_SORUMLUSU, set())
    admin_client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data(role=ROLE_SUPERADMIN))
    assert user(app, "hedef").role == ROLE_BIRIM_SORUMLUSU
    admin_client.post("/yonetim/kullanicilar/yeni", data=new_data(role=ROLE_SUPERADMIN))
    assert user(app, "yeni.kisi") is None
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User).where(
            User.role == ROLE_SUPERADMIN, User.active.is_(True))) == 1


def test_non_superadmin_cannot_escalate_self_or_change_others(app, client):
    me = make_profile(app, "kisi", ROLE_BIRIM_SORUMLUSU, set(GRANTABLE), seniority=5)
    other = make_profile(app, "diger", ROLE_GORUNTULEYICI, set())
    login(client, "kisi")
    assert client.post(f"/yonetim/kullanicilar/{me}/duzenle", data=edit_data(role=ROLE_SUPERADMIN)).status_code == 403
    assert client.post(f"/yonetim/kullanicilar/{me}/duzenle", data=edit_data(permissions=["users.manage"])).status_code == 403
    assert client.post(f"/yonetim/kullanicilar/{other}/duzenle", data=edit_data()).status_code == 403
    assert client.post(f"/yonetim/kullanicilar/{other}/devre-disi").status_code == 403
    assert user(app, "kisi").role == ROLE_BIRIM_SORUMLUSU and perms_of(app, "kisi") == set(GRANTABLE)
    assert user(app, "diger").active is True and audit(app, "user_role_changed") == []


# --- CSRF ve parola ----------------------------------------------------------------------------------
def test_edit_requires_csrf_token_and_works_with_one(csrf_app):
    make_profile(csrf_app, "kok", ROLE_SUPERADMIN)
    target = make_profile(csrf_app, "hedef", ROLE_BAKIM_PERSONELI, {"vehicles.view"}, seniority=1)
    client = csrf_app.test_client()
    token = get_csrf_token(client)
    assert client.post("/giris", data={"username": "kok", "password": TEST_PASSWORD, "csrf_token": token}).status_code == 302
    assert client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data()).status_code == 400
    assert user(csrf_app, "hedef").seniority_level == 1
    token = get_csrf_token(client, f"/yonetim/kullanicilar/{target}/duzenle")
    assert client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data(csrf_token=token)).status_code == 302
    assert user(csrf_app, "hedef").seniority_level == 2


def test_password_hash_is_never_rendered(app, admin_client):
    target = make_profile(app, "hedef", ROLE_BAKIM_PERSONELI, set())
    for url in ("/yonetim/kullanicilar", f"/yonetim/kullanicilar/{target}/duzenle", "/yonetim/kullanicilar/yeni",
                "/yonetim/denetim-kayitlari"):
        html = admin_client.get(url).get_data(as_text=True)
        assert "scrypt" not in html and "pbkdf2" not in html and "password_hash" not in html
    html = admin_client.post("/yonetim/kullanicilar/yeni", data=new_data(password_confirm="uyusmuyor")).get_data(as_text=True)
    assert PASSWORD not in html


def test_audit_log_page_shows_readable_labels_for_new_actions(app, admin_client):
    target = make_profile(app, "hedef", ROLE_ARIZA_KONTROL, {"tickets.view"})
    admin_client.post(f"/yonetim/kullanicilar/{target}/duzenle", data=edit_data())
    html = admin_client.get("/yonetim/denetim-kayitlari").get_data(as_text=True)
    for label in ("Kullanıcı rolü değişti", "Kullanıcı kıdem seviyesi değişti", "Kullanıcı izinleri değişti"):
        assert label in html


def test_new_user_form_prechecks_default_role_preset_and_exposes_no_users_manage_checkbox(admin_client):
    html = admin_client.get("/yonetim/kullanicilar/yeni").get_data(as_text=True)
    assert 'value="users.manage"' not in html
    assert 'id="perm-tickets.update"' in html and "perm-config" in html
    assert 'value="superadmin"' not in html
