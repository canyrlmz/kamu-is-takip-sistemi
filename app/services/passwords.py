"""Parola gücü ve kullanıcı adı kuralları."""
import re

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,64}$")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def validate_username(username: str) -> list[str]:
    if USERNAME_RE.match(username or ""):
        return []
    return ["Kullanıcı adı 3-64 karakter olmalı; yalnızca küçük harf, rakam, nokta, tire ve alt çizgi içerebilir."]


def validate_password_strength(password: str, username: str | None = None) -> list[str]:
    """Kuralları sağlamayan her madde için Türkçe bir hata mesajı döndürür."""
    errors: list[str] = []
    password = password or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Parola en az {MIN_PASSWORD_LENGTH} karakter olmalıdır.")
    if len(password) > MAX_PASSWORD_LENGTH:
        errors.append(f"Parola en fazla {MAX_PASSWORD_LENGTH} karakter olabilir.")
    if not any(c.islower() for c in password):
        errors.append("Parola en az bir küçük harf içermelidir.")
    if not any(c.isupper() for c in password):
        errors.append("Parola en az bir büyük harf içermelidir.")
    if not any(c.isdigit() for c in password):
        errors.append("Parola en az bir rakam içermelidir.")
    if not any(not c.isalnum() for c in password):
        errors.append("Parola en az bir özel karakter içermelidir.")
    if username and username.lower() in password.lower():
        errors.append("Parola kullanıcı adını içermemelidir.")
    return errors
