"""Aşama 5 migration hata güvenliği.

SQLite'ta Alembic varsayılan olarak "transactional DDL yok" kabul eder ve pysqlite DDL öncesi BEGIN yapmaz; bu
yüzden eskiden CREATE TABLE adımları kalıcılaşıp hata sonrası yarım tablo bırakabiliyordu. migrations/env.py artık
tüm migration'ı tek bir gerçek işlemde çalıştırır. Bu dosya, migration'ın HER adımında hata üretip veritabanının
bayt düzeyinde değil ama TÜM İÇERİĞİ (şema, her tablonun her satırı, alembic_version) ile aynı kaldığını doğrular.
Not: işlem kesintisi/güç kaybı/disk dolması gibi altyapı arızaları burada simüle edilemez; bunlara karşı güvence
SQLite'ın atomik commit'i ve kurulum öncesi alınan YEDEKTİR.
"""
import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_migrate import downgrade, upgrade
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from app.extensions import db
from tests.test_role_migration import MIGRATIONS, STAGE4, STAGE5, _prepare

MIGRATION_FILE = next(Path(MIGRATIONS, "versions").glob("c5d8f2b7a461_*.py"))

# (etiket, SQL deseni) — migration'ın upgrade() içindeki her adım ve Alembic'in sürüm güncellemesi
UPGRADE_FAILURE_POINTS = [
    ("create users_new", r"CREATE TABLE users_new"),
    ("create user_permissions", r"CREATE TABLE user_permissions"),
    ("insert users_new", r"INSERT INTO users_new"),
    ("insert izin (staff)", r"INSERT INTO user_permissions .*'staff'"),
    ("insert izin (pasif admin)", r"INSERT INTO user_permissions .*o\.active = 0"),
    ("insert denetim", r"INSERT INTO audit_logs"),
    ("drop users", r"DROP TABLE users\b"),
    ("rename users_new", r"ALTER TABLE users_new RENAME"),
    ("index ix_users_username", r"CREATE UNIQUE INDEX ix_users_username"),
    ("index tek süper admin", r"CREATE UNIQUE INDEX ux_users_single_active_superadmin"),
    ("alembic_version güncelle", r"UPDATE alembic_version"),
]
DOWNGRADE_FAILURE_POINTS = [
    ("create users_old", r"CREATE TABLE users_old"),
    ("insert users_old", r"INSERT INTO users_old"),
    ("drop user_permissions", r"DROP TABLE user_permissions"),
    ("drop users", r"DROP TABLE users\b"),
    ("rename users_old", r"ALTER TABLE users_old RENAME"),
    ("index ix_users_username", r"CREATE UNIQUE INDEX ix_users_username"),
    ("alembic_version güncelle", r"UPDATE alembic_version"),
]
USERS = [(1, "yonetici", "admin", 1), (2, "personel", "staff", 1), (3, "personel2", "staff", 0),
         (4, "eski_yonetici", "admin", 0)]


class _Injector:
    """Belirli bir SQL ifadesinde (veya commit'te) hata üretir; ilk eşleşmede bir kez tetiklenir."""

    def __init__(self):
        self.pattern = None
        self.on_commit = False
        self.fired = False

    def _statement(self, conn, cursor, statement, parameters, context, executemany):
        text_ = " ".join(statement.split())
        if self.pattern and not self.fired and re.search(self.pattern, text_):
            self.fired = True
            raise RuntimeError(f"ENJEKTE EDILEN HATA: {text_[:60]}")

    def _commit(self, conn):
        if self.on_commit and not self.fired:
            self.fired = True
            raise RuntimeError("ENJEKTE EDILEN HATA: commit")


@pytest.fixture
def inject():
    inj = _Injector()
    event.listen(Engine, "before_cursor_execute", inj._statement)
    event.listen(Engine, "commit", inj._commit)
    yield inj
    event.remove(Engine, "before_cursor_execute", inj._statement)
    event.remove(Engine, "commit", inj._commit)


def _fingerprint(path):
    """Veritabanının tüm içeriği: şema (sqlite_master), her tablonun her satırı, kullanıcı sürümü, FK ihlali."""
    con = sqlite3.connect(path)
    try:
        schema = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        rows = {}
        for t in tables:
            columns = len(con.execute(f'PRAGMA table_info("{t}")').fetchall())
            order = ", ".join(str(i) for i in range(1, columns + 1))  # tüm sütunlara göre: kararlı sıralama
            rows[t] = con.execute(f'SELECT * FROM "{t}" ORDER BY {order}').fetchall()
        return {"schema": schema, "rows": rows, "fk_violations": con.execute("PRAGMA foreign_key_check").fetchall()}
    finally:
        con.close()


def _db_path(app):
    return Path(app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", ""))


def _fk_state_of_fresh_connection():
    with db.engine.connect() as conn:
        return conn.exec_driver_sql("PRAGMA foreign_keys").scalar()


def _assert_nothing_changed(app, before, revision):
    db.session.rollback()
    db.engine.dispose()
    after = _fingerprint(_db_path(app))
    assert after["schema"] == before["schema"], "şema (tablolar/indeksler) değişti"
    for table, rows in before["rows"].items():
        assert after["rows"][table] == rows, f"{table} tablosunun içeriği değişti"
    assert set(after["rows"]) == set(before["rows"]), "tablo kümesi değişti"
    assert after["fk_violations"] == []
    assert after["rows"]["alembic_version"] == [(revision,)]
    assert _fk_state_of_fresh_connection() == 1  # yabancı anahtar denetimi yeniden açık


@pytest.mark.parametrize("label,pattern", UPGRADE_FAILURE_POINTS, ids=[p[0] for p in UPGRADE_FAILURE_POINTS])
def test_upgrade_failure_at_every_step_leaves_database_unchanged_and_retry_works(tmp_path, inject, label, pattern):
    app = _prepare(tmp_path, USERS)
    with app.app_context():
        before = _fingerprint(_db_path(app))
        users_before, grants_before = before["rows"]["users"], before["rows"].get("user_permissions")
        assert grants_before is None  # Aşama 4 şemasında izin tablosu yok

        inject.pattern = pattern
        with pytest.raises((SystemExit, RuntimeError)):
            upgrade(directory=MIGRATIONS, revision="head")
        assert inject.fired, "hata enjeksiyonu tetiklenmedi (desen migration ifadeleriyle eşleşmiyor)"

        _assert_nothing_changed(app, before, STAGE4)
        # kullanıcılar, izin tablosu yok, denetim kayıtları aynı (parmak izi zaten tüm satırları kapsar; açık doğrulama):
        after = _fingerprint(_db_path(app))
        assert after["rows"]["users"] == users_before
        assert "user_permissions" not in after["rows"] and "users_new" not in after["rows"]
        assert after["rows"]["audit_logs"] == before["rows"]["audit_logs"]

        # Hata giderilince aynı veritabanında migration sorunsuz yeniden çalışır
        inject.pattern = None
        upgrade(directory=MIGRATIONS, revision="head")
        db.session.rollback()
        done = _fingerprint(_db_path(app))
        assert done["rows"]["alembic_version"] == [(STAGE5,)]
        assert [(r[1], r[4]) for r in done["rows"]["users"]] == [
            ("yonetici", "superadmin"), ("personel", "ariza_kontrol"), ("personel2", "ariza_kontrol"),
            ("eski_yonetici", "birim_sorumlusu")]
        assert done["fk_violations"] == []


def test_upgrade_failure_during_commit_leaves_database_unchanged(tmp_path, inject):
    app = _prepare(tmp_path, USERS)
    with app.app_context():
        before = _fingerprint(_db_path(app))
        inject.on_commit = True
        with pytest.raises((SystemExit, RuntimeError)):
            upgrade(directory=MIGRATIONS, revision="head")
        assert inject.fired
        _assert_nothing_changed(app, before, STAGE4)


def test_failed_upgrade_keeps_related_tables_and_audit_rows_intact(tmp_path, inject):
    """Bağlı tablolar (demirbaş, zimmet, talep, araç, bakım, denetim) hatadan sonra birebir aynı ve FK-tutarlıdır."""
    app = _prepare(tmp_path, USERS)  # _prepare tüm modüllere kayıt ekler
    with app.app_context():
        before = _fingerprint(_db_path(app))
        for table in ("assets", "asset_assignments", "tickets", "ticket_events", "vehicles", "maintenance_records",
                      "maintenance_voids", "audit_logs"):
            assert before["rows"][table], f"{table} için test verisi yok"
        inject.pattern = r"ALTER TABLE users_new RENAME"  # users silindikten SONRA, yeniden adlandırmada hata
        with pytest.raises((SystemExit, RuntimeError)):
            upgrade(directory=MIGRATIONS, revision="head")
        _assert_nothing_changed(app, before, STAGE4)
        after = _fingerprint(_db_path(app))
        for table in ("assets", "asset_assignments", "tickets", "ticket_events", "vehicles", "maintenance_records",
                      "maintenance_voids", "audit_logs"):
            assert after["rows"][table] == before["rows"][table]
        assert not [r for r in after["rows"]["audit_logs"] if r[3] == "user_role_changed"]


def _upgraded(tmp_path):
    app = _prepare(tmp_path, USERS)
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="head")
        db.session.rollback()
    return app


@pytest.mark.parametrize("label,pattern", DOWNGRADE_FAILURE_POINTS, ids=[p[0] for p in DOWNGRADE_FAILURE_POINTS])
def test_downgrade_failure_at_every_step_leaves_database_unchanged(tmp_path, inject, label, pattern):
    app = _upgraded(tmp_path)
    with app.app_context():
        db.engine.dispose()
        before = _fingerprint(_db_path(app))
        assert before["rows"]["user_permissions"]  # izinler var; kaybolmamalı
        inject.pattern = pattern
        with pytest.raises((SystemExit, RuntimeError)):
            downgrade(directory=MIGRATIONS, revision=STAGE4)
        assert inject.fired
        _assert_nothing_changed(app, before, STAGE5)
        after = _fingerprint(_db_path(app))
        assert after["rows"]["user_permissions"] == before["rows"]["user_permissions"]
        assert "users_old" not in after["rows"]


def test_downgrade_failure_during_commit_leaves_database_unchanged(tmp_path, inject):
    app = _upgraded(tmp_path)
    with app.app_context():
        db.engine.dispose()
        before = _fingerprint(_db_path(app))
        inject.on_commit = True
        with pytest.raises((SystemExit, RuntimeError)):
            downgrade(directory=MIGRATIONS, revision=STAGE4)
        assert inject.fired
        _assert_nothing_changed(app, before, STAGE5)


# --- Eski/uyumsuz env.py koruması -------------------------------------------------------------------------------
def _load_migration():
    spec = importlib.util.spec_from_file_location("c5d8_under_test", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_migration_refuses_to_run_outside_a_real_transaction(tmp_path, direction):
    """Eski env.py (BEGIN yok) ile çalıştırılırsa migration HİÇBİR ŞEY yapmadan durur: geri alınamaz adımlara girmez."""
    app = _prepare(tmp_path, USERS)
    path = _db_path(app)
    if direction == "downgrade":
        with app.app_context():
            upgrade(directory=MIGRATIONS, revision="head")
            db.session.rollback()
            db.engine.dispose()
    before = _fingerprint(path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        context = MigrationContext.configure(conn)  # transactional_ddl yok, açık BEGIN yok (eski env.py davranışı)
        with Operations.context(context):
            with pytest.raises(RuntimeError, match="tek bir veritabani islemi"):
                getattr(_load_migration(), direction)()
        conn.rollback()
    engine.dispose()
    assert _fingerprint(path) == before
