"""Uygulama yapılandırması.

Gizli değerler (SECRET_KEY, DATABASE_URL, ...) kaynak koda yazılmaz;
ortam değişkenlerinden veya Git'e eklenmeyen .env dosyasından okunur.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()  # .env dosyası varsa ortam değişkenlerine yükler


class Config:
    """Tüm ortamlar için ortak ayarlar."""

    SECRET_KEY = os.environ.get("SECRET_KEY")

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Oturum ve çerez güvenliği
    SESSION_COOKIE_NAME = "karabuk_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)  # boşta kalma süresi

    # Form / istek sınırları
    WTF_CSRF_TIME_LIMIT = 3600
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2 MB

    # Giriş denemesi sınırı (IP başına)
    RATELIMIT_STORAGE_URI = "memory://"
    LOGIN_RATE_LIMIT = "5 per minute;30 per hour"

    # HTTPS ve güvenlik başlıkları
    FORCE_HTTPS = False

    # Yerel saat dilimi (veritabanında UTC saklanır)
    APP_TIMEZONE = "Europe/Istanbul"

    LOG_TO_FILE = True
    MIN_SECRET_KEY_LENGTH = 32
    DEBUG = False
    TESTING = False


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or "sqlite:///karabuk_takip_dev.db"


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "yalnizca-test-icin-gizli-anahtar"  # gerçek bir anahtar değildir
    MIN_SECRET_KEY_LENGTH = 0
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # bellekte, izole test veritabanı
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    LOG_TO_FILE = False


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    FORCE_HTTPS = True
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")  # zorunlu


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
