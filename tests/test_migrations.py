from pathlib import Path

from flask_migrate import upgrade
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db

MIGRATIONS = str(Path(__file__).resolve().parents[1] / "migrations")
STAGE1_REVISION = "da9bc564aab8"
STAGE2_REVISION = "0a6f12ac96e2"


def test_stage2_migration_keeps_existing_data_and_adds_tables(tmp_path):
    app = create_app("testing", {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'migration.db'}"})
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE1_REVISION)
        assert "assets" not in inspect(db.engine).get_table_names()
        db.session.execute(text(
            "INSERT INTO users (username, full_name, password_hash, role, active, created_at, updated_at) "
            "VALUES ('eskiyonetici', 'Eski Yönetici', 'hash', 'admin', 1, '2026-01-01', '2026-01-01')"
        ))
        db.session.execute(text(
            "INSERT INTO audit_logs (created_at, action) VALUES ('2026-01-01', 'login_success')"
        ))
        db.session.commit()

        upgrade(directory=MIGRATIONS, revision=STAGE2_REVISION)

        tables = inspect(db.engine).get_table_names()
        assert {"assets", "asset_assignments"} <= set(tables)
        assert db.session.execute(text("SELECT username, role FROM users")).all() == [("eskiyonetici", "admin")]
        assert db.session.execute(text("SELECT count(*) FROM audit_logs")).scalar() == 1


def test_migrated_schema_enforces_single_active_assignment(tmp_path):
    from sqlalchemy.exc import IntegrityError

    app = create_app("testing", {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'index.db'}"})
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE2_REVISION)
        db.session.execute(text(
            "INSERT INTO users (id, username, full_name, password_hash, role, active, created_at, updated_at) "
            "VALUES (1, 'u', 'U', 'h', 'admin', 1, '2026-01-01', '2026-01-01')"))
        db.session.execute(text(
            "INSERT INTO assets (id, code, name, category, status, archived, created_at, updated_at) "
            "VALUES (1, 'A-1', 'x', 'y', 'in_storage', 0, '2026-01-01', '2026-01-01')"))
        insert = ("INSERT INTO asset_assignments (asset_id, assignee_id, start_date, return_date, assigned_by_id, created_at) "
                  "VALUES (1, 1, :s, :r, 1, '2026-01-01')")
        db.session.execute(text(insert), {"s": "2026-01-01", "r": "2026-01-02"})  # geçmiş kayıtlar serbest
        db.session.execute(text(insert), {"s": "2026-01-03", "r": "2026-01-04"})
        db.session.execute(text(insert), {"s": "2026-01-05", "r": None})  # bir aktif zimmet serbest
        db.session.commit()
        try:
            db.session.execute(text(insert), {"s": "2026-01-06", "r": None})
            db.session.commit()
            raise AssertionError("ikinci aktif zimmet kabul edildi")
        except IntegrityError:
            db.session.rollback()
