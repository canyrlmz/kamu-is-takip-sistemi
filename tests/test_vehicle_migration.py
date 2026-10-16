"""Aşama 4.1: araç / bakım şeması ve migration'ı (yalnızca geçici test veritabanlarında)."""
from datetime import date
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from app import create_app
from app.extensions import db
from app.models import MaintenanceRecord, MaintenanceVoid, Vehicle

MIGRATIONS = str(Path(__file__).resolve().parents[1] / "migrations")
STAGE3_REVISION = "84674ae864e7"
STAGE4_REVISION = "b4c7e1a9d352"
NEW_TABLES = {"vehicles", "maintenance_records", "maintenance_voids"}
NEW_TRIGGERS = {
    "trg_maint_records_no_update", "trg_maint_records_no_delete",
    "trg_maint_voids_no_update", "trg_maint_voids_no_delete", "trg_vehicles_meter_type_locked",
}
TICKET_TRIGGERS = {"trg_ticket_events_no_update", "trg_ticket_events_no_delete"}


def _new_app(tmp_path, name):
    return create_app("testing", {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / name}"})


def _triggers(session):
    return {r[0] for r in session.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}


def _seed_stage3(session):
    session.execute(text(
        "INSERT INTO users (id, username, full_name, password_hash, role, active, created_at, updated_at) "
        "VALUES (1, 'yonetici', 'Yönetici', 'h', 'admin', 1, '2026-01-01', '2026-01-01'), "
        "(2, 'personel', 'Personel', 'h', 'staff', 1, '2026-01-01', '2026-01-01')"))
    session.execute(text(
        "INSERT INTO assets (id, code, name, category, status, archived, created_at, updated_at) "
        "VALUES (1, 'DMB-1', 'Bilgisayar', 'Bilgisayar', 'in_storage', 0, '2026-01-01', '2026-01-01')"))
    session.execute(text(
        "INSERT INTO tickets (title, description, category, location, priority, status, created_by_id, "
        "created_at, updated_at) VALUES ('T', 'D', 'printer', 'L', 'normal', 'new', 2, '2026-01-01', '2026-01-01')"))
    session.execute(text(
        "INSERT INTO ticket_events (ticket_id, event_type, to_status, actor_id, created_at) "
        "VALUES (1, 'created', 'new', 2, '2026-01-01')"))
    session.execute(text("INSERT INTO audit_logs (created_at, action) VALUES ('2026-01-01', 'login_success')"))
    session.commit()


def _snapshot(session):
    q = lambda sql: session.execute(text(sql)).all()  # noqa: E731
    return {
        "users": q("SELECT id, username, role FROM users ORDER BY id"),
        "assets": q("SELECT code, name, status FROM assets"),
        "tickets": q("SELECT id, title, status FROM tickets"),
        "events": q("SELECT ticket_id, event_type FROM ticket_events"),
        # Aşama 5 migration'ı dönüştürülen hesaplar için yeni denetim kayıtları ekler; mevcut kayıtlar aynen korunmalıdır.
        "audit": q("SELECT id, action FROM audit_logs WHERE action <> 'user_role_changed' ORDER BY id"),
    }


def _vehicle(session, ident="78ABC123", meter="km", **extra):
    values = {"id": 1, "ident": ident, "meter": meter, "status": "active", "archived": 0, "year": None,
              "current": 0, **extra}
    session.execute(text(
        "INSERT INTO vehicles (id, vehicle_type, identifier, model_year, meter_type, current_meter, status, "
        "archived, created_by_id, created_at, updated_at) VALUES (:id, 'Kamyonet', :ident, :year, :meter, "
        ":current, :status, :archived, 1, '2026-01-01', '2026-01-01')"), values)


_RECORD = (
    "INSERT INTO maintenance_records (vehicle_id, maintenance_type, maintenance_date, meter_reading, description, "
    "cost_kurus, next_date, next_meter, created_by_id, created_at) VALUES "
    "(1, :type, :date, :reading, 'Bakım yapıldı', :cost, :next_date, :next_meter, 1, '2026-01-01')"
)
_OK_RECORD = {"type": "periodic", "date": "2026-03-01", "reading": 10000, "cost": 125050,
              "next_date": "2026-09-01", "next_meter": 20000}


def test_chain_has_single_head_after_stage3():
    config = Config()
    config.set_main_option("script_location", MIGRATIONS)
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1
    assert script.get_revision(STAGE4_REVISION).down_revision == STAGE3_REVISION
    ancestors = {r.revision for r in script.iterate_revisions(script.get_current_head(), STAGE3_REVISION)}
    assert STAGE4_REVISION in ancestors, "Aşama 4 migration'ı güncel head'in atası olmalı"


def test_upgrade_adds_only_new_objects_and_preserves_data(tmp_path):
    app = _new_app(tmp_path, "up.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE3_REVISION)
        assert not NEW_TABLES & set(inspect(db.engine).get_table_names())
        _seed_stage3(db.session)
        before = _snapshot(db.session)
        tables_before = set(inspect(db.engine).get_table_names())

        upgrade(directory=MIGRATIONS, revision=STAGE4_REVISION)

        assert set(inspect(db.engine).get_table_names()) == tables_before | NEW_TABLES
        assert _snapshot(db.session) == before
        assert _triggers(db.session) == TICKET_TRIGGERS | NEW_TRIGGERS


def test_models_match_migrated_schema(tmp_path):
    """Modeller ile migration birbirinden sapmamalı (otomatik üretim fark bulmamalı)."""
    app = _new_app(tmp_path, "drift.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="head")
        with db.engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
        assert diff == []


@pytest.mark.parametrize("sql_params", [
    {"ident": "78 ABC 123"},                 # boşluklu tanımlayıcı
    {"ident": ""},                           # boş tanımlayıcı
    {"meter": "miles"},                      # geçersiz sayaç türü
    {"status": "bogus"},                     # geçersiz durum
    {"current": -1},                         # negatif sayaç
    {"year": 1900}, {"year": 2200},          # model yılı sınır dışı
    {"archived": 1},                         # aktif araç arşive alınamaz
])
def test_vehicle_constraints_reject_bad_rows(tmp_path, sql_params):
    app = _new_app(tmp_path, "vc.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE4_REVISION)
        _seed_stage3(db.session)
        with pytest.raises(DBAPIError):
            _vehicle(db.session, **sql_params)
        db.session.rollback()
        assert db.session.execute(text("SELECT count(*) FROM vehicles")).scalar() == 0


def test_vehicle_identifier_is_unique_and_valid_rows_accepted(tmp_path):
    app = _new_app(tmp_path, "vu.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE4_REVISION)
        _seed_stage3(db.session)
        _vehicle(db.session, year=2020, current=500)
        _vehicle(db.session, ident="EKSK-01", id=2, meter="hours", status="out_of_service", archived=1)
        db.session.commit()
        with pytest.raises(DBAPIError):
            _vehicle(db.session, id=3)  # aynı tanımlayıcı
        db.session.rollback()
        assert db.session.execute(text("SELECT count(*) FROM vehicles")).scalar() == 2


@pytest.mark.parametrize("override", [
    {"type": "bogus"}, {"reading": -1}, {"cost": -1},
    {"next_date": "2026-02-28"},                 # sonraki tarih bakım tarihinden önce
    {"next_meter": 10000}, {"next_meter": 9000},  # eşik, bakım sayacından büyük olmalı
])
def test_maintenance_constraints_reject_bad_rows(tmp_path, override):
    app = _new_app(tmp_path, "mc.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE4_REVISION)
        _seed_stage3(db.session)
        _vehicle(db.session)
        db.session.commit()
        with pytest.raises(DBAPIError):
            db.session.execute(text(_RECORD), {**_OK_RECORD, **override})
        db.session.rollback()
        db.session.execute(text(_RECORD), _OK_RECORD)
        db.session.execute(text(_RECORD), {**_OK_RECORD, "cost": None, "next_date": None, "next_meter": None})
        db.session.commit()
        assert db.session.execute(text("SELECT count(*) FROM maintenance_records")).scalar() == 2


def test_records_and_voids_are_append_only_and_void_is_single_and_reasoned(tmp_path):
    app = _new_app(tmp_path, "imm.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE4_REVISION)
        _seed_stage3(db.session)
        _vehicle(db.session)
        db.session.execute(text(_RECORD), _OK_RECORD)
        db.session.commit()

        for sql, message in (
            ("UPDATE maintenance_records SET cost_kurus = 1 WHERE id = 1", "değiştirilemez"),
            ("DELETE FROM maintenance_records WHERE id = 1", "silinemez"),
        ):
            with pytest.raises(DBAPIError, match=message):
                db.session.execute(text(sql))
            db.session.rollback()

        void = "INSERT INTO maintenance_voids (record_id, reason, voided_by_id, created_at) VALUES (:r, :why, 1, '2026-01-02')"
        for bad in ({"r": 1, "why": ""}, {"r": 1, "why": "    "}, {"r": 1, "why": "kısa"}, {"r": 99, "why": "Yanlış girildi"}):
            with pytest.raises(DBAPIError):
                db.session.execute(text(void), bad)
            db.session.rollback()
        db.session.execute(text(void), {"r": 1, "why": "Sayaç yanlış girildi"})
        db.session.commit()
        with pytest.raises(DBAPIError):  # aynı kayıt iki kez iptal edilemez
            db.session.execute(text(void), {"r": 1, "why": "Tekrar iptal"})
        db.session.rollback()
        for sql, message in (
            ("UPDATE maintenance_voids SET reason = 'gizlice değişti' WHERE id = 1", "değiştirilemez"),
            ("DELETE FROM maintenance_voids WHERE id = 1", "silinemez"),
        ):
            with pytest.raises(DBAPIError, match=message):
                db.session.execute(text(sql))
            db.session.rollback()
        # İptal, asıl kaydı değiştirmez
        assert db.session.execute(text("SELECT cost_kurus FROM maintenance_records WHERE id = 1")).scalar() == 125050


def test_meter_type_locks_once_maintenance_exists(tmp_path):
    app = _new_app(tmp_path, "lock.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE4_REVISION)
        _seed_stage3(db.session)
        _vehicle(db.session)
        db.session.commit()
        # Kayıt yokken sayaç türü düzeltilebilir
        db.session.execute(text("UPDATE vehicles SET meter_type = 'hours' WHERE id = 1"))
        db.session.commit()
        db.session.execute(text(_RECORD), _OK_RECORD)
        db.session.commit()
        with pytest.raises(DBAPIError, match="sayaç türü"):
            db.session.execute(text("UPDATE vehicles SET meter_type = 'km' WHERE id = 1"))
        db.session.rollback()
        # Diğer alanlar ve aynı değere yazma serbest kalır
        db.session.execute(text("UPDATE vehicles SET meter_type = 'hours', current_meter = 10050 WHERE id = 1"))
        db.session.commit()


def test_orm_blocks_changes_to_records_and_voids(tmp_path):
    app = _new_app(tmp_path, "orm.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE4_REVISION)
        _seed_stage3(db.session)
        vehicle = Vehicle(vehicle_type="Kamyonet", identifier="78ABC123", meter_type="km", current_meter=0,
                          created_by_id=1)
        db.session.add(vehicle)
        db.session.flush()
        record = MaintenanceRecord(vehicle_id=vehicle.id, maintenance_type="periodic", maintenance_date=date(2026, 3, 1),
                                   meter_reading=1000, description="Bakım", created_by_id=1)
        db.session.add(record)
        db.session.commit()
        db.session.add(MaintenanceVoid(record_id=record.id, reason="Yanlış kayıt", voided_by_id=1))
        db.session.commit()
        db.session.refresh(record)
        assert record.is_void and record.void.reason == "Yanlış kayıt"

        record.description = "gizlice"
        with pytest.raises(RuntimeError, match="değiştirilemez"):
            db.session.commit()
        db.session.rollback()
        db.session.delete(record.void)
        with pytest.raises(RuntimeError, match="silinemez"):
            db.session.commit()
        db.session.rollback()


def test_downgrade_removes_only_stage4_objects_and_reupgrade_works(tmp_path):
    app = _new_app(tmp_path, "down.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE3_REVISION)
        _seed_stage3(db.session)
        before = _snapshot(db.session)
        tables_at_stage3 = set(inspect(db.engine).get_table_names())
        upgrade(directory=MIGRATIONS, revision="head")
        _vehicle(db.session)
        db.session.commit()

        downgrade(directory=MIGRATIONS, revision=STAGE3_REVISION)

        assert set(inspect(db.engine).get_table_names()) == tables_at_stage3
        assert _triggers(db.session) == TICKET_TRIGGERS  # Aşama 3 tetikleyicileri kalır
        assert _snapshot(db.session) == before

        upgrade(directory=MIGRATIONS, revision=STAGE4_REVISION)  # bu test Aşama 4 nesnelerini sınar
        assert NEW_TABLES <= set(inspect(db.engine).get_table_names())
        assert _snapshot(db.session) == before
