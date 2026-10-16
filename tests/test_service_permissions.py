"""Aşama 5.2: servis katmanı, route denetimi atlansa bile kendi iznini doğrular.

Her servis fonksiyonu için: gereken izin dışındaki TÜM izinlere sahip kullanıcı reddedilir (hiçbir kayıt ve
denetim satırı oluşmaz); yalnızca gereken izne sahip kullanıcı yetki duvarına takılmaz.
"""
from datetime import date

import pytest

from app.extensions import db
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.ticket import Ticket
from app.models.user import User
from app.models.vehicle import MaintenanceRecord, Vehicle
from app.permissions import GRANTABLE, ROLE_BIRIM_SORUMLUSU, ROLE_SUPERADMIN
from app.services import assets as asset_svc
from app.services import tickets as ticket_svc
from app.services import users as user_svc
from app.services import vehicles as vehicle_svc
from app.services.authz import PermissionDenied, ensure_any_permission, ensure_permission
from tests.authz_helpers import make_profile
from tests.conftest import TEST_PASSWORD, login
from tests.ticket_helpers import make_ticket
from tests.vehicle_helpers import add_record, make_vehicle, vehicle_data

ASSET_DATA = {"code": "DMB-9", "name": "Yeni", "category": "Bilgisayar", "brand": None, "model": None,
              "serial_number": None, "location": None, "acquisition_date": None, "description": None,
              "status": "in_storage"}


@pytest.fixture()
def ctx(app):
    root = make_profile(app, "kok", ROLE_SUPERADMIN)
    with app.app_context():
        asset = Asset(code="DMB-1", name="Bilgisayar", category="Bilgisayar", status="in_storage")
        db.session.add(asset)
        db.session.commit()
        asset_id = asset.id
    vehicle_id = make_vehicle(app, root)
    record_id = add_record(app, vehicle_id, root)
    ticket_id = make_ticket(app, root)
    target = make_profile(app, "hedef", ROLE_BIRIM_SORUMLUSU, {"tickets.update", "tickets.view"})
    return {"root": root, "asset": asset_id, "vehicle": vehicle_id, "record": record_id, "ticket": ticket_id,
            "target": target}


def _g(model, pk):
    return db.session.get(model, pk)


# (ad, gereken izin, çağrı(ctx, actor)) — çağrılar actor'ı servis fonksiyonuna verir.
CASES = [
    ("create_asset", "assets.create", lambda c, a: asset_svc.create_asset(dict(ASSET_DATA), a)),
    ("update_asset", "assets.update",
     lambda c, a: asset_svc.update_asset(_g(Asset, c["asset"]), {**ASSET_DATA, "name": "Değişti"}, a, locked=False)),
    ("assign_asset", "assets.assign",
     lambda c, a: asset_svc.assign_asset(_g(Asset, c["asset"]), _g(User, c["target"]), date.today(), None, a)),
    ("return_asset", "assets.assign",
     lambda c, a: asset_svc.return_asset(_g(Asset, c["asset"]), date.today(), "in_storage", None, a)),
    ("archive_asset", "assets.update", lambda c, a: asset_svc.archive_asset(_g(Asset, c["asset"]), a)),
    ("restore_asset", "assets.update", lambda c, a: asset_svc.restore_asset(_g(Asset, c["asset"]), a)),
    ("create_vehicle", "vehicles.create",
     lambda c, a: vehicle_svc.create_vehicle(
         {**vehicle_data(identifier="34XYZ99"), "current_meter": 5, "model_year": 2020}, a)),
    ("update_vehicle", "vehicles.update",
     lambda c, a: vehicle_svc.update_vehicle(
         _g(Vehicle, c["vehicle"]), {**vehicle_data(identifier="78ABC123"), "current_meter": 20000, "model_year": 2020}, a)),
    ("archive_vehicle", "vehicles.update", lambda c, a: vehicle_svc.archive_vehicle(_g(Vehicle, c["vehicle"]), a)),
    ("restore_vehicle", "vehicles.update", lambda c, a: vehicle_svc.restore_vehicle(_g(Vehicle, c["vehicle"]), a)),
    ("add_maintenance", "maintenance.create",
     lambda c, a: vehicle_svc.add_maintenance(
         _g(Vehicle, c["vehicle"]),
         {"maintenance_type": "repair", "maintenance_date": date.today(), "meter_reading": 10000,
          "description": "Onarım yapıldı"}, a)),
    ("void_maintenance", "maintenance.void",
     lambda c, a: vehicle_svc.void_maintenance(_g(MaintenanceRecord, c["record"]), "Hatalı giriş yapıldı", a)),
    ("create_ticket", "tickets.create",
     lambda c, a: ticket_svc.create_ticket(a, "Başlık", "Açıklama", "printer", "Oda 1")),
    ("assign_ticket", "tickets.assign",
     lambda c, a: ticket_svc.assign_ticket(_g(Ticket, c["ticket"]), _g(User, c["target"]), a, None)),
    ("create_user", "users.manage",
     lambda c, a: user_svc.create_user(a, username="yeni.kisi", full_name="Yeni Kişi", password=TEST_PASSWORD,
                                       role="goruntuleyici")),
    ("update_user", "users.manage",
     lambda c, a: user_svc.update_user(a, _g(User, c["target"]), full_name="Başka Ad", role="goruntuleyici",
                                       seniority=3, permissions=())),
    ("set_user_active", "users.manage", lambda c, a: user_svc.set_user_active(a, _g(User, c["target"]), False)),
]


def _state(app):
    with app.app_context():
        return {
            "audit": db.session.scalar(db.select(db.func.count()).select_from(AuditLog)),
            "assets": db.session.execute(db.text("SELECT id, name, status, archived FROM assets")).all(),
            "vehicles": db.session.execute(db.text("SELECT id, status, archived, current_meter FROM vehicles")).all(),
            "records": db.session.scalar(db.select(db.func.count()).select_from(MaintenanceRecord)),
            "tickets": db.session.execute(db.text("SELECT id, status, assigned_to_id FROM tickets")).all(),
            "users": db.session.execute(
                db.text("SELECT id, username, full_name, role, seniority_level, active FROM users")).all(),
        }


@pytest.mark.parametrize("name,permission,call", CASES, ids=[c[0] for c in CASES])
def test_service_rejects_every_user_who_lacks_exactly_its_permission(app, ctx, name, permission, call):
    # users.manage atanamaz: "diğer her izne sahip" kullanıcı zaten onsuzdur
    actor_id = make_profile(app, "neredeyse", ROLE_BIRIM_SORUMLUSU, GRANTABLE - {permission})
    before = _state(app)
    with app.app_context():
        with pytest.raises(PermissionDenied):
            call(ctx, db.session.get(User, actor_id))
        db.session.rollback()
    assert _state(app) == before  # hiçbir kayıt ve denetim satırı oluşmadı


@pytest.mark.parametrize("name,permission,call", [c for c in CASES if c[1] != "users.manage"],
                         ids=[c[0] for c in CASES if c[1] != "users.manage"])
def test_service_does_not_block_a_user_who_has_exactly_the_permission(app, ctx, name, permission, call):
    actor_id = make_profile(app, "tek", ROLE_BIRIM_SORUMLUSU, {permission, "tickets.view", "tickets.view_all"})
    with app.app_context():
        try:
            call(ctx, db.session.get(User, actor_id))
        except PermissionDenied:
            pytest.fail(f"{name}: gereken izne sahip kullanıcı reddedildi")
        except Exception:  # noqa: BLE001 — iş kuralı hataları (ör. zimmet yok) bu testin konusu değil
            db.session.rollback()


@pytest.mark.parametrize("name,permission,call", [c for c in CASES if c[1] == "users.manage"],
                         ids=[c[0] for c in CASES if c[1] == "users.manage"])
def test_user_services_work_for_superadmin_only(app, ctx, name, permission, call):
    with app.app_context():
        call(ctx, db.session.get(User, ctx["root"]))  # süper admin: iş kuralı hatası dahi olmamalı


def test_inactive_user_with_all_permissions_is_rejected_by_services(app, ctx):
    actor_id = make_profile(app, "pasif", ROLE_BIRIM_SORUMLUSU, GRANTABLE, active=False)
    with app.app_context():
        with pytest.raises(PermissionDenied):
            asset_svc.archive_asset(db.session.get(Asset, ctx["asset"]), db.session.get(User, actor_id))


def test_ensure_helpers_reject_none_unknown_and_unprivileged(app, ctx):
    with app.app_context():
        user = db.session.get(User, ctx["target"])
        with pytest.raises(PermissionDenied):
            ensure_permission(None, "assets.view")
        with pytest.raises(PermissionDenied):
            ensure_permission(user, "assets.create")
        with pytest.raises(ValueError):
            ensure_permission(user, "assets.typo")  # yazım hatası sessizce "izin yok" olmaz
        with pytest.raises(PermissionDenied):
            ensure_any_permission(user, "assets.create", "vehicles.create")
        ensure_any_permission(user, "assets.create", "tickets.update")  # biri yeterli


def test_permission_denied_raised_inside_a_view_becomes_403_and_rolls_back(app):
    @app.route("/_test/servis-reddi")
    def _denied():
        db.session.add(Asset(code="GECICI", name="x", category="y", status="in_storage"))
        raise_denied = ensure_permission
        raise_denied(None, "assets.create")

    make_profile(app, "kisi", ROLE_BIRIM_SORUMLUSU, set())
    client = app.test_client()
    login(client, "kisi")
    assert client.get("/_test/servis-reddi").status_code == 403
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Asset)) == 0


def test_ticket_permission_error_is_also_a_permission_denied():
    assert issubclass(ticket_svc.TicketPermissionError, PermissionDenied)


def test_anonymous_user_object_has_no_permissions(app):
    from flask_login import AnonymousUserMixin

    with app.app_context():
        with pytest.raises(PermissionDenied):
            ensure_permission(AnonymousUserMixin(), "assets.view")
