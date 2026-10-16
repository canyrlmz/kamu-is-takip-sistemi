"""Servis katmanı yetki denetimi.

Route dekoratörleri (`permission_required`) ilk savunma hattıdır; servis fonksiyonları ise çağrıldıkları yerden
bağımsız olarak kendi izinlerini yeniden doğrular (CLI, test, başka bir route ya da ileride eklenecek bir arayüz
yanlışlıkla denetimi atlasa bile işlem gerçekleşmez).
"""
from app.permissions import PERMISSIONS


class PermissionDenied(Exception):
    """Kullanıcının bu işlem için izni yok. Route katmanında 403 olarak gösterilir."""


def _actor_has(actor, permission: str) -> bool:
    checker = getattr(actor, "has_permission", None)  # anonim kullanıcı nesnesinde yöntem yoktur: izin yok
    return callable(checker) and bool(getattr(actor, "is_authenticated", True)) and checker(permission)


def ensure_permission(actor, permission: str) -> None:
    if permission not in PERMISSIONS:  # yazım hatası sessizce "izin yok" yerine açık bir programlama hatası olsun
        raise ValueError(f"Bilinmeyen izin: {permission}")
    if not _actor_has(actor, permission):
        raise PermissionDenied(f"Bu işlem için yetkiniz yok ({permission}).")


def ensure_any_permission(actor, *permissions: str) -> None:
    for permission in permissions:
        if permission not in PERMISSIONS:
            raise ValueError(f"Bilinmeyen izin: {permission}")
    if not any(_actor_has(actor, p) for p in permissions):
        raise PermissionDenied("Bu işlem için yetkiniz yok.")
