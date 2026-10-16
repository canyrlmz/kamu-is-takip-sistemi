"""Aşama 5.2: her route için izin matrisi.

Her izin için YALNIZCA o izne sahip bir kullanıcı oluşturulur ve tüm uç noktalar denenir: kullanıcı yalnızca
izninin gerektirdiği uç noktalara girebilmeli, geri kalanında 403 almalıdır (GET ve POST, doğrudan URL ile).
Menü gizleme bu testlerde hiçbir rol oynamaz.
"""
from dataclasses import dataclass

import pytest

from app.extensions import db
from app.permissions import (
    GRANTABLE,
    PERMISSIONS,
    ROLE_ARIZA_KONTROL,
    ROLE_BAKIM_PERSONELI,
    ROLE_BIRIM_SORUMLUSU,
    ROLE_CEILINGS,
    ROLE_GORUNTULEYICI,
    ROLE_PRESETS,
    ROLE_SUPERADMIN,
    effective_permissions,
)
from tests.authz_helpers import insert_raw_grant, make_profile
from tests.conftest import login
from tests.ticket_helpers import make_ticket
from tests.vehicle_helpers import add_record, make_vehicle


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str  # {a} demirbaş, {t} talep, {v} araç, {r} bakım kaydı, {u} kullanıcı
    all_of: tuple = ()
    any_of: tuple = ()

    def allowed_for(self, perms) -> bool:
        perms = set(perms)
        return set(self.all_of) <= perms and (not self.any_of or bool(perms & set(self.any_of)))


TICKET_VIEW = ("tickets.view", "tickets.view_all")
ENDPOINTS = [
    # Demirbaş ve zimmet
    Endpoint("GET", "/demirbaslar/", ("assets.view",)),
    Endpoint("GET", "/demirbaslar/{a}", ("assets.view",)),
    Endpoint("GET", "/demirbaslar/yeni", ("assets.create",)),
    Endpoint("POST", "/demirbaslar/yeni", ("assets.create",)),
    Endpoint("GET", "/demirbaslar/{a}/duzenle", ("assets.update",)),
    Endpoint("POST", "/demirbaslar/{a}/duzenle", ("assets.update",)),
    Endpoint("GET", "/demirbaslar/{a}/zimmet-ver", ("assets.assign",)),
    Endpoint("POST", "/demirbaslar/{a}/zimmet-ver", ("assets.assign",)),
    Endpoint("GET", "/demirbaslar/{a}/iade-al", ("assets.assign",)),
    Endpoint("POST", "/demirbaslar/{a}/iade-al", ("assets.assign",)),
    Endpoint("POST", "/demirbaslar/{a}/arsivle", ("assets.update",)),
    Endpoint("POST", "/demirbaslar/{a}/geri-al", ("assets.update",)),
    # Arıza ve talepler
    Endpoint("GET", "/talepler/", any_of=TICKET_VIEW),
    Endpoint("GET", "/talepler/{t}", any_of=TICKET_VIEW),
    Endpoint("GET", "/talepler/yeni", ("tickets.create",)),
    Endpoint("POST", "/talepler/yeni", ("tickets.create",)),
    Endpoint("GET", "/talepler/{t}/ata", ("tickets.assign",)),
    Endpoint("POST", "/talepler/{t}/ata", ("tickets.assign",)),
    # Araç ve bakım
    Endpoint("GET", "/araclar/", ("vehicles.view",)),
    Endpoint("GET", "/araclar/{v}", ("vehicles.view",)),
    Endpoint("GET", "/araclar/yaklasan-bakimlar", ("vehicles.view", "maintenance.view")),
    Endpoint("GET", "/araclar/yeni", ("vehicles.create",)),
    Endpoint("POST", "/araclar/yeni", ("vehicles.create",)),
    Endpoint("GET", "/araclar/{v}/duzenle", ("vehicles.update",)),
    Endpoint("POST", "/araclar/{v}/duzenle", ("vehicles.update",)),
    Endpoint("POST", "/araclar/{v}/arsivle", ("vehicles.update",)),
    Endpoint("POST", "/araclar/{v}/geri-al", ("vehicles.update",)),
    Endpoint("GET", "/araclar/{v}/bakim-ekle", ("maintenance.create",)),
    Endpoint("POST", "/araclar/{v}/bakim-ekle", ("maintenance.create",)),
    Endpoint("GET", "/araclar/{v}/bakim/{r}/iptal", ("maintenance.void",)),
    Endpoint("POST", "/araclar/{v}/bakim/{r}/iptal", ("maintenance.void",)),
    # Yönetim (yalnızca süper admin: users.manage hiçbir kullanıcıya atanamaz)
    Endpoint("GET", "/yonetim/denetim-kayitlari", ("audit.view",)),
    Endpoint("GET", "/yonetim/kullanicilar", ("users.manage",)),
    Endpoint("GET", "/yonetim/kullanicilar/yeni", ("users.manage",)),
    Endpoint("POST", "/yonetim/kullanicilar/yeni", ("users.manage",)),
    Endpoint("GET", "/yonetim/kullanicilar/{u}/duzenle", ("users.manage",)),
    Endpoint("POST", "/yonetim/kullanicilar/{u}/duzenle", ("users.manage",)),
    Endpoint("POST", "/yonetim/kullanicilar/{u}/devre-disi", ("users.manage",)),
    Endpoint("POST", "/yonetim/kullanicilar/{u}/etkinlestir", ("users.manage",)),
]


@pytest.fixture()
def world(app, make_user):
    """Tüm uç noktaların çözümlenebilmesi için örnek kayıtlar. Hedef kullanıcı, süper admin olmayan 'hedef'tir."""
    from app.models.asset import Asset

    root = make_profile(app, "kok", ROLE_SUPERADMIN)
    target = make_profile(app, "hedef", ROLE_BIRIM_SORUMLUSU)
    with app.app_context():
        asset = Asset(code="DMB-1", name="Bilgisayar", category="Bilgisayar", status="in_storage")
        db.session.add(asset)
        db.session.commit()
        asset_id = asset.id
    ticket_id = make_ticket(app, root)
    vehicle_id = make_vehicle(app, root)
    record_id = add_record(app, vehicle_id, root)
    return {"a": asset_id, "t": ticket_id, "v": vehicle_id, "r": record_id, "u": target, "root": root}


def _call(client, endpoint, world):
    path = endpoint.path.format(**world)
    return client.get(path) if endpoint.method == "GET" else client.post(path, data={})


def _reached_handler(response) -> bool:
    if response.status_code in (401, 403):
        return False
    return not (response.status_code == 302 and "/giris" in response.headers.get("Location", ""))


def _assert_matrix(app, client, world, username, effective):
    login(client, username)
    for endpoint in ENDPOINTS:
        response = _call(client, endpoint, world)
        expected = endpoint.allowed_for(effective)
        assert _reached_handler(response) == expected, (
            f"{username}: {endpoint.method} {endpoint.path} -> {response.status_code}, "
            f"beklenen: {'izinli' if expected else '403'}")
        if not expected:
            assert response.status_code == 403


# --- Her izin için "yalnızca o izne sahip" kullanıcı -----------------------------------------------
@pytest.mark.parametrize("permission", sorted(GRANTABLE))
def test_single_permission_opens_only_its_own_endpoints(app, client, world, permission):
    make_profile(app, "tek", ROLE_BIRIM_SORUMLUSU, {permission})
    _assert_matrix(app, client, world, "tek", {permission})


def test_user_without_any_permission_is_denied_everywhere(app, client, world):
    make_profile(app, "bos", ROLE_BIRIM_SORUMLUSU, set())
    _assert_matrix(app, client, world, "bos", set())


def test_superadmin_reaches_every_endpoint(app, client, world):
    login(client, "kok")
    for endpoint in ENDPOINTS:
        response = _call(client, endpoint, world)
        assert _reached_handler(response), f"{endpoint.method} {endpoint.path} -> {response.status_code}"


# --- Roller: önerilen izin setiyle ve tavan dışı satırlarla ---------------------------------------
@pytest.mark.parametrize("role", [ROLE_BIRIM_SORUMLUSU, ROLE_BAKIM_PERSONELI, ROLE_ARIZA_KONTROL, ROLE_GORUNTULEYICI])
def test_each_role_with_its_preset_gets_exactly_its_effective_permissions(app, client, world, role):
    make_profile(app, "rol", role, ROLE_PRESETS[role])
    _assert_matrix(app, client, world, "rol", effective_permissions(role, ROLE_PRESETS[role]))


@pytest.mark.parametrize("role", [ROLE_BAKIM_PERSONELI, ROLE_ARIZA_KONTROL, ROLE_GORUNTULEYICI])
def test_role_ceiling_blocks_every_out_of_ceiling_grant_even_if_row_exists(app, client, world, role):
    """Veritabanına elle tavan dışı izin satırı eklenmiş olsa bile (en yetkili senaryo) rol tavanı geçerlidir."""
    uid = make_profile(app, "tavan", role, set())
    for permission in GRANTABLE - ROLE_CEILINGS[role]:
        insert_raw_grant(app, uid, permission)
    _assert_matrix(app, client, world, "tavan", set())  # hiçbir satır etkin değil


def test_viewer_cannot_write_anywhere_even_with_every_grantable_row(app, client, world):
    uid = make_profile(app, "izleyici", ROLE_GORUNTULEYICI, set())
    for permission in GRANTABLE:
        if permission not in ROLE_CEILINGS[ROLE_GORUNTULEYICI]:
            insert_raw_grant(app, uid, permission)
    login(client, "izleyici")
    for endpoint in ENDPOINTS:
        if endpoint.method == "POST":
            assert _call(client, endpoint, world).status_code == 403, endpoint.path


def test_inactive_account_loses_every_permission(app, client, world):
    make_profile(app, "pasif", ROLE_BIRIM_SORUMLUSU, set(GRANTABLE), active=False)
    assert login(client, "pasif").status_code == 401
    for endpoint in ENDPOINTS[:6]:
        assert not _reached_handler(_call(client, endpoint, world))


def test_anonymous_is_redirected_to_login_on_every_endpoint(client, world):
    for endpoint in ENDPOINTS:
        response = _call(client, endpoint, world)
        assert response.status_code == 302 and "/giris" in response.headers["Location"], endpoint.path


def test_every_registered_state_changing_route_is_covered_by_the_matrix(app):
    """Yeni bir yazma route'u eklenip matrise girilmezse bu test düşer (yetki atlaması kalmasın)."""
    covered = {(e.method, e.path.replace("{a}", "<int:asset_id>").replace("{t}", "<int:ticket_id>")
                .replace("{v}", "<int:vehicle_id>").replace("{r}", "<int:record_id>").replace("{u}", "<int:user_id>"))
               for e in ENDPOINTS}
    # tickets.status: kendi kuralı (durum geçişi izni talebe ve kullanıcıya bağlı) test_ticket_permissions.py'de test edilir.
    separately_tested = {"tickets.status"}
    prefixes = ("/demirbaslar", "/talepler", "/araclar", "/yonetim")
    for rule in app.url_map.iter_rules():
        if rule.endpoint in separately_tested or not rule.rule.startswith(prefixes):
            continue
        for method in rule.methods & {"GET", "POST"}:
            assert (method, rule.rule) in covered, f"Matriste yok: {method} {rule.rule}"


def test_permission_keys_used_in_matrix_exist():
    for endpoint in ENDPOINTS:
        assert set(endpoint.all_of) | set(endpoint.any_of) <= set(PERMISSIONS)
