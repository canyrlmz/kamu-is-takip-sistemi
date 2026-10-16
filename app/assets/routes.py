from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.assets import bp
from app.assets.forms import DEFAULT_CATEGORIES, AssetForm, AssignForm, ReturnForm
from app.decorators import permission_required
from app.extensions import db
from app.models.asset import STATUS_LABELS, Asset, AssetAssignment
from app.models.user import User
from app.permissions import ASSETS_ASSIGN, ASSETS_CREATE, ASSETS_UPDATE, ASSETS_VIEW
from app.services.assets import (
    EDITABLE_FIELDS,
    AssetRuleError,
    archive_asset,
    assign_asset,
    create_asset,
    get_active_assignment,
    restore_asset,
    return_asset,
    update_asset,
)

ASSIGNMENT_FILTERS = ("zimmetli", "zimmetsiz")
ARCHIVE_FILTERS = ("aktif", "arsivli", "hepsi")


def _get_asset_or_404(asset_id: int) -> Asset:
    asset = db.session.get(Asset, asset_id)
    if asset is None:
        abort(404)
    return asset


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _read_filters(args) -> dict:
    status = args.get("durum", "")
    assignment = args.get("zimmet", "")
    archive = args.get("arsiv", "aktif")
    return {
        "q": (args.get("q") or "").strip()[:100],
        "kategori": (args.get("kategori") or "").strip()[:80],
        "konum": (args.get("konum") or "").strip()[:120],
        "durum": status if status in STATUS_LABELS else "",
        "zimmet": assignment if assignment in ASSIGNMENT_FILTERS else "",
        "arsiv": archive if archive in ARCHIVE_FILTERS else "aktif",
    }


def _contains(column, text):
    return column.ilike(f"%{_escape_like(text)}%", escape="\\")


@bp.route("/")
@permission_required(ASSETS_VIEW)
def index():
    filters = _read_filters(request.args)
    stmt = db.select(Asset)
    if filters["q"]:
        stmt = stmt.where(or_(_contains(Asset.code, filters["q"]), _contains(Asset.name, filters["q"]),
                              _contains(Asset.serial_number, filters["q"])))
    if filters["kategori"]:
        stmt = stmt.where(_contains(Asset.category, filters["kategori"]))
    if filters["konum"]:
        stmt = stmt.where(_contains(Asset.location, filters["konum"]))
    if filters["durum"]:
        stmt = stmt.where(Asset.status == filters["durum"])
    has_active = (
        db.select(AssetAssignment.id)
        .where(AssetAssignment.asset_id == Asset.id, AssetAssignment.return_date.is_(None))
        .exists()
    )
    if filters["zimmet"] == "zimmetli":
        stmt = stmt.where(has_active)
    elif filters["zimmet"] == "zimmetsiz":
        stmt = stmt.where(~has_active)
    if filters["arsiv"] == "aktif":
        stmt = stmt.where(Asset.archived.is_(False))
    elif filters["arsiv"] == "arsivli":
        stmt = stmt.where(Asset.archived.is_(True))

    page = request.args.get("sayfa", 1, type=int)
    pagination = db.paginate(stmt.order_by(Asset.code), page=page, per_page=20, error_out=False)

    # Sayfadaki demirbaşların güncel zimmetleri tek sorguda alınır.
    ids = [a.id for a in pagination.items]
    active = {}
    if ids:
        rows = db.session.scalars(
            db.select(AssetAssignment).where(AssetAssignment.asset_id.in_(ids), AssetAssignment.return_date.is_(None))
        ).all()
        active = {row.asset_id: row for row in rows}

    page_params = {k: v for k, v in filters.items() if v and not (k == "arsiv" and v == "aktif")}
    return render_template("assets/list.html", pagination=pagination, filters=filters, page_params=page_params,
                           active=active, statuses=STATUS_LABELS)


@bp.route("/<int:asset_id>")
@permission_required(ASSETS_VIEW)
def detail(asset_id):
    asset = _get_asset_or_404(asset_id)
    history = db.session.scalars(
        db.select(AssetAssignment)
        .where(AssetAssignment.asset_id == asset.id)
        .order_by(AssetAssignment.start_date.desc(), AssetAssignment.id.desc())
    ).all()
    current = next((h for h in history if h.is_active), None)
    return render_template("assets/detail.html", asset=asset, history=history, current=current)


def _category_suggestions():
    used = db.session.scalars(db.select(Asset.category).distinct().order_by(Asset.category)).all()
    return sorted(set(DEFAULT_CATEGORIES) | set(used))


@bp.route("/yeni", methods=["GET", "POST"])
@permission_required(ASSETS_CREATE)
def new():
    form = AssetForm()
    if form.validate_on_submit():
        data = {name: form[name].data for name in EDITABLE_FIELDS}
        data["status"] = form.status.data
        try:
            asset = create_asset(data, current_user)
        except IntegrityError:
            flash("Demirbaş kodu veya seri numarası zaten kayıtlı.", "danger")
            return render_template("assets/form.html", form=form, asset=None, categories=_category_suggestions())
        flash(f"Demirbaş oluşturuldu: {asset.code}", "success")
        return redirect(url_for("assets.detail", asset_id=asset.id))
    return render_template("assets/form.html", form=form, asset=None, categories=_category_suggestions())


@bp.route("/<int:asset_id>/duzenle", methods=["GET", "POST"])
@permission_required(ASSETS_UPDATE)
def edit(asset_id):
    asset = _get_asset_or_404(asset_id)
    if asset.archived:
        flash("Arşivdeki demirbaş düzenlenemez. Önce arşivden geri alın.", "warning")
        return redirect(url_for("assets.detail", asset_id=asset.id))
    locked = get_active_assignment(asset.id) is not None
    form = AssetForm(obj=asset if request.method == "GET" else None, asset_id=asset.id, lock_status=locked)
    if form.validate_on_submit():
        data = {name: form[name].data for name in EDITABLE_FIELDS}
        if not locked:
            data["status"] = form.status.data
        try:
            changed = update_asset(asset, data, current_user, locked=locked)
        except IntegrityError:
            flash("Demirbaş kodu veya seri numarası zaten kayıtlı.", "danger")
            return render_template("assets/form.html", form=form, asset=asset, locked=locked,
                                   categories=_category_suggestions())
        flash("Demirbaş güncellendi." if changed else "Herhangi bir değişiklik yapılmadı.",
              "success" if changed else "info")
        return redirect(url_for("assets.detail", asset_id=asset.id))
    return render_template("assets/form.html", form=form, asset=asset, locked=locked, categories=_category_suggestions())


@bp.route("/<int:asset_id>/zimmet-ver", methods=["GET", "POST"])
@permission_required(ASSETS_ASSIGN)
def assign(asset_id):
    asset = _get_asset_or_404(asset_id)
    form = AssignForm()
    users = db.session.scalars(db.select(User).where(User.active.is_(True)).order_by(User.full_name)).all()
    form.assignee_id.choices = [(u.id, f"{u.full_name} ({u.username})") for u in users]
    if form.validate_on_submit():
        try:
            assignee = db.session.get(User, form.assignee_id.data)
            assign_asset(asset, assignee, form.start_date.data, form.note.data, current_user)
        except AssetRuleError as error:
            flash(str(error), "danger")
            return render_template("assets/assign.html", form=form, asset=asset)
        flash(f"Zimmet verildi: {asset.code} → {assignee.full_name}", "success")
        return redirect(url_for("assets.detail", asset_id=asset.id))
    return render_template("assets/assign.html", form=form, asset=asset)


@bp.route("/<int:asset_id>/iade-al", methods=["GET", "POST"])
@permission_required(ASSETS_ASSIGN)
def return_(asset_id):
    asset = _get_asset_or_404(asset_id)
    current = get_active_assignment(asset.id)
    if current is None:
        flash("Bu demirbaşın aktif bir zimmeti yok.", "warning")
        return redirect(url_for("assets.detail", asset_id=asset.id))
    form = ReturnForm()
    if form.validate_on_submit():
        try:
            return_asset(asset, form.return_date.data, form.status_after.data, form.note.data, current_user)
        except AssetRuleError as error:
            flash(str(error), "danger")
            return render_template("assets/return.html", form=form, asset=asset, current=current)
        flash(f"Zimmet iade alındı: {asset.code}", "success")
        return redirect(url_for("assets.detail", asset_id=asset.id))
    return render_template("assets/return.html", form=form, asset=asset, current=current)


@bp.route("/<int:asset_id>/arsivle", methods=["POST"])
@permission_required(ASSETS_UPDATE)
def archive(asset_id):
    asset = _get_asset_or_404(asset_id)
    try:
        archive_asset(asset, current_user)
        flash(f"Demirbaş arşivlendi: {asset.code}", "success")
    except AssetRuleError as error:
        flash(str(error), "danger")
    return redirect(url_for("assets.detail", asset_id=asset.id))


@bp.route("/<int:asset_id>/geri-al", methods=["POST"])
@permission_required(ASSETS_UPDATE)
def restore(asset_id):
    asset = _get_asset_or_404(asset_id)
    try:
        restore_asset(asset, current_user)
        flash(f"Demirbaş arşivden geri alındı: {asset.code}", "success")
    except AssetRuleError as error:
        flash(str(error), "danger")
    return redirect(url_for("assets.detail", asset_id=asset.id))
