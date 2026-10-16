import pytest

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.vehicle import Vehicle
from app.models.user import ROLE_ADMIN
from tests.conftest import get_csrf_token, login
from tests.vehicle_helpers import (
    add_record, audit_actions, counts, make_vehicle, vehicle_data,
)


def _get(app, vid):
    with app.app_context():
        return db.session.get(Vehicle, vid)


# --- Erişim ve yetki -----------------------------------------------------------
def test_anonymous_is_redirected_everywhere(app, client, admin_id):
    vid = make_vehicle(app, admin_id)
    for url in ("/araclar/", "/araclar/yeni", f"/araclar/{vid}", f"/araclar/{vid}/duzenle",
                f"/araclar/{vid}/bakim-ekle", "/araclar/yaklasan-bakimlar"):
        response = client.get(url)
        assert response.status_code == 302 and "/giris" in response.headers["Location"], url
    for url in ("/araclar/yeni", f"/araclar/{vid}/duzenle", f"/araclar/{vid}/bakim-ekle",
                f"/araclar/{vid}/arsivle", f"/araclar/{vid}/geri-al"):
        assert client.post(url, data=vehicle_data()).status_code == 302, url
    assert counts(app) == (1, 0)


def test_staff_can_view_but_not_change(app, client, admin_id, staff_id):
    vid = make_vehicle(app, admin_id)
    rid = add_record(app, vid, admin_id, next_in_days=100)
    login(client, "personel")
    for url in ("/araclar/", f"/araclar/{vid}", "/araclar/yaklasan-bakimlar"):
        assert client.get(url).status_code == 200, url
    page = client.get(f"/araclar/{vid}").get_data(as_text=True)
    assert "Bakım ekle" not in page and "Düzenle" not in page and "İptal et" not in page
    before_audit = audit_actions(app)
    for url in ("/araclar/yeni", f"/araclar/{vid}/duzenle", f"/araclar/{vid}/bakim-ekle",
                f"/araclar/{vid}/bakim/{rid}/iptal"):
        assert client.get(url).status_code == 403, url
        assert client.post(url, data=vehicle_data(identifier="YENI-1")).status_code == 403, url
    for url in (f"/araclar/{vid}/arsivle", f"/araclar/{vid}/geri-al"):
        assert client.post(url).status_code == 403, url
    assert counts(app) == (1, 1)
    assert audit_actions(app) == before_audit  # yetkisiz denemeler değişiklik ve denetim kaydı bırakmaz


def test_missing_vehicle_returns_404(app, client, admin_id):
    login(client, "yonetici")
    for url in ("/araclar/999", "/araclar/999/duzenle", "/araclar/999/bakim-ekle"):
        assert client.get(url).status_code == 404, url
    assert client.post("/araclar/999/arsivle").status_code == 404


def test_posts_require_csrf(csrf_app, make_user):
    make_user(csrf_app, username="yonetici", role=ROLE_ADMIN)
    client = csrf_app.test_client()
    token = get_csrf_token(client)
    client.post("/giris", data={"username": "yonetici", "password": "Test-Parola-123!", "csrf_token": token})
    assert client.post("/araclar/yeni", data=vehicle_data()).status_code == 400
    assert counts(csrf_app) == (0, 0)
    token = get_csrf_token(client, "/araclar/yeni")
    response = client.post("/araclar/yeni", data={**vehicle_data(), "csrf_token": token})
    assert response.status_code == 302 and counts(csrf_app) == (1, 0)


# --- Oluşturma, listeleme, arama -------------------------------------------------
def test_admin_creates_vehicle_and_it_is_listed_with_normalized_identifier(app, client, admin_id):
    login(client, "yonetici")
    response = client.post("/araclar/yeni", data=vehicle_data(identifier=" 78 abc 123 "))
    assert response.status_code == 302
    with app.app_context():
        vehicle = db.session.scalar(db.select(Vehicle))
        assert vehicle.identifier == "78ABC123" and vehicle.created_by_id == admin_id
        assert vehicle.model_year == 2020 and vehicle.current_meter == 10000 and vehicle.archived is False
    page = client.get("/araclar/").get_data(as_text=True)
    assert "78ABC123" in page and "Planlanmamış" in page
    assert "78ABC123" in client.get(response.headers["Location"]).get_data(as_text=True)


def test_search_and_filters(app, client, admin_id):
    make_vehicle(app, admin_id, "34ABC001", "km", vehicle_type="Kamyon")
    make_vehicle(app, admin_id, "EKS-01", "hours", vehicle_type="Ekskavatör")
    make_vehicle(app, admin_id, "OLD-01", "km", status="out_of_service", vehicle_type="Pikap")
    login(client, "yonetici")
    def listed(query):
        page = client.get("/araclar/?" + query).get_data(as_text=True)
        return {i for i in ("34ABC001", "EKS-01", "OLD-01") if i in page}
    assert listed("") == {"34ABC001", "EKS-01", "OLD-01"}
    assert listed("q=eks") == {"EKS-01"}
    assert listed("tur=kamyon") == {"34ABC001"}
    assert listed("sayac=hours") == {"EKS-01"}
    assert listed("durum=out_of_service") == {"OLD-01"}
    assert listed("q=%25") == set()  # joker karakterler kaçırılır
    assert listed("sayac=bogus") == {"34ABC001", "EKS-01", "OLD-01"}  # geçersiz filtre yok sayılır


def test_list_pagination(app, client, admin_id):
    for i in range(25):
        make_vehicle(app, admin_id, f"V-{i:03d}")
    login(client, "yonetici")
    first = client.get("/araclar/").get_data(as_text=True)
    second = client.get("/araclar/?sayfa=2").get_data(as_text=True)
    assert "V-000" in first and "V-024" not in first and "V-024" in second and "Toplam 25" in first
    assert client.get("/araclar/?sayfa=99").status_code == 200


# --- Benzersizlik ve geçersiz form ------------------------------------------------
def test_identifier_uniqueness_is_normalized_and_prevents_double_submit(app, client, admin_id):
    make_vehicle(app, admin_id, "78ABC123")
    login(client, "yonetici")
    for variant in ("78ABC123", "78 abc 123", "78abc123 "):
        response = client.post("/araclar/yeni", data=vehicle_data(identifier=variant))
        assert response.status_code == 200 and "zaten kayıtlı" in response.get_data(as_text=True)
    assert counts(app) == (1, 0)
    assert audit_actions(app).count("vehicle_created") == 1


@pytest.mark.parametrize("override,message", [
    ({"vehicle_type": ""}, "Tür zorunludur"),
    ({"identifier": ""}, "zorunludur"),
    ({"identifier": "A"}, "2-30 karakter"),
    ({"identifier": "AB$%&"}, "2-30 karakter"),
    ({"identifier": "X" * 31}, "En fazla 30"),
    ({"meter_type": "miles"}, "Geçerli bir sayaç türü"),
    ({"status": "bogus"}, "Geçerli bir durum"),
    ({"current_meter": "-1"}, "arasında olmalı"),
    ({"current_meter": "abc"}, "Tam sayı"),
    ({"current_meter": ""}, "zorunludur"),
    ({"current_meter": "99999999999"}, "arasında olmalı"),
    ({"model_year": "1900"}, "Model yılı"),
    ({"model_year": "3000"}, "Model yılı"),
    ({"model_year": "x"}, "Tam sayı"),
    ({"description": "x" * 1001}, "En fazla 1000"),
])
def test_invalid_vehicle_forms_are_rejected(app, client, admin_id, override, message):
    login(client, "yonetici")
    response = client.post("/araclar/yeni", data=vehicle_data(**override))
    assert response.status_code == 200 and message in response.get_data(as_text=True)
    assert counts(app) == (0, 0) and audit_actions(app) == ["login_success"]


def test_optional_fields_may_be_blank(app, client, admin_id):
    login(client, "yonetici")
    response = client.post("/araclar/yeni", data=vehicle_data(brand="", model="", model_year="", description=""))
    assert response.status_code == 302
    with app.app_context():
        v = db.session.scalar(db.select(Vehicle))
        assert (v.brand, v.model, v.model_year, v.description) == (None, None, None, None)


# --- Düzenleme ----------------------------------------------------------------------
def test_edit_updates_fields_and_audits_without_free_text(app, client, admin_id):
    vid = make_vehicle(app, admin_id, "78ABC123", current_meter=1000)
    login(client, "yonetici")
    response = client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(
        identifier="78ABC999", current_meter="1500", description="GİZLİ AÇIKLAMA METNİ"))
    assert response.status_code == 302
    v = _get(app, vid)
    assert v.identifier == "78ABC999" and v.current_meter == 1500 and v.updated_by_id == admin_id
    with app.app_context():
        entry = db.session.scalars(db.select(AuditLog).where(AuditLog.action == "vehicle_updated")).one()
        assert entry.entity_id == vid and "sayaç 1000 → 1500" in entry.details
        assert "GİZLİ" not in entry.details


def test_edit_without_changes_writes_no_audit(app, client, admin_id):
    vid = make_vehicle(app, admin_id, "78ABC123", current_meter=10000)
    login(client, "yonetici")
    before = audit_actions(app)
    page = client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(model_year="", brand="", model="", description=""),
                       follow_redirects=True).get_data(as_text=True)
    assert "Herhangi bir değişiklik yapılmadı" in page and audit_actions(app) == before


def test_edit_cannot_take_another_vehicles_identifier(app, client, admin_id):
    make_vehicle(app, admin_id, "AAA-1")
    vid = make_vehicle(app, admin_id, "BBB-2")
    login(client, "yonetici")
    response = client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(identifier="aaa-1"))
    assert response.status_code == 200 and "başka bir kayıtta" in response.get_data(as_text=True)
    assert _get(app, vid).identifier == "BBB-2"


def test_meter_type_is_locked_once_maintenance_exists(app, client, admin_id):
    vid = make_vehicle(app, admin_id, "78ABC123", "km")
    login(client, "yonetici")
    # Kayıt yokken değiştirilebilir
    assert client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(meter_type="hours")).status_code == 302
    assert _get(app, vid).meter_type == "hours"
    add_record(app, vid, admin_id, reading=10000)
    page = client.get(f"/araclar/{vid}/duzenle").get_data(as_text=True)
    assert "Bakım kaydı bulunduğu için değiştirilemez" in page
    # Elle hazırlanmış istek de sayaç türünü değiştiremez
    client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(meter_type="km", description="yeni"))
    assert _get(app, vid).meter_type == "hours"
    from app.services.vehicles import VehicleRuleError, update_vehicle
    from app.models.user import User
    with app.app_context():
        with pytest.raises(VehicleRuleError, match="sayaç türü"):
            update_vehicle(db.session.get(Vehicle, vid), {**vehicle_data(meter_type="km", model_year=None,
                           current_meter=10000, identifier="78ABC123", brand=None, model=None, description=None)},
                           db.session.get(User, admin_id))


def test_current_meter_cannot_drop_below_recorded_maintenance(app, client, admin_id):
    vid = make_vehicle(app, admin_id, "78ABC123", current_meter=9000)
    add_record(app, vid, admin_id, reading=12000)  # araç sayacı 12000'e yükselir
    assert _get(app, vid).current_meter == 12000
    login(client, "yonetici")
    response = client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(current_meter="11000"))
    assert response.status_code == 200 and "en yüksek bakım sayacından" in response.get_data(as_text=True)
    assert _get(app, vid).current_meter == 12000
    # Yukarı düzeltme serbest
    assert client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(current_meter="12500")).status_code == 302


# --- Arşivleme ---------------------------------------------------------------------------
def test_archive_requires_out_of_service_and_restore_works(app, client, admin_id):
    vid = make_vehicle(app, admin_id, "78ABC123", status="active")
    login(client, "yonetici")
    page = client.post(f"/araclar/{vid}/arsivle", follow_redirects=True).get_data(as_text=True)
    assert "Kullanım dışı" in page and _get(app, vid).archived is False
    assert "vehicle_archived" not in audit_actions(app)

    client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(status="out_of_service"))
    page = client.post(f"/araclar/{vid}/arsivle", follow_redirects=True).get_data(as_text=True)
    assert "arşivlendi" in page and _get(app, vid).archived is True

    # Arşivdeki kayıt düzenlenemez, bakım eklenemez; varsayılan listede görünmez
    assert client.get(f"/araclar/{vid}/duzenle").status_code == 302
    assert client.get(f"/araclar/{vid}/bakim-ekle").status_code == 302
    assert "78ABC123" not in client.get("/araclar/").get_data(as_text=True)
    assert "78ABC123" in client.get("/araclar/?arsiv=arsivli").get_data(as_text=True)
    assert client.post(f"/araclar/{vid}/arsivle", follow_redirects=True).status_code == 200  # zaten arşivde: hata mesajı

    client.post(f"/araclar/{vid}/geri-al")
    assert _get(app, vid).archived is False
    assert audit_actions(app).count("vehicle_archived") == 1 and audit_actions(app).count("vehicle_restored") == 1
    assert counts(app)[0] == 1  # kayıt hiçbir aşamada silinmedi


def test_audit_trail_for_vehicle_creation(app, client, admin_id):
    login(client, "yonetici")
    client.post("/araclar/yeni", data=vehicle_data())
    with app.app_context():
        entry = db.session.scalars(db.select(AuditLog).where(AuditLog.action == "vehicle_created")).one()
        assert entry.user_id == admin_id and entry.entity_type == "vehicle" and "78ABC123" in entry.details
    assert "Araç / iş makinesi oluşturuldu" in client.get("/yonetim/denetim-kayitlari").get_data(as_text=True)
