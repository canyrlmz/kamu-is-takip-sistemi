"""Aşama 5.2: menü, panel ve düğmeler izne göre gösterilir (güvenlik değil kolaylık; asıl denetim matris testlerindedir)."""
import pytest

from app.extensions import db
from app.models.asset import Asset
from app.permissions import ROLE_BIRIM_SORUMLUSU, ROLE_SUPERADMIN
from tests.authz_helpers import make_profile
from tests.conftest import login
from tests.ticket_helpers import make_ticket
from tests.vehicle_helpers import add_record, make_vehicle

MENU_LINKS = {
    "assets.view": "/demirbaslar/",
    "tickets.view": "/talepler/",
    "tickets.view_all": "/talepler/",
    "vehicles.view": "/araclar/",
    "audit.view": "/yonetim/denetim-kayitlari",
}


@pytest.fixture()
def world(app):
    root = make_profile(app, "kok", ROLE_SUPERADMIN)
    with app.app_context():
        asset = Asset(code="DMB-1", name="Bilgisayar", category="Bilgisayar", status="in_storage")
        db.session.add(asset)
        db.session.commit()
        asset_id = asset.id
    vehicle_id = make_vehicle(app, root)
    record_id = add_record(app, vehicle_id, root, description="Yağ değişti")
    ticket_id = make_ticket(app, root)
    return {"asset": asset_id, "vehicle": vehicle_id, "record": record_id, "ticket": ticket_id}


def html_for(app, client, permissions, url="/"):
    make_profile(app, "kisi", ROLE_BIRIM_SORUMLUSU, permissions)
    login(client, "kisi")
    return client.get(url).get_data(as_text=True)


@pytest.mark.parametrize("permission", sorted(MENU_LINKS))
def test_menu_shows_only_the_modules_the_user_may_open(app, client, world, permission):
    html = html_for(app, client, {permission})
    nav = html.split('aria-label="Modül menüsü"', 1)[1].split("</nav>", 1)[0]
    for other, link in MENU_LINKS.items():
        assert (link in nav) == (link == MENU_LINKS[permission]), (permission, other, link)
    assert "/yonetim/kullanicilar" not in nav  # users.manage kimseye atanamaz


def test_user_without_permissions_sees_an_empty_menu_and_dashboard(app, client, world):
    html = html_for(app, client, set())
    for fragment in ("/demirbaslar/", "/talepler/", "/araclar/", "/yonetim/", "Demirbaş ve Zimmet", "Araç ve Bakım",
                     "Arıza ve Talepler", "Son denetim kayıtları", "Aktif kullanıcı"):
        assert fragment not in html, fragment
    assert "Hoş geldiniz" in html


def test_superadmin_sees_everything(app, client, world):
    login(client, "kok")
    html = client.get("/").get_data(as_text=True)
    for fragment in ("/demirbaslar/", "/talepler/", "/araclar/", "/yonetim/kullanicilar", "/yonetim/denetim-kayitlari",
                     "Aktif kullanıcı", "Son denetim kayıtları", "Demirbaş ve Zimmet", "Arıza ve Talepler"):
        assert fragment in html, fragment


@pytest.mark.parametrize("permission, visible, hidden", [
    ("assets.view", "Toplam demirbaş", ("Kayıtlı araç", "Açık talep")),
    ("vehicles.view", "Kayıtlı araç", ("Toplam demirbaş", "Açık talep")),
    ("tickets.view", "Açık talep", ("Toplam demirbaş", "Kayıtlı araç")),
])
def test_dashboard_panels_follow_permissions(app, client, world, permission, visible, hidden):
    html = html_for(app, client, {permission})
    assert visible in html and all(h not in html for h in hidden)


def test_dashboard_vehicle_panel_hides_maintenance_counts_without_maintenance_view(app, client, world):
    html = html_for(app, client, {"vehicles.view"})
    assert "Gecikmiş" not in html and "Planlanmamış" not in html and "/araclar/yaklasan-bakimlar" not in html
    client.post("/cikis")
    login(client, "kok")
    assert "Gecikmiş" in client.get("/").get_data(as_text=True)


def test_audit_card_requires_audit_view(app, client, world):
    assert "Son denetim kayıtları" in html_for(app, client, {"audit.view"})


@pytest.mark.parametrize("permissions, shown, hidden", [
    ({"assets.view"}, (), ("Düzenle", "Zimmet ver", "Arşivle")),
    ({"assets.view", "assets.update"}, ("Düzenle", "Arşivle"), ("Zimmet ver",)),
    ({"assets.view", "assets.assign"}, ("Zimmet ver",), ("Düzenle", "Arşivle")),
])
def test_asset_buttons_follow_permissions(app, client, world, permissions, shown, hidden):
    html = html_for(app, client, permissions, f"/demirbaslar/{world['asset']}")
    assert all(s in html for s in shown) and all(h not in html for h in hidden)


def test_asset_list_new_button_requires_create(app, client, world):
    assert "Yeni demirbaş" not in html_for(app, client, {"assets.view"}, "/demirbaslar/")
    client.post("/cikis")
    make_profile(app, "kisi2", ROLE_BIRIM_SORUMLUSU, {"assets.view", "assets.create"})
    login(client, "kisi2")
    assert "Yeni demirbaş" in client.get("/demirbaslar/").get_data(as_text=True)


def test_vehicle_page_hides_maintenance_without_maintenance_view(app, client, world):
    html = html_for(app, client, {"vehicles.view"}, f"/araclar/{world['vehicle']}")
    assert "Yağ değişti" not in html and "Bakım geçmişi" not in html and "Bakım planları" not in html
    assert "Kayıt bilgileri" in html


def test_vehicle_list_hides_maintenance_column_and_ignores_the_filter(app, client, world):
    make_profile(app, "kisi", ROLE_BIRIM_SORUMLUSU, {"vehicles.view"})
    login(client, "kisi")
    html = client.get("/araclar/", query_string={"bakim": "gecikmis"}).get_data(as_text=True)
    assert "<th>Bakım</th>" not in html and "Bakım durumu özeti" not in html
    assert "78ABC123" in html  # filtre uygulanmadı: yetkisiz kullanıcı bakım durumuna göre elenmiş liste görmez


def test_vehicle_page_shows_maintenance_with_maintenance_view_and_buttons_follow_permissions(app, client, world):
    html = html_for(app, client, {"vehicles.view", "maintenance.view", "maintenance.void"},
                    f"/araclar/{world['vehicle']}")
    assert "Yağ değişti" in html and "İptal et" in html
    assert "/bakim-ekle" not in html and "/duzenle" not in html
    client.post("/cikis")
    make_profile(app, "kisi2", ROLE_BIRIM_SORUMLUSU, {"vehicles.view", "maintenance.view", "maintenance.create"})
    login(client, "kisi2")
    html = client.get(f"/araclar/{world['vehicle']}").get_data(as_text=True)
    assert "/bakim-ekle" in html and "İptal et" not in html


def test_ticket_buttons_follow_permissions(app, client, world):
    html = html_for(app, client, {"tickets.view_all"}, f"/talepler/{world['ticket']}")
    assert "Sorumlu ata" not in html and "Durumu değiştir" not in html
    client.post("/cikis")
    make_profile(app, "kisi2", ROLE_BIRIM_SORUMLUSU, {"tickets.view_all", "tickets.assign", "tickets.manage"})
    login(client, "kisi2")
    html = client.get(f"/talepler/{world['ticket']}").get_data(as_text=True)
    assert "Sorumlu ata" in html and "Durumu değiştir" in html


def test_ticket_list_new_button_requires_create(app, client, world):
    assert "Yeni talep" not in html_for(app, client, {"tickets.view_all"}, "/talepler/")
