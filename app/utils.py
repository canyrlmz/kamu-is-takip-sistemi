"""Küçük yardımcılar: UTC zamanı ve Türkiye saatinde gösterim."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app


def utcnow() -> datetime:
    """Şu anki UTC zamanı (veritabanında UTC saklanır)."""
    return datetime.now(timezone.utc)


def to_local(value: datetime | None) -> datetime | None:
    """Veritabanından gelen (UTC kabul edilen) zamanı yerel saate çevirir."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(current_app.config["APP_TIMEZONE"]))


def today_local():
    """Yerel saat dilimine göre bugünün tarihi."""
    return utcnow().astimezone(ZoneInfo(current_app.config["APP_TIMEZONE"])).date()


def format_datetime(value: datetime | None) -> str:
    """Jinja filtresi: 31.12.2026 14:05 biçimi."""
    local = to_local(value)
    return local.strftime("%d.%m.%Y %H:%M") if local else "—"


def format_date(value) -> str:
    """Jinja filtresi: 31.12.2026 biçimi."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        value = to_local(value)
    return value.strftime("%d.%m.%Y")


def format_money(kurus) -> str:
    """Jinja filtresi: kuruş cinsinden tam sayıyı 1.250,50 ₺ biçiminde gösterir."""
    if kurus is None:
        return "—"
    lira, cents = divmod(int(kurus), 100)
    return f"{lira:,}".replace(",", ".") + f",{cents:02d} ₺"
