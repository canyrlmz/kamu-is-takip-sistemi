from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from app import create_app
from app.extensions import db

MIGRATIONS = str(Path(__file__).resolve().parents[1] / "migrations")
STAGE1_REVISION = "da9bc564aab8"
STAGE2_REVISION = "0a6f12ac96e2"
STAGE3_REVISION = "84674ae864e7"
TICKET_TRIGGERS = ("trg_ticket_events_no_update", "trg_ticket_events_no_delete")


def _script_directory():
    config = Config()
    config.set_main_option("script_location", MIGRATIONS)
    return ScriptDirectory.from_config(config)


def _new_app(tmp_path, name):
    return create_app("testing", {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / name}"})


def _seed_stage2_data(session):
    session.execute(text(
        "INSERT INTO users (id, username, full_name, password_hash, role, active, created_at, updated_at) "
        "VALUES (1, 'eskiyonetici', 'Eski Yönetici', 'hash', 'admin', 1, '2026-01-01', '2026-01-01'), "
        "(2, 'eskipersonel', 'Eski Personel', 'hash', 'staff', 1, '2026-01-01', '2026-01-01')"))
    session.execute(text(
        "INSERT INTO assets (id, code, name, category, status, archived, created_at, updated_at) "
        "VALUES (1, 'DMB-1', 'Eski Bilgisayar', 'Bilgisayar', 'in_use', 0, '2026-01-01', '2026-01-01')"))
    session.execute(text(
        "INSERT INTO asset_assignments (id, asset_id, assignee_id, start_date, assigned_by_id, created_at) "
        "VALUES (1, 1, 2, '2026-01-02', 1, '2026-01-02')"))
    session.execute(text("INSERT INTO audit_logs (created_at, action) VALUES ('2026-01-01', 'login_success')"))
    session.commit()


def _stage2_snapshot(session):
    return {
        "users": session.execute(text("SELECT id, username, role FROM users ORDER BY id")).all(),
        "assets": session.execute(text("SELECT code, name, status FROM assets")).all(),
        "assignments": session.execute(text("SELECT asset_id, assignee_id, return_date FROM asset_assignments")).all(),
        # Aşama 5 migration'ı dönüştürülen hesaplar için YENİ denetim kayıtları ekler; mevcut kayıtlar aynen korunmalıdır.
        "audit": session.execute(text("SELECT id, action FROM audit_logs WHERE action <> 'user_role_changed' ORDER BY id")).all(),
    }


def _trigger_names(session):
    return {r[0] for r in session.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}


def test_migration_chain_continues_from_stage2():
    script = _script_directory()
    assert len(script.get_heads()) == 1, "Migration zincirinde dallanma var"
    # Aşama 3 migration'ı doğrudan Aşama 2'nin devamıdır ve sonraki aşamalar da onun üzerine kurulur.
    assert script.get_revision(STAGE3_REVISION).down_revision == STAGE2_REVISION
    assert script.get_revision(STAGE2_REVISION).down_revision == STAGE1_REVISION
    ancestors = {r.revision for r in script.iterate_revisions(script.get_current_head(), STAGE1_REVISION)}
    assert STAGE3_REVISION in ancestors, "Aşama 3 migration'ı güncel head'in atası olmalı"


def test_stage3_upgrade_preserves_existing_data_and_adds_tables(tmp_path):
    app = _new_app(tmp_path, "up.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE2_REVISION)
        assert "tickets" not in inspect(db.engine).get_table_names()
        _seed_stage2_data(db.session)
        before = _stage2_snapshot(db.session)

        upgrade(directory=MIGRATIONS, revision=STAGE3_REVISION)

        tables = set(inspect(db.engine).get_table_names())
        assert {"tickets", "ticket_events", "users", "assets", "asset_assignments", "audit_logs"} <= tables
        assert _stage2_snapshot(db.session) == before  # mevcut veriler birebir korunur
        columns = {c["name"] for c in inspect(db.engine).get_columns("tickets")}
        assert {"title", "description", "category", "location", "priority", "status", "created_by_id",
                "assigned_to_id", "created_at", "updated_at", "closed_at", "resolution_note"} <= columns


def test_migrated_schema_enforces_constraints_and_immutable_history(tmp_path):
    app = _new_app(tmp_path, "rules.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE3_REVISION)
        _seed_stage2_data(db.session)
        insert_ticket = (
            "INSERT INTO tickets (title, description, category, location, priority, status, created_by_id, "
            "created_at, updated_at, closed_at) VALUES ('T', 'D', :cat, 'L', :pri, :st, 2, '2026-01-01', '2026-01-01', :closed)"
        )
        ok = {"cat": "printer", "pri": "normal", "st": "new", "closed": None}
        db.session.execute(text(insert_ticket), ok)
        db.session.commit()

        for bad in ({**ok, "st": "bogus"}, {**ok, "cat": "bogus"}, {**ok, "pri": "bogus"},
                    {**ok, "st": "resolved"}, {**ok, "closed": "2026-01-02"}):
            with pytest.raises(DBAPIError):
                db.session.execute(text(insert_ticket), bad)
            db.session.rollback()
        assert db.session.execute(text("SELECT count(*) FROM tickets")).scalar() == 1

        db.session.execute(text(
            "INSERT INTO ticket_events (ticket_id, event_type, to_status, actor_id, created_at) "
            "VALUES (1, 'created', 'new', 2, '2026-01-01')"))
        db.session.commit()
        # Doğrudan SQL ile bile geçmiş değiştirilemez / silinemez (veritabanı tetikleyicileri)
        with pytest.raises(DBAPIError, match="değiştirilemez"):
            db.session.execute(text("UPDATE ticket_events SET note = 'gizlice' WHERE id = 1"))
        db.session.rollback()
        with pytest.raises(DBAPIError, match="silinemez"):
            db.session.execute(text("DELETE FROM ticket_events WHERE id = 1"))
        db.session.rollback()
        assert db.session.execute(text("SELECT note FROM ticket_events WHERE id = 1")).scalar() is None


def test_stage3_downgrade_removes_only_ticket_tables(tmp_path):
    app = _new_app(tmp_path, "down.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE2_REVISION)
        _seed_stage2_data(db.session)
        before = _stage2_snapshot(db.session)
        upgrade(directory=MIGRATIONS, revision="head")
        assert _trigger_names(db.session) >= set(TICKET_TRIGGERS)  # geçmiş tetikleyicileri head'de de durur

        downgrade(directory=MIGRATIONS, revision=STAGE2_REVISION)

        tables = set(inspect(db.engine).get_table_names())
        assert "tickets" not in tables and "ticket_events" not in tables
        assert {"users", "assets", "asset_assignments", "audit_logs"} <= tables
        assert _stage2_snapshot(db.session) == before
        assert _trigger_names(db.session) == set()

        upgrade(directory=MIGRATIONS, revision="head")  # tekrar yükseltme sorunsuz
        assert "tickets" in inspect(db.engine).get_table_names()
