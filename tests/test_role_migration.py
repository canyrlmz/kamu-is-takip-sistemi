"""Aşama 5.1: roller/kıdem/izin migration'ı — tüm eski kullanıcı senaryoları, geçici veritabanlarında."""
import importlib.util
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text

from app import create_app, permissions as perm
from app.extensions import db
from app.models.user import User

MIGRATIONS = str(Path(__file__).resolve().parents[1] / "migrations")
STAGE1, STAGE2, STAGE3 = "da9bc564aab8", "0a6f12ac96e2", "84674ae864e7"
STAGE4 = "b4c7e1a9d352"
STAGE5 = "c5d8f2b7a461"


def _new_app(tmp_path, name="m.db"):
    return create_app("testing", {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / name}"})


def _run(sql, **params):
    db.session.execute(text(sql), params)


def _add_user(uid, username, role, active=1):
    _run("INSERT INTO users (id, username, full_name, password_hash, role, active, created_at, updated_at, last_login_at) "
         "VALUES (:i, :u, :f, :h, :r, :a, '2026-01-01', '2026-01-02', '2026-01-03')",
         i=uid, u=username, f=f"Ad {username}", h=f"hash-{username}", r=role, a=active)


def _seed_all_modules():
    """Aşama 4 şemasında eski rollerle; her modülden kayıt (kullanıcılara bağlı yabancı anahtarlarla)."""
    _run("INSERT INTO assets (id, code, name, category, status, archived, created_by_id, created_at, updated_at) "
         "VALUES (1, 'DMB-1', 'Bilgisayar', 'Bilgisayar', 'in_use', 0, 1, '2026-01-01', '2026-01-01')")
    _run("INSERT INTO asset_assignments (id, asset_id, assignee_id, start_date, assigned_by_id, created_at) "
         "VALUES (1, 1, 2, '2026-01-02', 1, '2026-01-02')")
    _run("INSERT INTO tickets (id, title, description, category, location, priority, status, created_by_id, "
         "assigned_to_id, created_at, updated_at) VALUES (1, 'T', 'D', 'printer', 'L', 'normal', 'new', 2, 2, "
         "'2026-01-01', '2026-01-01')")
    _run("INSERT INTO ticket_events (ticket_id, event_type, to_status, actor_id, created_at) "
         "VALUES (1, 'created', 'new', 2, '2026-01-01')")
    _run("INSERT INTO vehicles (id, vehicle_type, identifier, meter_type, current_meter, status, archived, "
         "created_by_id, created_at, updated_at) VALUES (1, 'Kamyonet', '78ABC123', 'km', 1000, 'active', 0, 1, "
         "'2026-01-01', '2026-01-01')")
    _run("INSERT INTO maintenance_records (id, vehicle_id, maintenance_type, maintenance_date, meter_reading, "
         "description, cost_kurus, created_by_id, created_at) VALUES (1, 1, 'periodic', '2026-01-05', 900, 'Bakım', "
         "125050, 1, '2026-01-05')")
    _run("INSERT INTO maintenance_voids (record_id, reason, voided_by_id, created_at) "
         "VALUES (1, 'Yanlış girildi', 1, '2026-01-06')")
    _run("INSERT INTO audit_logs (created_at, user_id, action) VALUES ('2026-01-01', 1, 'login_success')")


MODULE_TABLES = ("assets", "asset_assignments", "tickets", "ticket_events", "vehicles", "maintenance_records",
                 "maintenance_voids")


def _snapshot():
    rows = {t: db.session.execute(text(f"SELECT * FROM {t} ORDER BY 1")).all() for t in MODULE_TABLES}
    rows["audit_original"] = db.session.execute(text(
        "SELECT * FROM audit_logs WHERE action <> 'user_role_changed' ORDER BY id")).all()
    rows["users_core"] = db.session.execute(text(
        "SELECT id, username, full_name, password_hash, department, active, created_at, updated_at, last_login_at "
        "FROM users ORDER BY id")).all()
    return rows


def _users():
    return {r[1]: (r[2], r[3], r[4]) for r in db.session.execute(
        text("SELECT id, username, role, seniority_level, active FROM users ORDER BY id"))}


def _grants(username):
    return {r[0] for r in db.session.execute(text(
        "SELECT permission FROM user_permissions p JOIN users u ON u.id = p.user_id WHERE u.username = :u"),
        {"u": username})}


def _fk_violations():
    return db.session.execute(text("PRAGMA foreign_key_check")).all()


def _prepare(tmp_path, users, name="m.db", revision=STAGE4, seed=True):
    app = _new_app(tmp_path, name)
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=revision)
        for args in users:
            _add_user(*args)
        if seed:
            _seed_all_modules()
        db.session.commit()
    return app


# --- Zincir ve uyum -----------------------------------------------------------------------------------
def test_chain_has_single_head_and_continues_stage4():
    config = Config()
    config.set_main_option("script_location", MIGRATIONS)
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [STAGE5]
    assert script.get_revision(STAGE5).down_revision == STAGE4
    assert script.get_revision(STAGE4).down_revision == STAGE3
    assert script.get_revision(STAGE3).down_revision == STAGE2
    assert script.get_revision(STAGE2).down_revision == STAGE1


def test_migration_constants_match_the_permission_registry():
    spec = importlib.util.spec_from_file_location(
        "m5", Path(MIGRATIONS) / "versions" / "c5d8f2b7a461_asama_5_roller_kidem_ve_izinler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.LEGACY_STAFF_PERMISSIONS) == perm.LEGACY_STAFF_PERMISSIONS
    assert set(module.ALL_GRANTABLE_PERMISSIONS) == perm.GRANTABLE
    assert {r.strip().strip("'") for r in module.NEW_ROLES.split(",")} == set(perm.ROLE_LABELS)


def test_models_match_migrated_schema(tmp_path):
    app = _new_app(tmp_path, "drift.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="head")
        with db.engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.metadata) == []


# --- Eski kullanıcı senaryoları ---------------------------------------------------------------------------
def test_single_admin_and_staff_are_converted_and_all_data_is_preserved(tmp_path):
    app = _prepare(tmp_path, [(1, "yonetici", "admin"), (2, "personel", "staff"), (3, "personel2", "staff")])
    with app.app_context():
        before = _snapshot()
        upgrade(directory=MIGRATIONS, revision="head")

        assert _users() == {"yonetici": ("superadmin", 1, 1), "personel": ("ariza_kontrol", 1, 1),
                            "personel2": ("ariza_kontrol", 1, 1)}
        assert _snapshot() == before  # kullanıcılar (parola özeti dahil) ve tüm modül kayıtları birebir
        assert _fk_violations() == []
        assert _grants("personel") == _grants("personel2") == set(perm.LEGACY_STAFF_PERMISSIONS)
        assert _grants("yonetici") == set()  # süper admin izinleri örtüktür
        # Denetim: eski kayıt duruyor + her dönüşüm için yeni kayıt
        entries = db.session.execute(text(
            "SELECT entity_id, details FROM audit_logs WHERE action = 'user_role_changed' ORDER BY entity_id")).all()
        assert entries == [(1, "Asama 5 gecisi: admin -> superadmin"), (2, "Asama 5 gecisi: staff -> ariza_kontrol"),
                           (3, "Asama 5 gecisi: staff -> ariza_kontrol")]
        # Uygulama modeli dönüştürülen hesapları okuyabilir ve yetkileri bugünkü personelle aynıdır
        staff = db.session.scalar(db.select(User).where(User.username == "personel"))
        assert staff.effective_permissions == perm.LEGACY_STAFF_PERMISSIONS and staff.has_permission("tickets.create")
        assert not staff.has_permission("assets.create") and not staff.has_permission("users.manage")
        admin = db.session.scalar(db.select(User).where(User.username == "yonetici"))
        assert admin.is_superadmin and admin.has_permission("users.manage") and admin.check_password is not None


def test_converted_staff_keeps_exactly_todays_capabilities(tmp_path):
    """Bugünkü personel: görüntüleme + talep açma + kendisine atanan talepte durum değiştirme; yazma yok."""
    app = _prepare(tmp_path, [(1, "yonetici", "admin"), (2, "personel", "staff")], seed=False)
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="head")
        staff = db.session.scalar(db.select(User).where(User.username == "personel"))
        allowed = {"assets.view", "vehicles.view", "maintenance.view", "tickets.view", "tickets.create", "tickets.update"}
        for key in perm.PERMISSIONS:
            assert staff.has_permission(key) == (key in allowed), key


def test_multiple_active_admins_stop_migration_without_any_change(tmp_path, capfd):
    app = _prepare(tmp_path, [(1, "yonetici", "admin"), (2, "yonetici2", "admin"), (3, "personel", "staff")])
    with app.app_context():
        before = _snapshot()
        tables_before = set(inspect(db.engine).get_table_names())
        with pytest.raises((SystemExit, RuntimeError)):
            upgrade(directory=MIGRATIONS, revision="head")
        db.session.rollback()
        assert "aktif 'admin'" in capfd.readouterr().err  # kullanıcıya nedeni söyler
        assert _users_legacy() == {"yonetici": "admin", "yonetici2": "admin", "personel": "staff"}
        assert set(inspect(db.engine).get_table_names()) == tables_before  # yeni tablo/artık yok
        assert _snapshot() == before
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == STAGE4
        assert db.session.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'user_role_changed'")).scalar() == 0
        # Fazla yönetici devre dışı bırakılınca migration artık uygulanabilir
        _run("UPDATE users SET active = 0 WHERE username = 'yonetici2'")
        db.session.commit()
        upgrade(directory=MIGRATIONS, revision="head")
        assert _users()["yonetici"][0] == "superadmin" and _users()["yonetici2"] == ("birim_sorumlusu", 1, 0)


def _users_legacy():
    return {r[0]: r[1] for r in db.session.execute(text("SELECT username, role FROM users"))}


def test_inactive_admin_becomes_unit_manager_with_all_grantable_permissions(tmp_path):
    app = _prepare(tmp_path, [(1, "yonetici", "admin"), (2, "eskiyonetici", "admin", 0), (3, "pasifpersonel", "staff", 0)])
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="head")
        assert _users() == {"yonetici": ("superadmin", 1, 1), "eskiyonetici": ("birim_sorumlusu", 1, 0),
                            "pasifpersonel": ("ariza_kontrol", 1, 0)}
        assert _grants("eskiyonetici") == set(perm.GRANTABLE) and perm.USERS_MANAGE not in _grants("eskiyonetici")
        assert _grants("pasifpersonel") == set(perm.LEGACY_STAFF_PERMISSIONS)
        assert _fk_violations() == []
        with pytest.raises(Exception):  # pasif eski admin sonradan "süper admin" olarak etkinleştirilemez
            _run("UPDATE users SET role = 'superadmin', active = 1 WHERE username = 'eskiyonetici'")
        db.session.rollback()


@pytest.mark.parametrize("users,expected", [
    ([], {}),
    ([(1, "personel", "staff")], {"personel": ("ariza_kontrol", 1, 1)}),
    ([(1, "eskiyonetici", "admin", 0)], {"eskiyonetici": ("birim_sorumlusu", 1, 0)}),
])
def test_no_active_admin_scenarios_create_no_superadmin(tmp_path, users, expected):
    app = _prepare(tmp_path, users, seed=False)
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="head")
        assert _users() == expected
        assert db.session.execute(text("SELECT count(*) FROM users WHERE role = 'superadmin'")).scalar() == 0


def test_direct_upgrade_from_stage1_with_data_in_one_go(tmp_path):
    """Birden çok migration tek seferde: önceki adımların işlemleri PRAGMA'yı etkisiz kılmamalı."""
    app = _new_app(tmp_path, "oneshot.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision=STAGE1)
        _add_user(1, "yonetici", "admin")
        _add_user(2, "personel", "staff")
        _run("INSERT INTO audit_logs (created_at, user_id, action) VALUES ('2026-01-01', 1, 'login_success')")
        db.session.commit()
        upgrade(directory=MIGRATIONS, revision="head")
        assert _users()["yonetici"][0] == "superadmin" and _users()["personel"][0] == "ariza_kontrol"
        assert _fk_violations() == []
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == STAGE5
        assert db.session.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'login_success'")).scalar() == 1


def test_fresh_database_upgrades_cleanly(tmp_path):
    app = _new_app(tmp_path, "fresh.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="head")
        assert {"users", "user_permissions", "vehicles"} <= set(inspect(db.engine).get_table_names())


def test_foreign_key_enforcement_is_restored_after_migrating(tmp_path):
    app = _prepare(tmp_path, [(1, "yonetici", "admin")], seed=False)
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="head")
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        with pytest.raises(Exception):  # var olmayan kullanıcıya bağlı satır reddedilir
            _run("INSERT INTO user_permissions (user_id, permission, granted_at) VALUES (999, 'assets.view', '2026-01-01')")
        db.session.rollback()


def test_preexisting_foreign_key_inconsistency_aborts_before_any_change(tmp_path, capfd):
    app = _prepare(tmp_path, [(1, "yonetici", "admin"), (2, "personel", "staff")])
    with app.app_context():
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=OFF"))
        _run("INSERT INTO tickets (id, title, description, category, location, priority, status, created_by_id, "
             "created_at, updated_at) VALUES (2, 'T', 'D', 'printer', 'L', 'normal', 'new', 999, "
             "'2026-02-01', '2026-02-01')")  # var olmayan kullanıcıya bağlı talep
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        with pytest.raises((SystemExit, RuntimeError)):
            upgrade(directory=MIGRATIONS, revision="head")
        db.session.rollback()
        assert "tutarsizlik" in capfd.readouterr().err
        assert _users_legacy() == {"yonetici": "admin", "personel": "staff"}
        assert "user_permissions" not in inspect(db.engine).get_table_names()


def _fresh_connection_fk_state():
    """Havuzdan alınan bağlantının foreign_keys ayarı (1 = açık)."""
    with db.engine.connect() as conn:
        return conn.exec_driver_sql("PRAGMA foreign_keys").scalar()


def test_foreign_keys_are_back_on_after_every_failed_upgrade(tmp_path, capfd):
    # 1) Ön kontrolde durma (çoklu aktif admin)
    app = _prepare(tmp_path, [(1, "yonetici", "admin"), (2, "yonetici2", "admin")], name="a.db")
    with app.app_context():
        with pytest.raises((SystemExit, RuntimeError)):
            upgrade(directory=MIGRATIONS, revision="head")
        db.session.rollback()
        assert _fresh_connection_fk_state() == 1
    # 2) Migration'ın ORTASINDA hata: user_permissions zaten var -> users_new oluşturulduktan sonra patlar
    app = _prepare(tmp_path, [(1, "yonetici", "admin"), (2, "personel", "staff")], name="b.db")
    with app.app_context():
        db.session.execute(text("CREATE TABLE user_permissions (x INTEGER)"))
        db.session.commit()
        before = _snapshot()
        with pytest.raises(Exception):
            upgrade(directory=MIGRATIONS, revision="head")
        db.session.rollback()
        assert _fresh_connection_fk_state() == 1
        tables = set(inspect(db.engine).get_table_names())
        assert "users_new" not in tables  # geçici tablo temizlenir
        assert _users_legacy() == {"yonetici": "admin", "personel": "staff"}  # users dokunulmadı
        assert _snapshot() == before
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == STAGE4
        assert db.session.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'user_role_changed'")).scalar() == 0


# --- Veritabanı kuralları (göç edilmiş şemada) -----------------------------------------------------------------
def test_migrated_schema_enforces_single_superadmin_and_permission_rules(tmp_path):
    app = _prepare(tmp_path, [(1, "yonetici", "admin"), (2, "personel", "staff")], seed=False)
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="head")
        bad_sql = [
            "UPDATE users SET role = 'superadmin' WHERE id = 2",                       # ikinci aktif süper admin
            "UPDATE users SET role = 'admin' WHERE id = 2",                            # eski rol değeri
            "UPDATE users SET seniority_level = 9 WHERE id = 2",                       # kıdem sınırı
            "INSERT INTO user_permissions (user_id, permission, granted_at) VALUES (2, 'users.manage', '2026-01-01')",
        ]
        for sql in bad_sql:
            with pytest.raises(Exception):
                _run(sql)
            db.session.rollback()
        assert _users()["yonetici"][0] == "superadmin" and _users()["personel"][0] == "ariza_kontrol"


# --- Geri dönüş ---------------------------------------------------------------------------------------------------
def test_downgrade_maps_roles_back_keeps_data_and_reupgrade_works(tmp_path):
    app = _prepare(tmp_path, [(1, "yonetici", "admin"), (2, "personel", "staff"), (3, "eskiyonetici", "admin", 0)])
    with app.app_context():
        before = _snapshot()
        upgrade(directory=MIGRATIONS, revision="head")
        _run("UPDATE users SET seniority_level = 4 WHERE username = 'personel'")
        db.session.commit()

        downgrade(directory=MIGRATIONS, revision=STAGE4)

        assert _users_legacy() == {"yonetici": "admin", "personel": "staff", "eskiyonetici": "staff"}
        assert "user_permissions" not in inspect(db.engine).get_table_names()
        assert "seniority_level" not in {c["name"] for c in inspect(db.engine).get_columns("users")}
        assert _snapshot() == before and _fk_violations() == []
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1

        upgrade(directory=MIGRATIONS, revision="head")
        assert _users()["yonetici"][0] == "superadmin" and _fk_violations() == []
