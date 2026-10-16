"""Can Yorulmaz - Kamu Is Takip Sistemi uygulama fabrikasi."""
import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask, request
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.extensions import csrf, db, limiter, login_manager, migrate, talisman
from config import CONFIGS

# Bootstrap yerel olarak sunulduğu için yalnızca 'self' kaynaklarına izin veriliyor.
CONTENT_SECURITY_POLICY = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self'",
    "img-src": ["'self'", "data:"],
    "font-src": "'self'",
    "object-src": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "frame-ancestors": "'none'",
}


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite yabancı anahtar kısıtlarını varsayılan olarak uygulamaz; açıyoruz."""
    import sqlite3

    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_app(config_name: str | None = None, overrides: dict | None = None) -> Flask:
    config_name = config_name or os.environ.get("APP_ENV", "development")
    if config_name not in CONFIGS:
        raise RuntimeError(f"Bilinmeyen APP_ENV değeri: {config_name!r}. Geçerli: {', '.join(CONFIGS)}")

    app = Flask(__name__)
    app.config.from_object(CONFIGS[config_name])
    if overrides:
        app.config.update(overrides)
    _validate_config(app)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True, render_as_batch=True)
    csrf.init_app(app)
    limiter.init_app(app)
    talisman.init_app(
        app,
        force_https=app.config["FORCE_HTTPS"],
        strict_transport_security=app.config["FORCE_HTTPS"],
        session_cookie_secure=app.config["SESSION_COOKIE_SECURE"],
        session_cookie_http_only=True,
        session_cookie_samesite=app.config["SESSION_COOKIE_SAMESITE"],
        content_security_policy=CONTENT_SECURITY_POLICY,
        frame_options="DENY",
        referrer_policy="same-origin",
    )

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Bu sayfayı görmek için giriş yapmalısınız."
    login_manager.login_message_category = "warning"
    login_manager.session_protection = "strong"

    from app import models  # noqa: F401  (Flask-Migrate için modelleri yükler)
    from app.admin import bp as admin_bp
    from app.assets import bp as assets_bp
    from app.auth import bp as auth_bp
    from app.cli import register_commands
    from app.dashboard import bp as dashboard_bp
    from app.tickets import bp as tickets_bp
    from app.vehicles import bp as vehicles_bp
    from app.errors import register_error_handlers
    from app.utils import format_date, format_datetime, format_money

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(vehicles_bp)
    register_error_handlers(app)
    register_commands(app)
    app.add_template_filter(format_datetime, "dt")
    app.add_template_filter(format_date, "d")
    app.add_template_filter(format_money, "tl")

    @app.context_processor
    def inject_permission_helper():
        # Şablonlarda menü/düğme göstermek için; asıl yetki denetimi route ve servis katmanında yapılır.
        from flask_login import current_user

        def can(*permissions):
            return bool(current_user.is_authenticated and any(current_user.has_permission(p) for p in permissions))

        return {"can": can}

    @app.after_request
    def add_cache_headers(response):
        # Çıkıştan sonra "geri" tuşuyla korumalı sayfaların görünmesini engeller.
        if request.endpoint != "static":
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    _configure_logging(app)
    return app


def _validate_config(app: Flask) -> None:
    secret = app.config.get("SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "SECRET_KEY tanımlı değil. .env dosyasına veya ortam değişkenine güçlü bir anahtar ekleyin "
            "(README'deki komutla üretebilirsiniz)."
        )
    if len(secret) < app.config["MIN_SECRET_KEY_LENGTH"]:
        raise RuntimeError(
            f"SECRET_KEY en az {app.config['MIN_SECRET_KEY_LENGTH']} karakter olmalıdır."
        )
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError("DATABASE_URL tanımlı değil (üretim ortamında zorunludur).")


def _configure_logging(app: Flask) -> None:
    if not app.config["LOG_TO_FILE"]:
        return
    log_dir = os.path.join(app.instance_path, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"), maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
