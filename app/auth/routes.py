from urllib.parse import urlsplit

from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.auth import bp
from app.auth.forms import LoginForm
from app.extensions import db, limiter
from app.models.audit_log import LOGIN_FAILED, LOGIN_SUCCESS, LOGOUT
from app.models.user import User
from app.services.audit import log_event
from app.services.passwords import normalize_username
from app.utils import utcnow

GENERIC_LOGIN_ERROR = "Kullanıcı adı veya parola hatalı."


def _safe_next_url(target: str | None) -> str | None:
    """Yalnızca site içi, göreli adreslere yönlendirmeye izin verir (open redirect koruması)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target


@bp.route("/giris", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"], methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        username = normalize_username(form.username.data)
        password = form.password.data
        user = db.session.scalar(db.select(User).where(User.username == username))

        password_ok = False
        if user is None:
            User.burn_password_check(password)
        else:
            password_ok = user.check_password(password)

        if password_ok and user.active:
            session.clear()  # eski oturum verisini taşıma (session fixation)
            login_user(user)
            session.permanent = True
            user.last_login_at = utcnow()
            log_event(LOGIN_SUCCESS, user_id=user.id, entity_type="user", entity_id=user.id, commit=False)
            db.session.commit()
            return redirect(_safe_next_url(request.args.get("next")) or url_for("dashboard.index"))

        # Başarısız deneme: kullanıcıya hangi nedenle başarısız olduğu söylenmez.
        if user is None:
            reason = "Bilinmeyen kullanıcı adı"  # girilen ad loglanmaz (yanlışlıkla parola girilmiş olabilir)
        elif not password_ok:
            reason = "Hatalı parola"
        else:
            reason = "Devre dışı hesap"
        log_event(
            LOGIN_FAILED,
            user_id=user.id if user else None,
            entity_type="user" if user else None,
            entity_id=user.id if user else None,
            details=reason,
        )
        flash(GENERIC_LOGIN_ERROR, "danger")
        return render_template("auth/login.html", form=form), 401

    return render_template("auth/login.html", form=form)


@bp.route("/cikis", methods=["POST"])
@login_required
def logout():
    log_event(LOGOUT, user_id=current_user.id, entity_type="user", entity_id=current_user.id)
    logout_user()
    session.clear()
    flash("Oturumunuz kapatıldı.", "info")
    return redirect(url_for("auth.login"))
