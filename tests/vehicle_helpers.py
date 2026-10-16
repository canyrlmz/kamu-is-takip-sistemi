"""Araç / bakım testleri için ortak yardımcılar (servis katmanı üzerinden kayıt oluşturur)."""
from datetime import date, timedelta

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.vehicle import MaintenanceRecord, Vehicle
from app.services import vehicles as svc
from app.utils import today_local


def vehicle_data(**overrides):
    data = {
        "vehicle_type": "Kamyonet", "identifier": "78 ABC 123", "brand": "Ford", "model": "Transit",
        "model_year": "2020", "meter_type": "km", "current_meter": "10000", "status": "active",
        "description": "Fen işleri aracı",
    }
    data.update(overrides)
    return data


def maintenance_data(app, **overrides):
    with app.app_context():
        today = today_local()
    data = {
        "maintenance_type": "periodic", "maintenance_date": today.isoformat(), "meter_reading": "10000",
        "description": "Periyodik bakım yapıldı", "provider": "Merkez Servis", "cost": "1250,50",
        "next_date": "", "next_meter": "", "notes": "",
    }
    data.update(overrides)
    return data


def make_vehicle(app, user_id, identifier="78ABC123", meter_type="km", current_meter=10000, status="active", **kw):
    with app.app_context():
        vehicle = svc.create_vehicle(
            {"vehicle_type": kw.get("vehicle_type", "Kamyonet"), "identifier": identifier, "brand": None, "model": None,
             "model_year": None, "meter_type": meter_type, "current_meter": current_meter, "status": status,
             "description": None},
            db.session.get(User, user_id))
        return vehicle.id


def add_record(app, vehicle_id, user_id, days_ago=0, mtype="periodic", reading=10000, description=None,
               next_in_days=None, next_meter=None, cost_kurus=None):
    """Bakım kaydı ekler. `days_ago`: bugünden kaç gün önce; `next_in_days`: bugünden kaç gün sonra planlı."""
    with app.app_context():
        today = today_local()
        record = svc.add_maintenance(
            db.session.get(Vehicle, vehicle_id),
            {"maintenance_type": mtype, "maintenance_date": today - timedelta(days=days_ago),
             "meter_reading": reading, "description": description or f"{mtype} {days_ago} {reading}",
             "cost_kurus": cost_kurus,
             "next_date": today + timedelta(days=next_in_days) if next_in_days is not None else None,
             "next_meter": next_meter},
            db.session.get(User, user_id))
        return record.id


def void_record(app, record_id, user_id, reason="Hatalı giriş yapıldı"):
    with app.app_context():
        svc.void_maintenance(db.session.get(MaintenanceRecord, record_id), reason, db.session.get(User, user_id))


def alert_of(app, vehicle_id):
    """Servis katmanından güncel uyarı (önbellek yok)."""
    with app.app_context():
        vehicle = db.session.get(Vehicle, vehicle_id)
        return svc.fleet_alerts([vehicle])[vehicle_id]


def audit_actions(app):
    with app.app_context():
        return [a.action for a in db.session.scalars(db.select(AuditLog).order_by(AuditLog.id))]


def counts(app):
    with app.app_context():
        return (db.session.scalar(db.select(db.func.count()).select_from(Vehicle)),
                db.session.scalar(db.select(db.func.count()).select_from(MaintenanceRecord)))
