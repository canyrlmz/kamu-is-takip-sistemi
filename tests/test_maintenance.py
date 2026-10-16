from datetime import timedelta

import pytest

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.vehicle import MaintenanceRecord, MaintenanceVoid, Vehicle
from tests.conftest import local_today, login
from tests.vehicle_helpers import (
    add_record, alert_of, audit_actions, counts, maintenance_data, make_vehicle, void_record,
)


def _vehicle(app, vid):
    with app.app_context():
        return db.session.get(Vehicle, vid)


def _post(client, app, vid, **overrides):
    return client.post(f"/araclar/{vid}/bakim-ekle", data=maintenance_data(app, **overrides))


# --- Ekleme ------------------------------------------------------------------------------
def test_admin_adds_maintenance_and_history_is_shown_newest_first(app, client, admin_id):
    vid = make_vehicle(app, admin_id, current_meter=9000)
    login(client, "yonetici")
    today = local_today(app)
    old = (today - timedelta(days=60)).isoformat()
    assert _post(client, app, vid, maintenance_date=old, meter_reading="9500", description="Eski bakım işlemi",
                 cost="100").status_code == 302
    response = _post(client, app, vid, meter_reading="10000", description="Yeni bakım işlemi",
                     cost="1250,50", provider="Merkez Servis", next_meter="11000",
                     next_date=(today + timedelta(days=90)).isoformat(), notes="Filtre değişti")
    assert response.status_code == 302
    page = client.get(f"/araclar/{vid}").get_data(as_text=True)
    assert page.index("Yeni bakım işlemi") < page.index("Eski bakım işlemi")  # tarih sırası (yeni üstte)
    assert "1.250,50 ₺" in page and "100,00 ₺" in page and "Merkez Servis" in page and "Filtre değişti" in page
    with app.app_context():
        rec = db.session.scalars(db.select(MaintenanceRecord).order_by(MaintenanceRecord.id.desc())).first()
        assert rec.cost_kurus == 125050 and rec.created_by_id == admin_id and rec.meter_reading == 10000
    assert _vehicle(app, vid).current_meter == 10000  # sayaç yukarı güncellendi


def test_meter_only_rises_never_drops_via_maintenance(app, client, admin_id):
    vid = make_vehicle(app, admin_id, current_meter=20000)
    login(client, "yonetici")
    old = (local_today(app) - timedelta(days=30)).isoformat()
    assert _post(client, app, vid, maintenance_date=old, meter_reading="15000").status_code == 302
    assert _vehicle(app, vid).current_meter == 20000


def test_optional_fields_may_be_blank_and_plan_is_closed(app, client, admin_id):
    vid = make_vehicle(app, admin_id)
    login(client, "yonetici")
    assert _post(client, app, vid, provider="", cost="", notes="").status_code == 302
    with app.app_context():
        rec = db.session.scalar(db.select(MaintenanceRecord))
        assert (rec.provider, rec.cost_kurus, rec.next_date, rec.next_meter, rec.notes) == (None,) * 5
    assert alert_of(app, vid).status == "unscheduled"


def test_maintenance_audit_has_no_free_text(app, client, admin_id):
    vid = make_vehicle(app, admin_id)
    login(client, "yonetici")
    _post(client, app, vid, description="ÇOK GİZLİ AÇIKLAMA", notes="GİZLİ NOT", provider="GİZLİ SERVİS")
    with app.app_context():
        entry = db.session.scalars(db.select(AuditLog).where(AuditLog.action == "maintenance_added")).one()
        assert entry.user_id == admin_id and entry.entity_type == "maintenance_record"
        assert "78ABC123" in entry.details and "GİZLİ" not in entry.details


@pytest.mark.parametrize("override,message", [
    ({"cost": "-5"}, "negatif değer"),
    ({"cost": "abc"}, "Tutarı"),
    ({"cost": "1.250,50"}, "Tutarı"),
    ({"meter_reading": "-1"}, "arasında olmalı"),
    ({"meter_reading": "abc"}, "Tam sayı"),
    ({"meter_reading": ""}, "zorunludur"),
    ({"description": ""}, "zorunludur"),
    ({"description": "  "}, "zorunludur"),
    ({"maintenance_type": "bogus"}, "Geçerli bir bakım türü"),
    ({"maintenance_date": ""}, "zorunludur"),
    ({"maintenance_date": "31.31.2026"}, "Geçerli bir tarih"),
    ({"next_meter": "-4"}, "arasında olmalı"),
    ({"next_meter": "10000"}, "büyük olmalı"),     # bakım sayacına eşit
    ({"next_meter": "500"}, "büyük olmalı"),
])
def test_invalid_maintenance_forms_are_rejected(app, client, admin_id, override, message):
    vid = make_vehicle(app, admin_id)
    login(client, "yonetici")
    response = _post(client, app, vid, **override)
    assert response.status_code == 200 and message in response.get_data(as_text=True)
    assert counts(app) == (1, 0)
    assert "maintenance_added" not in audit_actions(app)


def test_date_rules(app, client, admin_id):
    vid = make_vehicle(app, admin_id)
    login(client, "yonetici")
    today = local_today(app)
    future = _post(client, app, vid, maintenance_date=(today + timedelta(days=1)).isoformat())
    assert "gelecekte olamaz" in future.get_data(as_text=True)
    early = _post(client, app, vid, next_date=(today - timedelta(days=1)).isoformat())
    assert "önce olamaz" in early.get_data(as_text=True)
    assert counts(app) == (1, 0)


def test_meter_must_be_consistent_with_neighbouring_records(app, client, admin_id):
    vid = make_vehicle(app, admin_id, current_meter=0)
    add_record(app, vid, admin_id, days_ago=100, reading=5000)
    add_record(app, vid, admin_id, days_ago=10, reading=9000)
    login(client, "yonetici")
    today = local_today(app)
    lower_than_before = _post(client, app, vid, maintenance_date=(today - timedelta(days=5)).isoformat(),
                              meter_reading="8000", description="Geri sayaç")
    assert "düşük olamaz" in lower_than_before.get_data(as_text=True)
    higher_than_after = _post(client, app, vid, maintenance_date=(today - timedelta(days=50)).isoformat(),
                              meter_reading="9500", description="Araya girmiş")
    assert "yüksek olamaz" in higher_than_after.get_data(as_text=True)
    between = _post(client, app, vid, maintenance_date=(today - timedelta(days=50)).isoformat(),
                    meter_reading="7000", description="Araya giren geçerli kayıt")
    assert between.status_code == 302 and counts(app) == (1, 3)


def test_voided_record_does_not_constrain_new_meter_readings(app, client, admin_id):
    vid = make_vehicle(app, admin_id, current_meter=0)
    typo = add_record(app, vid, admin_id, days_ago=10, reading=99999)  # yanlış girilmiş sayaç
    void_record(app, typo, admin_id)
    login(client, "yonetici")
    assert _post(client, app, vid, meter_reading="12000", description="Doğru kayıt").status_code == 302


def test_duplicate_maintenance_is_blocked_but_other_type_or_day_is_allowed(app, client, admin_id):
    vid = make_vehicle(app, admin_id)
    login(client, "yonetici")
    assert _post(client, app, vid).status_code == 302
    again = _post(client, app, vid, description=" PERIYODIK bakım yapıldı ")
    assert again.status_code == 200 and "zaten var" in again.get_data(as_text=True)
    assert _post(client, app, vid, maintenance_type="tire").status_code == 302  # farklı tür
    assert counts(app) == (1, 2)
    # İptal edilen kaydın aynısı yeniden girilebilir (yanlış giriş düzeltme akışı)
    with app.app_context():
        first_id = db.session.scalars(db.select(MaintenanceRecord).order_by(MaintenanceRecord.id)).first().id
    void_record(app, first_id, admin_id)
    assert _post(client, app, vid).status_code == 302


# --- Değiştirilemezlik ve iptal ---------------------------------------------------------------
def test_no_edit_or_delete_routes_exist_for_records(app, client, admin_id):
    vid = make_vehicle(app, admin_id)
    rid = add_record(app, vid, admin_id)
    login(client, "yonetici")
    for method, url in (("get", f"/araclar/{vid}/bakim/{rid}/duzenle"), ("post", f"/araclar/{vid}/bakim/{rid}/sil"),
                        ("post", f"/araclar/{vid}/bakim/{rid}/duzenle")):
        assert getattr(client, method)(url).status_code == 404, url
    assert client.delete(f"/araclar/{vid}/bakim/{rid}/iptal").status_code == 405
    assert counts(app) == (1, 1)


def test_void_requires_reason_and_keeps_record_visible(app, client, admin_id):
    vid = make_vehicle(app, admin_id)
    rid = add_record(app, vid, admin_id, days_ago=30, description="Hatalı girilen bakım", next_in_days=-5)
    assert alert_of(app, vid).status == "overdue"
    login(client, "yonetici")
    url = f"/araclar/{vid}/bakim/{rid}/iptal"
    assert client.get(url).status_code == 200
    for bad, message in (("", "zorunludur"), ("   ", "zorunludur"), ("kısa", "5-500 karakter")):
        response = client.post(url, data={"reason": bad})
        assert response.status_code == 200 and message in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(MaintenanceVoid)) == 0
    response = client.post(url, data={"reason": "Sayaç yanlış girildi"})
    assert response.status_code == 302
    with app.app_context():
        void = db.session.scalar(db.select(MaintenanceVoid))
        assert void.record_id == rid and void.voided_by_id == admin_id and void.reason == "Sayaç yanlış girildi"
        assert void.created_at is not None
        assert db.session.get(MaintenanceRecord, rid).description == "Hatalı girilen bakım"  # kayıt değişmedi
    page = client.get(f"/araclar/{vid}").get_data(as_text=True)
    assert "Hatalı girilen bakım" in page and "İPTAL: Sayaç yanlış girildi" in page and "Yönetici Örnek" in page
    assert alert_of(app, vid).status == "unscheduled"  # iptal edilen kayıt uyarıdan çıktı
    assert audit_actions(app).count("maintenance_voided") == 1


def test_void_cannot_be_repeated_and_other_vehicles_record_is_404(app, client, admin_id):
    v1 = make_vehicle(app, admin_id, "AAA-1")
    v2 = make_vehicle(app, admin_id, "BBB-2")
    rid = add_record(app, v1, admin_id)
    login(client, "yonetici")
    assert client.get(f"/araclar/{v2}/bakim/{rid}/iptal").status_code == 404  # başka aracın kaydı
    assert client.post(f"/araclar/{v2}/bakim/{rid}/iptal", data={"reason": "Başka araç"}).status_code == 404
    assert client.post(f"/araclar/{v1}/bakim/{rid}/iptal", data={"reason": "İlk iptal gerekçesi"}).status_code == 302
    again = client.post(f"/araclar/{v1}/bakim/{rid}/iptal", data={"reason": "İkinci iptal gerekçesi"},
                        follow_redirects=True)
    assert "iptal edilemez" in again.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(MaintenanceVoid)) == 1
    assert audit_actions(app).count("maintenance_voided") == 1


def test_void_does_not_change_vehicle_meter(app, client, admin_id):
    vid = make_vehicle(app, admin_id, current_meter=100)
    rid = add_record(app, vid, admin_id, reading=5000)
    void_record(app, rid, admin_id)
    assert _vehicle(app, vid).current_meter == 5000  # otomatik düşürülmez; araç düzenlenerek düzeltilir
    login(client, "yonetici")
    from tests.vehicle_helpers import vehicle_data
    # İptalden sonra hatalı sayaç artık engel değildir
    assert client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(identifier="78ABC123", current_meter="150")).status_code == 302
    assert _vehicle(app, vid).current_meter == 150


def test_meter_type_stays_locked_even_if_all_records_are_voided(app, client, admin_id):
    vid = make_vehicle(app, admin_id, "78ABC123", "km")
    rid = add_record(app, vid, admin_id)
    void_record(app, rid, admin_id)
    login(client, "yonetici")
    from tests.vehicle_helpers import vehicle_data
    client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(meter_type="hours"))
    assert _vehicle(app, vid).meter_type == "km"  # veritabanı tetikleyicisiyle uyumlu: iptal edilmiş kayıt da sayılır
