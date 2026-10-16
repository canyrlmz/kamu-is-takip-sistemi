"""Sunucu tarafı izin kontrolü (route katmanı).

Yetki rol adına değil izne bağlıdır; kıdem seviyesi hiçbir kontrolde kullanılmaz. Servis fonksiyonları ayrıca
`app.services.authz.ensure_permission` ile kendi izinlerini doğrular.
"""
from functools import wraps

from flask import abort
from flask_login import current_user

from app.extensions import login_manager
from app.permissions import PERMISSIONS


def permission_required(*permissions: str, any_of: bool = False):
    """Giriş yapmamışsa girişe yönlendirir; izin yoksa 403 döner.

    Varsayılan olarak verilen izinlerin HEPSİ gerekir; `any_of=True` ile herhangi biri yeterlidir.
    """
    unknown = [p for p in permissions if p not in PERMISSIONS]
    if unknown or not permissions:
        raise ValueError(f"Geçersiz izin tanımı: {unknown or 'boş'}")

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            checks = [current_user.has_permission(p) for p in permissions]
            if not (any(checks) if any_of else all(checks)):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
