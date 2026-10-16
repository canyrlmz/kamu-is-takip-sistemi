"""Uyarıların veritabanı ve arayüzle birlikte çalışması: güncel hesaplama, türlere göre plan, kapsam, panel."""
from app.services.vehicles import vehicle_summary
from tests.conftest import login
from tests.vehicle_helpers import add_record, alert_of, make_vehicle, vehicle_data, void_record


def _status_ids(client, query):
    page = client.get("/araclar/?" + query).get_data(as_text=True)
    return {i for i in ("OVR-1", "UPC-1", "NRM-1", "UNS-1") if i in page}


def _fleet(app, admin_id):
    ids = {
        "OVR-1": make_vehicle(app, admin_id, "OVR-1"),
        "UPC-1": make_vehicle(app, admin_id, "UPC-1"),
        "NRM-1": make_vehicle(app, admin_id, "NRM-1"),
        "UNS-1": make_vehicle(app, admin_id, "UNS-1"),
    }
    add_record(app, ids["OVR-1"], admin_id, days_ago=40, next_in_days=-1)
    add_record(app, ids["UPC-1"], admin_id, days_ago=40, next_in_days=10)
    add_record(app, ids["NRM-1"], admin_id, days_ago=40, next_in_days=200)
    return ids


def test_list_filters_by_maintenance_state(app, client, admin_id):
    _fleet(app, admin_id)
    login(client, "yonetici")
    assert _status_ids(client, "bakim=gecikmis") == {"OVR-1"}
    assert _status_ids(client, "bakim=yaklasan") == {"UPC-1"}
    assert _status_ids(client, "bakim=planlanmamis") == {"UNS-1"}
    assert _status_ids(client, "") == {"OVR-1", "UPC-1", "NRM-1", "UNS-1"}
    page = client.get("/araclar/").get_data(as_text=True)
    assert "Gecikmiş" in page and "Yaklaşan" in page and "Planlanmamış" in page


def test_km_and_hours_vehicles_with_same_numbers_are_judged_by_their_own_unit(app, admin_id):
    km = make_vehicle(app, admin_id, "KM-1", "km", current_meter=10000)
    hours = make_vehicle(app, admin_id, "HRS-1", "hours", current_meter=10000)
    add_record(app, km, admin_id, days_ago=5, reading=10000, next_meter=10500)      # 500 km kaldı -> yaklaşan
    add_record(app, hours, admin_id, days_ago=5, reading=10000, next_meter=10500)   # 500 saat kaldı -> normal
    assert alert_of(app, km).status == "upcoming"
    assert alert_of(app, hours).status == "normal"
    add_record(app, hours, admin_id, days_ago=1, mtype="tire", reading=10000, next_meter=10040)  # 40 saat kaldı
    assert alert_of(app, hours).status == "upcoming"


def test_plans_of_different_types_do_not_overwrite_each_other(app, admin_id):
    vid = make_vehicle(app, admin_id)
    add_record(app, vid, admin_id, days_ago=90, mtype="oil_filter", next_in_days=-5)     # gecikmiş yağ planı
    assert alert_of(app, vid).status == "overdue"
    add_record(app, vid, admin_id, days_ago=1, mtype="tire", next_in_days=300)           # daha yeni ama başka tür
    alert = alert_of(app, vid)
    assert alert.status == "overdue"
    assert {p.maintenance_type: p.status for p in alert.plans} == {"oil_filter": "overdue", "tire": "normal"}
    # Aynı türde yeni bakım eski planı kapatır/yerine geçer
    add_record(app, vid, admin_id, days_ago=0, mtype="oil_filter", next_in_days=180)
    alert = alert_of(app, vid)
    assert alert.status == "normal" and len(alert.plans) == 2


def test_plan_closes_and_reopens_after_void(app, admin_id):
    vid = make_vehicle(app, admin_id)
    add_record(app, vid, admin_id, days_ago=60, mtype="brake", next_in_days=-3)
    closing = add_record(app, vid, admin_id, days_ago=0, mtype="brake")  # sonraki bakım yok -> plan kapanır
    assert alert_of(app, vid).status == "unscheduled"
    void_record(app, closing, admin_id)                                  # önceki kayıt yeniden belirleyici
    assert alert_of(app, vid).status == "overdue"


def test_alerts_are_recomputed_after_every_change_no_stale_cache(app, client, admin_id):
    vid = make_vehicle(app, admin_id, "78ABC123", "km", current_meter=10000)
    rid = add_record(app, vid, admin_id, days_ago=5, reading=10000, next_meter=12000)
    login(client, "yonetici")
    assert alert_of(app, vid).status == "normal"
    # Sayaç değişikliği (araç düzenleme)
    client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(current_meter="11500"))
    assert alert_of(app, vid).status == "upcoming"
    client.post(f"/araclar/{vid}/duzenle", data=vehicle_data(current_meter="12000"))
    assert alert_of(app, vid).status == "overdue"
    assert "Gecikmiş" in client.get("/araclar/").get_data(as_text=True)
    # Yeni bakım kaydı
    add_record(app, vid, admin_id, days_ago=0, reading=12000, next_meter=20000, description="Yeni periyodik")
    assert alert_of(app, vid).status == "normal"
    assert "Normal" in client.get("/araclar/").get_data(as_text=True)
    # İptal
    new_rid = add_record(app, vid, admin_id, days_ago=0, mtype="tire", reading=12000, next_meter=12100)
    assert alert_of(app, vid).status == "upcoming"  # 100 km kaldı
    void_record(app, new_rid, admin_id)
    assert alert_of(app, vid).status == "normal"
    assert rid  # ilk kayıt etkilenmedi


def test_out_of_service_and_archived_vehicles_are_excluded(app, client, admin_id):
    active = make_vehicle(app, admin_id, "ACT-1")              # planlanmamış
    out = make_vehicle(app, admin_id, "OUT-1", status="out_of_service")
    add_record(app, out, admin_id, days_ago=40, next_in_days=-10)  # gecikmiş olurdu
    arch = make_vehicle(app, admin_id, "ARC-1", status="out_of_service")
    login(client, "yonetici")
    client.post(f"/araclar/{arch}/arsivle")
    with app.app_context():
        summary = vehicle_summary()
    assert summary == {"total": 2, "overdue": 0, "upcoming": 0, "unscheduled": 1}  # arşivli hariç, kullanım dışı uyarısız
    assert alert_of(app, out).status == "inactive" and alert_of(app, active).status == "unscheduled"
    page = client.get(f"/araclar/{out}").get_data(as_text=True)
    assert "bakım uyarısı hesaplanmaz" in page


def test_dashboard_and_summary_page_show_real_counts(app, client, admin_id, staff_id):
    _fleet(app, admin_id)
    login(client, "personel")  # personel de uyarıları görür
    dash = client.get("/").get_data(as_text=True)
    assert "Aşama 4'te eklenecek" not in dash and "Kayıtlı araç" in dash
    with app.app_context():
        assert vehicle_summary() == {"total": 4, "overdue": 1, "upcoming": 1, "unscheduled": 1}
    page = client.get("/araclar/yaklasan-bakimlar").get_data(as_text=True)
    for ident in ("OVR-1", "UPC-1", "UNS-1"):
        assert ident in page
    assert "NRM-1" not in page and "Normal durumdaki araç sayısı: 1" in page


def test_detail_shows_per_type_plans_with_units(app, client, admin_id):
    vid = make_vehicle(app, admin_id, "EKS-01", "hours", current_meter=500)
    add_record(app, vid, admin_id, days_ago=3, mtype="oil_filter", reading=500, next_meter=520)
    login(client, "yonetici")
    page = client.get(f"/araclar/{vid}").get_data(as_text=True)
    assert "Yağ / filtre" in page and "20 saat kaldı" in page and "Yaklaşan" in page
    assert "km" not in page.split("Bakım planları")[1].split("Bakım geçmişi")[0]  # saat aracında km görünmez


def test_navigation_link_is_active(app, client, admin_id):
    login(client, "yonetici")
    page = client.get("/").get_data(as_text=True)
    assert 'href="/araclar/"' in page and "(Aşama 4)" not in page
