import pytest

from app.extensions import db
from app.models.asset import Asset, AssetAssignment
from app.models.audit_log import AuditLog
from tests.conftest import get_csrf_token, iso, local_today, login
from datetime import timedelta
from app.models.user import ROLE_ADMIN


def asset_data(**overrides):
    data = {
        "code": "DMB-1001", "name": "Dizüstü Bilgisayar", "category": "Bilgisayar", "brand": "MarkaX",
        "model": "M1", "serial_number": "sn-12345", "location": "Oda 12", "acquisition_date": "2024-03-01",
        "status": "in_storage", "description": "Örnek kayıt",
    }
    data.update(overrides)
    return data


def get_asset(app, code):
    with app.app_context():
        return db.session.scalar(db.select(Asset).where(Asset.code == code))


def asset_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count()).select_from(Asset))


def audit_actions(app):
    with app.app_context():
        return [e.action for e in db.session.scalars(db.select(AuditLog).order_by(AuditLog.id)).all()]


# --- Erişim ------------------------------------------------------------------

def test_anonymous_is_redirected(client, app, make_asset):
    aid = make_asset(app)
    for url in ("/demirbaslar/", f"/demirbaslar/{aid}", "/demirbaslar/yeni", f"/demirbaslar/{aid}/duzenle",
                f"/demirbaslar/{aid}/zimmet-ver", f"/demirbaslar/{aid}/iade-al"):
        response = client.get(url)
        assert response.status_code == 302 and "/giris" in response.headers["Location"], url
    for url in ("/demirbaslar/yeni", f"/demirbaslar/{aid}/arsivle", f"/demirbaslar/{aid}/geri-al"):
        assert client.post(url, data=asset_data()).status_code == 302


def test_staff_can_view_but_not_change(app, client, admin_id, staff_id, make_asset):
    aid = make_asset(app)
    login(client, "personel")
    assert client.get("/demirbaslar/").status_code == 200
    assert client.get(f"/demirbaslar/{aid}").status_code == 200
    html = client.get(f"/demirbaslar/{aid}").get_data(as_text=True)
    assert "Düzenle" not in html and "Zimmet ver" not in html and "Arşivle" not in html
    for url in ("/demirbaslar/yeni", f"/demirbaslar/{aid}/duzenle", f"/demirbaslar/{aid}/zimmet-ver",
                f"/demirbaslar/{aid}/iade-al"):
        assert client.get(url).status_code == 403, url
        assert client.post(url, data=asset_data()).status_code == 403, url
    assert client.post(f"/demirbaslar/{aid}/arsivle").status_code == 403
    assert client.post(f"/demirbaslar/{aid}/geri-al").status_code == 403
    assert client.post(f"/demirbaslar/{aid}/zimmet-ver", data={"assignee_id": staff_id}).status_code == 403
    assert asset_count(app) == 1
    assert get_asset(app, "DMB-0001").archived is False
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(AssetAssignment)) == 0


def test_menu_link_visible_to_both_roles(client, admin_id, staff_id):
    for username in ("personel", "yonetici"):
        login(client, username)
        assert "/demirbaslar/" in client.get("/").get_data(as_text=True)
        client.post("/cikis")


def test_no_delete_route_exists(client, app, admin_id, make_asset):
    aid = make_asset(app)
    login(client, "yonetici")
    assert client.post(f"/demirbaslar/{aid}/sil").status_code == 404
    assert client.delete(f"/demirbaslar/{aid}").status_code == 405
    assert asset_count(app) == 1


# --- Oluşturma ---------------------------------------------------------------

def test_admin_creates_asset(app, client, admin_id):
    login(client, "yonetici")
    response = client.post("/demirbaslar/yeni", data=asset_data())
    assert response.status_code == 302
    asset = get_asset(app, "DMB-1001")
    assert asset.name == "Dizüstü Bilgisayar" and asset.serial_number == "SN-12345"
    assert asset.status == "in_storage" and asset.archived is False
    assert str(asset.acquisition_date) == "2024-03-01"
    with app.app_context():
        asset = db.session.scalar(db.select(Asset))
        assert asset.created_by_id == admin_id and asset.updated_by_id == admin_id
        entry = db.session.scalars(db.select(AuditLog).where(AuditLog.action == "asset_created")).one()
        assert entry.user_id == admin_id and entry.entity_type == "asset" and entry.entity_id == asset.id


def test_code_is_normalised_and_duplicates_rejected_case_insensitively(app, client, admin_id, make_asset):
    make_asset(app, code="DMB-2000")
    login(client, "yonetici")
    response = client.post("/demirbaslar/yeni", data=asset_data(code="  dmb-2000 "))
    assert response.status_code == 200 and "zaten kullanılıyor" in response.get_data(as_text=True)
    assert asset_count(app) == 1
    client.post("/demirbaslar/yeni", data=asset_data(code=" dmb-3000 ", serial_number=""))
    assert get_asset(app, "DMB-3000") is not None


def test_duplicate_serial_rejected_but_blank_serial_allowed_many_times(app, client, admin_id, make_asset):
    make_asset(app, code="DMB-1", serial_number="ABC123")
    login(client, "yonetici")
    response = client.post("/demirbaslar/yeni", data=asset_data(code="DMB-2", serial_number=" abc123 "))
    assert "başka bir demirbaşta kayıtlı" in response.get_data(as_text=True)
    for code in ("DMB-3", "DMB-4", "DMB-5"):
        assert client.post("/demirbaslar/yeni", data=asset_data(code=code, serial_number="")).status_code == 302
    assert asset_count(app) == 4
    assert get_asset(app, "DMB-3").serial_number is None


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"code": "bad code!"}, "Kod 2-50 karakter"),
        ({"code": "A"}, "Kod 2-50 karakter"),
        ({"code": ""}, "Demirbaş kodu zorunludur"),
        ({"name": ""}, "Eşya adı zorunludur"),
        ({"name": "x" * 121}, "En fazla 120"),
        ({"category": ""}, "Kategori zorunludur"),
        ({"description": "x" * 1001}, "En fazla 1000"),
        ({"acquisition_date": "2999-01-01"}, "gelecekte olamaz"),
        ({"acquisition_date": "31.02.2024"}, "Geçerli bir tarih girin"),
        ({"status": "in_use"}, "Geçerli bir durum seçin"),
        ({"status": "bogus"}, "Geçerli bir durum seçin"),
    ],
)
def test_invalid_input_rejected_and_nothing_saved(app, client, admin_id, overrides, message):
    login(client, "yonetici")
    response = client.post("/demirbaslar/yeni", data=asset_data(**overrides))
    assert response.status_code == 200
    assert message in response.get_data(as_text=True)
    assert asset_count(app) == 0
    assert "asset_created" not in audit_actions(app)


def test_optional_fields_may_be_blank(app, client, admin_id):
    login(client, "yonetici")
    data = asset_data(brand="", model="", serial_number="", location="", acquisition_date="", description="")
    assert client.post("/demirbaslar/yeni", data=data).status_code == 302
    asset = get_asset(app, "DMB-1001")
    assert asset.brand is None and asset.location is None and asset.acquisition_date is None


# --- Düzenleme ---------------------------------------------------------------

def test_edit_updates_fields_and_audits_changed_fields(app, client, admin_id, make_asset):
    aid = make_asset(app, code="DMB-7", location="Oda 1")
    login(client, "yonetici")
    data = asset_data(code="DMB-7", name="Yeni Ad", location="Oda 2", serial_number="")
    assert client.post(f"/demirbaslar/{aid}/duzenle", data=data).status_code == 302
    asset = get_asset(app, "DMB-7")
    assert asset.name == "Yeni Ad" and asset.location == "Oda 2"
    with app.app_context():
        entry = db.session.scalars(db.select(AuditLog).where(AuditLog.action == "asset_updated")).one()
        assert "ad" in entry.details and "konum" in entry.details and entry.entity_id == aid
        assert db.session.get(Asset, aid).updated_by_id == admin_id


def test_edit_without_changes_writes_no_audit(app, client, admin_id, make_asset):
    aid = make_asset(app, code="DMB-8", name="Aynı", category="Masa")
    login(client, "yonetici")
    data = {"code": "DMB-8", "name": "Aynı", "category": "Masa", "status": "in_storage"}
    assert client.post(f"/demirbaslar/{aid}/duzenle", data=data).status_code == 302
    assert "asset_updated" not in audit_actions(app)


def test_edit_form_prefills_values(app, client, admin_id, make_asset):
    aid = make_asset(app, code="DMB-9", name="Öne Dolu", location="Arşiv")
    login(client, "yonetici")
    html = client.get(f"/demirbaslar/{aid}/duzenle").get_data(as_text=True)
    assert "DMB-9" in html and "Öne Dolu" in html and "Arşiv" in html


def test_edit_code_uniqueness_excludes_self_but_not_others(app, client, admin_id, make_asset):
    aid = make_asset(app, code="DMB-10")
    make_asset(app, code="DMB-11")
    login(client, "yonetici")
    ok = client.post(f"/demirbaslar/{aid}/duzenle", data=asset_data(code="DMB-10", serial_number="", name="A"))
    assert ok.status_code == 302
    clash = client.post(f"/demirbaslar/{aid}/duzenle", data=asset_data(code="DMB-11", serial_number=""))
    assert clash.status_code == 200 and "zaten kullanılıyor" in clash.get_data(as_text=True)
    assert get_asset(app, "DMB-10") is not None


def test_edit_can_change_status_when_not_assigned(app, client, admin_id, make_asset):
    aid = make_asset(app, code="DMB-12")
    login(client, "yonetici")
    client.post(f"/demirbaslar/{aid}/duzenle", data=asset_data(code="DMB-12", serial_number="", status="maintenance"))
    assert get_asset(app, "DMB-12").status == "maintenance"


def test_edit_unknown_asset_is_404(client, admin_id):
    login(client, "yonetici")
    assert client.get("/demirbaslar/999/duzenle").status_code == 404
    assert client.get("/demirbaslar/999").status_code == 404


# --- Arşivleme (pasife alma) -------------------------------------------------

def test_archive_and_restore_with_audit_and_history_kept(app, client, admin_id, make_asset):
    aid = make_asset(app, code="DMB-20")
    login(client, "yonetici")
    assert client.post(f"/demirbaslar/{aid}/arsivle").status_code == 302
    assert get_asset(app, "DMB-20").archived is True
    link = f'href="/demirbaslar/{aid}"'  # tablodaki satır bağlantısı (bildirim metninden bağımsız)
    assert link not in client.get("/demirbaslar/").get_data(as_text=True)
    assert link in client.get("/demirbaslar/", query_string={"arsiv": "arsivli"}).get_data(as_text=True)
    assert link in client.get("/demirbaslar/", query_string={"arsiv": "hepsi"}).get_data(as_text=True)
    assert client.get(f"/demirbaslar/{aid}").status_code == 200  # arşiv kaydı görüntülenebilir
    assert client.post(f"/demirbaslar/{aid}/geri-al").status_code == 302
    assert get_asset(app, "DMB-20").archived is False
    assert audit_actions(app).count("asset_archived") == 1 and audit_actions(app).count("asset_restored") == 1


def test_archived_asset_cannot_be_edited_or_assigned(app, client, admin_id, staff_id, make_asset):
    aid = make_asset(app, code="DMB-21", archived=True)
    login(client, "yonetici")
    assert client.get(f"/demirbaslar/{aid}/duzenle").status_code == 302
    client.post(f"/demirbaslar/{aid}/duzenle", data=asset_data(code="DMB-21", name="Değişti", serial_number=""))
    assert get_asset(app, "DMB-21").name != "Değişti"
    response = client.post(f"/demirbaslar/{aid}/zimmet-ver",
                           data={"assignee_id": staff_id, "start_date": iso(local_today(app))})
    assert "Arşivdeki demirbaş zimmetlenemez" in response.get_data(as_text=True)


def test_double_archive_and_restore_of_active_are_harmless(app, client, admin_id, make_asset):
    aid = make_asset(app, code="DMB-22")
    login(client, "yonetici")
    client.post(f"/demirbaslar/{aid}/geri-al")
    client.post(f"/demirbaslar/{aid}/arsivle")
    client.post(f"/demirbaslar/{aid}/arsivle")
    assert audit_actions(app).count("asset_archived") == 1
    assert "asset_restored" not in audit_actions(app)


# --- Liste, arama, filtreleme ------------------------------------------------

@pytest.fixture()
def sample_assets(app, make_asset):
    make_asset(app, code="BLG-001", name="Masaüstü Bilgisayar", category="Bilgisayar", location="Bilgi İşlem", serial_number="SER111")
    make_asset(app, code="YZC-001", name="Lazer Yazıcı", category="Yazıcı", location="Muhasebe", serial_number="SER222", status="maintenance")
    make_asset(app, code="MSA-001", name="Çalışma Masası", category="Masa", location="Muhasebe")
    make_asset(app, code="OLD-001", name="Eski Monitör", category="Monitör", status="scrap_candidate", archived=True)


def codes_in(response):
    html = response.get_data(as_text=True)
    return {c for c in ("BLG-001", "YZC-001", "MSA-001", "OLD-001") if c in html}


def test_list_filters(app, client, admin_id, sample_assets):
    login(client, "yonetici")
    assert codes_in(client.get("/demirbaslar/")) == {"BLG-001", "YZC-001", "MSA-001"}  # arşiv gizli
    q = lambda **p: codes_in(client.get("/demirbaslar/", query_string=p))  # noqa: E731
    assert q(q="blg") == {"BLG-001"}
    assert q(q="lazer") == {"YZC-001"}
    assert q(q="ser222") == {"YZC-001"}
    assert q(kategori="masa") == {"MSA-001"}
    assert q(konum="muhasebe") == {"YZC-001", "MSA-001"}
    assert q(durum="maintenance") == {"YZC-001"}
    assert q(arsiv="arsivli") == {"OLD-001"}
    assert q(arsiv="hepsi") == {"BLG-001", "YZC-001", "MSA-001", "OLD-001"}
    assert q(q="blg", konum="muhasebe") == set()


def test_assigned_unassigned_filter(app, client, admin_id, staff_id, sample_assets):
    login(client, "yonetici")
    with app.app_context():
        asset = db.session.scalar(db.select(Asset).where(Asset.code == "BLG-001"))
        db.session.add(AssetAssignment(asset_id=asset.id, assignee_id=staff_id, assigned_by_id=admin_id,
                                       start_date=local_today(app)))
        asset.status = "in_use"
        db.session.commit()
    zimmetli = codes_in(client.get("/demirbaslar/", query_string={"zimmet": "zimmetli"}))
    zimmetsiz = codes_in(client.get("/demirbaslar/", query_string={"zimmet": "zimmetsiz"}))
    assert zimmetli == {"BLG-001"} and zimmetsiz == {"YZC-001", "MSA-001"}
    html = client.get("/demirbaslar/").get_data(as_text=True)
    assert "Personel Örnek" in html  # zimmetli personel listede görünür


def test_like_wildcards_are_treated_literally(app, client, admin_id, sample_assets, make_asset):
    make_asset(app, code="PCT-001", name="Yüzde %50 Etiket")
    login(client, "yonetici")
    html = client.get("/demirbaslar/", query_string={"q": "%"}).get_data(as_text=True)
    assert "PCT-001" in html and "BLG-001" not in html
    html = client.get("/demirbaslar/", query_string={"q": "_"}).get_data(as_text=True)
    assert "BLG-001" not in html


def test_list_tolerates_garbage_parameters(client, admin_id, sample_assets):
    login(client, "yonetici")
    params = {"durum": "<script>", "zimmet": "x", "arsiv": "y", "sayfa": "abc", "q": "x" * 500}
    assert client.get("/demirbaslar/", query_string=params).status_code == 200
    for value in ("-3", "9999"):
        assert client.get("/demirbaslar/", query_string={"sayfa": value}).status_code == 200


def test_pagination_keeps_filters(app, client, admin_id, make_asset):
    for i in range(25):
        make_asset(app, code=f"PGN-{i:03d}", name=f"Sayfa Deneme {i}")
    login(client, "yonetici")
    html = client.get("/demirbaslar/", query_string={"q": "sayfa"}).get_data(as_text=True)
    assert "PGN-000" in html and "PGN-024" not in html
    assert "q=sayfa" in html and "sayfa=2" in html
    assert "PGN-024" in client.get("/demirbaslar/", query_string={"q": "sayfa", "sayfa": 2}).get_data(as_text=True)


def test_user_input_is_escaped_in_html(app, client, admin_id, make_asset):
    aid = make_asset(app, code="XSS-001", name="<script>alert(1)</script>")
    login(client, "yonetici")
    for url in ("/demirbaslar/", f"/demirbaslar/{aid}"):
        html = client.get(url).get_data(as_text=True)
        assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


# --- Panel -------------------------------------------------------------------

def test_dashboard_counts_come_from_database(app, client, admin_id, staff_id, make_asset):
    login(client, "yonetici")
    html = client.get("/").get_data(as_text=True)
    assert "Toplam demirbaş" in html
    from app.services.assets import asset_summary
    with app.app_context():
        assert asset_summary() == {"total": 0, "assigned": 0, "available": 0}
    a = make_asset(app, code="D-1")
    make_asset(app, code="D-2")
    make_asset(app, code="D-3", status="maintenance")
    make_asset(app, code="D-4", archived=True)
    client.post(f"/demirbaslar/{a}/zimmet-ver", data={"assignee_id": staff_id, "start_date": iso(local_today(app))})
    with app.app_context():
        assert asset_summary() == {"total": 3, "assigned": 1, "available": 1}
    client.post("/cikis")
    login(client, "personel")  # personel de özeti görür
    assert client.get("/").status_code == 200


# --- CSRF --------------------------------------------------------------------

def test_asset_posts_require_csrf(csrf_app, make_user, make_asset):
    make_user(csrf_app, username="yonetici", role=ROLE_ADMIN)
    aid = make_asset(csrf_app, code="CSRF-1")
    client = csrf_app.test_client()
    token = get_csrf_token(client)
    client.post("/giris", data={"username": "yonetici", "password": "Test-Parola-123!", "csrf_token": token})

    assert client.post("/demirbaslar/yeni", data=asset_data()).status_code == 400
    assert client.post(f"/demirbaslar/{aid}/arsivle").status_code == 400
    assert client.post(f"/demirbaslar/{aid}/zimmet-ver", data={"assignee_id": 1}).status_code == 400
    assert get_asset(csrf_app, "DMB-1001") is None and get_asset(csrf_app, "CSRF-1").archived is False

    token = get_csrf_token(client, "/demirbaslar/yeni")
    assert client.post("/demirbaslar/yeni", data=asset_data(csrf_token=token)).status_code == 302
    token = get_csrf_token(client, f"/demirbaslar/{aid}")
    assert client.post(f"/demirbaslar/{aid}/arsivle", data={"csrf_token": token}).status_code == 302
    assert get_asset(csrf_app, "CSRF-1").archived is True
