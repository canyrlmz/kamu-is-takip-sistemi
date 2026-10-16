"""Kullanıcıya ayrıntılı hata izi göstermeyen, güvenli hata sayfaları."""
from flask import current_app, render_template
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from app.extensions import db
from app.services.authz import PermissionDenied

MESSAGES = {
    400: ("Geçersiz istek", "İsteğiniz işlenemedi. Lütfen sayfayı yenileyip tekrar deneyin."),
    403: ("Erişim engellendi", "Bu sayfayı görüntülemek için yetkiniz bulunmuyor."),
    404: ("Sayfa bulunamadı", "Aradığınız sayfa mevcut değil veya taşınmış olabilir."),
    405: ("İzin verilmeyen işlem", "Bu adres için bu istek türü kullanılamaz."),
    413: ("İstek çok büyük", "Gönderdiğiniz veri izin verilen boyutu aşıyor."),
    429: ("Çok fazla deneme", "Kısa sürede çok fazla istek gönderildi. Lütfen biraz bekleyip tekrar deneyin."),
    500: ("Beklenmeyen bir hata oluştu", "İşleminiz tamamlanamadı. Sorun kaydedildi; lütfen daha sonra tekrar deneyin."),
}


def _render(code: int, message: str | None = None):
    title, default_message = MESSAGES.get(code, MESSAGES[500])
    return render_template("errors/error.html", code=code, title=title, message=message or default_message), code


def register_error_handlers(app):
    @app.errorhandler(CSRFError)
    def handle_csrf(error):
        return _render(400, "Form doğrulaması başarısız oldu veya oturum süreniz doldu. Sayfayı yenileyip tekrar deneyin.")

    @app.errorhandler(PermissionDenied)
    def handle_permission_denied(error):
        # Servis katmanı yetkiyi reddetti (route denetimi atlansa bile işlem yapılmaz): 403
        db.session.rollback()
        return _render(403)

    @app.errorhandler(HTTPException)
    def handle_http(error):
        code = error.code if error.code in MESSAGES else 500
        return _render(code)

    @app.errorhandler(Exception)
    def handle_unexpected(error):
        # Ayrıntı yalnızca sunucu loguna yazılır, kullanıcıya gösterilmez.
        current_app.logger.exception("Beklenmeyen hata")
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _render(500)
