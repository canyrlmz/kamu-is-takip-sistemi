from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.admin import bp
from app.admin.forms import UserCreateForm, UserEditForm
from app.decorators import permission_required
from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.permissions import (
    AUDIT_VIEW,
    GRANTABLE,
    MODULE_LABELS,
    PERMISSIONS,
    ROLE_ARIZA_KONTROL,
    ROLE_CEILINGS,
    ROLE_PRESETS,
    ROLE_SUPERADMIN,
    USERS_MANAGE,
)
from app.services.users import (
    UserRuleError,
    create_user,
    set_user_active,
    update_user,
)


@bp.route("/denetim-kayitlari")
@permission_required(AUDIT_VIEW)
def audit_log():
    page = request.args.get("sayfa", 1, type=int)
    pagination = db.paginate(
        db.select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()),
        page=page,
        per_page=25,
        error_out=False,
    )
    return render_template("admin/audit_log.html", pagination=pagination)


@bp.route("/kullanicilar")
@permission_required(USERS_MANAGE)
def users():
    page = request.args.get("sayfa", 1, type=int)
    pagination = db.paginate(
        db.select(User).order_by(User.username),
        page=page,
        per_page=25,
        error_out=False,
    )
    return render_template("admin/users.html", pagination=pagination)


def _permission_groups():
    """Formda modül başlıkları altında gösterilecek atanabilir izinler: [(modül adı, [(anahtar, açıklama)])]."""
    groups: dict[str, list] = {}
    for key, (module, description) in PERMISSIONS.items():
        if key in GRANTABLE:
            groups.setdefault(module, []).append((key, description))
    return [(MODULE_LABELS[m], items) for m, items in groups.items()]


def _permission_config():
    """Tarayıcıdaki yardımcı betik için rol başına öneri ve tavan listesi (yalnızca kolaylık; sunucu yeniden doğrular)."""
    return {
        "presets": {role: sorted(p) for role, p in ROLE_PRESETS.items()},
        "ceilings": {role: sorted(c) for role, c in ROLE_CEILINGS.items()},
    }


def _render_form(template, form, **context):
    return render_template(template, form=form, permission_groups=_permission_groups(),
                           permission_config=_permission_config(), **context)


@bp.route("/kullanicilar/yeni", methods=["GET", "POST"])
@permission_required(USERS_MANAGE)
def user_new():
    form = UserCreateForm()
    if request.method == "GET":
        form.permissions.data = sorted(ROLE_PRESETS[ROLE_ARIZA_KONTROL])  # öneri; yönetici değiştirebilir
    if form.validate_on_submit():
        try:
            user = create_user(
                current_user, username=form.username.data, full_name=form.full_name.data,
                password=form.password.data, role=form.role.data, seniority=form.seniority_level.data,
                permissions=form.permissions.data or (),
            )
        except UserRuleError as error:
            flash(str(error), "danger")
            return _render_form("admin/user_new.html", form), 200
        flash(f"Hesap oluşturuldu: {user.username}", "success")
        return redirect(url_for("admin.users"))
    return _render_form("admin/user_new.html", form)


@bp.route("/kullanicilar/<int:user_id>/duzenle", methods=["GET", "POST"])
@permission_required(USERS_MANAGE)
def user_edit(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    is_superadmin = user.role == ROLE_SUPERADMIN
    form = UserEditForm()
    if is_superadmin:
        # Süper adminin rolü, izinleri ve aktifliği düzenlenemez; bu alanlar forma hiç konmaz.
        del form.role, form.permissions, form.active
    if request.method == "GET":
        form.full_name.data = user.full_name
        form.seniority_level.data = str(user.seniority_level)
        if not is_superadmin:
            form.role.data = user.role
            form.permissions.data = sorted(user.granted_permissions)
            form.active.data = bool(user.active)
    if form.validate_on_submit():
        try:
            changes = update_user(
                current_user, user, full_name=form.full_name.data,
                role=None if is_superadmin else form.role.data,
                seniority=form.seniority_level.data,
                permissions=() if is_superadmin else (form.permissions.data or ()),
                active=None if is_superadmin else form.active.data,
            )
        except UserRuleError as error:
            flash(str(error), "danger")
            return _render_form("admin/user_edit.html", form, target=user, is_superadmin=is_superadmin), 200
        flash("Hesap güncellendi." if changes else "Herhangi bir değişiklik yapılmadı.",
              "success" if changes else "info")
        return redirect(url_for("admin.users"))
    return _render_form("admin/user_edit.html", form, target=user, is_superadmin=is_superadmin)


@bp.route("/kullanicilar/<int:user_id>/devre-disi", methods=["POST"])
@permission_required(USERS_MANAGE)
def user_deactivate(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if not user.active:
        flash("Bu hesap zaten devre dışı.", "info")
        return redirect(url_for("admin.users"))
    try:
        set_user_active(current_user, user, False)
    except UserRuleError as error:
        flash(str(error), "danger")
        return redirect(url_for("admin.users"))
    flash(f"Hesap devre dışı bırakıldı: {user.username}", "success")
    return redirect(url_for("admin.users"))


@bp.route("/kullanicilar/<int:user_id>/etkinlestir", methods=["POST"])
@permission_required(USERS_MANAGE)
def user_activate(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.active:
        flash("Bu hesap zaten etkin.", "info")
        return redirect(url_for("admin.users"))
    try:
        set_user_active(current_user, user, True)
    except UserRuleError as error:
        flash(str(error), "danger")
        return redirect(url_for("admin.users"))
    flash(f"Hesap etkinleştirildi: {user.username}", "success")
    return redirect(url_for("admin.users"))
