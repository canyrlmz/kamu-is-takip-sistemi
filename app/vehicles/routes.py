from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.decorators import permission_required
from app.extensions import db
from app.models.vehicle import (
    MAINTENANCE_TYPE_LABELS, METER_LABELS, STATUS_LABELS, MaintenanceRecord, Vehicle,
)
from app.services.vehicles import (
    ALERT_LABELS, NORMAL, OVERDUE, UNSCHEDULED, UPCOMING, VehicleRuleError, add_maintenance, alert_in_scope,
    archive_vehicle, create_vehicle, fleet_alerts, restore_vehicle, update_vehicle, void_maintenance,
    compute_vehicle_alert,
)
from app.permissions import (
    MAINTENANCE_CREATE,
    MAINTENANCE_VIEW,
    MAINTENANCE_VOID,
    VEHICLES_CREATE,
    VEHICLES_UPDATE,
    VEHICLES_VIEW,
)
from app.utils import today_local
from app.vehicles import bp
from app.vehicles.forms import DEFAULT_VEHICLE_TYPES, MaintenanceForm, VehicleForm, VoidForm

ARCHIVE_FILTERS = ("aktif", "arsivli", "hepsi")
MAINT_FILTERS = {"gecikmis": OVERDUE, "yaklasan": UPCOMING, "planlanmamis": UNSCHEDULED}
PER_PAGE = 20


@bp.context_processor
def inject_permissions():
    return {
        "alert_labels": ALERT_LABELS,
        "maintenance_types": MAINTENANCE_TYPE_LABELS,
    }


class ListPagination:
    """Bellekteki liste için `render_pagination` makrosuyla uyumlu basit sayfalama."""

    def __init__(self, items, page, per_page):
        self.total = len(items)
        self.per_page = per_page
        self.pages = max(1, -(-self.total // per_page))
        self.page = min(max(page, 1), self.pages)
        start = (self.page - 1) * per_page
        self.items = items[start:start + per_page]

    has_prev = property(lambda self: self.page > 1)
    has_next = property(lambda self: self.page < self.pages)
    prev_num = property(lambda self: self.page - 1)
    next_num = property(lambda self: self.page + 1)

    def iter_pages(self):
        last = 0
        for n in range(1, self.pages + 1):
            if n <= 2 or n > self.pages - 2 or abs(n - self.page) <= 2:
                if last and n - last > 1:
                    yield None
                yield n
                last = n


def _get_vehicle_or_404(vehicle_id: int) -> Vehicle:
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None:
        abort(404)
    return vehicle


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _contains(column, text):
    return column.ilike(f"%{_escape_like(text)}%", escape="\\")


def _apply_rule_error(form, error: VehicleRuleError) -> None:
    """Alana bağlı hatayı forma işler; bağlı değilse genel uyarı gösterir."""
    target = getattr(form, error.field, None) if error.field else None
    if target is not None:
        target.errors = list(target.errors) + [str(error)]
    else:
        flash(str(error), "danger")


def _read_filters(args) -> dict:
    status, meter, maint = args.get("durum", ""), args.get("sayac", ""), args.get("bakim", "")
    archive = args.get("arsiv", "aktif")
    return {
        "q": (args.get("q") or "").strip()[:100],
        "tur": (args.get("tur") or "").strip()[:60],
        "durum": status if status in STATUS_LABELS else "",
        "sayac": meter if meter in METER_LABELS else "",
        "bakim": maint if maint in MAINT_FILTERS else "",
        "arsiv": archive if archive in ARCHIVE_FILTERS else "aktif",
    }


def _vehicle_types():
    used = db.session.scalars(db.select(Vehicle.vehicle_type).distinct().order_by(Vehicle.vehicle_type)).all()
    return sorted(set(DEFAULT_VEHICLE_TYPES) | set(used))


@bp.route("/")
@permission_required(VEHICLES_VIEW)
def index():
    filters = _read_filters(request.args)
    stmt = db.select(Vehicle)
    if filters["q"]:
        stmt = stmt.where(or_(_contains(Vehicle.identifier, filters["q"]), _contains(Vehicle.brand, filters["q"]),
                              _contains(Vehicle.model, filters["q"])))
    if filters["tur"]:
        stmt = stmt.where(_contains(Vehicle.vehicle_type, filters["tur"]))
    if filters["durum"]:
        stmt = stmt.where(Vehicle.status == filters["durum"])
    if filters["sayac"]:
        stmt = stmt.where(Vehicle.meter_type == filters["sayac"])
    if filters["arsiv"] == "aktif":
        stmt = stmt.where(Vehicle.archived.is_(False))
    elif filters["arsiv"] == "arsivli":
        stmt = stmt.where(Vehicle.archived.is_(True))
    vehicles = db.session.scalars(stmt.order_by(Vehicle.identifier)).all()

    # Uyarılar her istekte güncel kayıtlardan hesaplanır; bakım filtresi bu sonuca uygulanır.
    # Bakım bilgisi (uyarı ve filtre) yalnızca maintenance.view iznine sahip kullanıcıya sunulur.
    can_view_maintenance = current_user.has_permission(MAINTENANCE_VIEW)
    if not can_view_maintenance:
        filters["bakim"] = ""
    alerts = fleet_alerts(vehicles) if can_view_maintenance else {}
    if filters["bakim"]:
        wanted = MAINT_FILTERS[filters["bakim"]]
        vehicles = [v for v in vehicles if alerts[v.id].status == wanted]
    pagination = ListPagination(vehicles, request.args.get("sayfa", 1, type=int), PER_PAGE)
    page_params = {k: v for k, v in filters.items() if v and not (k == "arsiv" and v == "aktif")}
    return render_template("vehicles/list.html", pagination=pagination, filters=filters, page_params=page_params,
                           alerts=alerts, statuses=STATUS_LABELS, meters=METER_LABELS, maint_filters=MAINT_FILTERS)


@bp.route("/yaklasan-bakimlar")
@permission_required(VEHICLES_VIEW, MAINTENANCE_VIEW)
def upcoming():
    vehicles = db.session.scalars(
        db.select(Vehicle).where(Vehicle.archived.is_(False)).order_by(Vehicle.identifier)
    ).all()
    alerts = fleet_alerts(vehicles)
    groups = {
        OVERDUE: [], UPCOMING: [], UNSCHEDULED: [],
    }
    normal = 0
    for v in vehicles:
        status = alerts[v.id].status
        if status in groups:
            groups[status].append(v)
        elif status == NORMAL:
            normal += 1
    return render_template("vehicles/upcoming.html", groups=groups, alerts=alerts, normal=normal,
                           out_of_scope=sum(1 for v in vehicles if not alert_in_scope(v)))


@bp.route("/<int:vehicle_id>")
@permission_required(VEHICLES_VIEW)
def detail(vehicle_id):
    vehicle = _get_vehicle_or_404(vehicle_id)
    records, alert = [], None
    if current_user.has_permission(MAINTENANCE_VIEW):  # bakım geçmişi ve uyarıları ayrı izne bağlıdır
        records = db.session.scalars(
            db.select(MaintenanceRecord).where(MaintenanceRecord.vehicle_id == vehicle.id)
            .order_by(MaintenanceRecord.maintenance_date.desc(), MaintenanceRecord.id.desc())
        ).all()
        alert = compute_vehicle_alert(vehicle.meter_type, vehicle.current_meter, records, today_local()) \
            if alert_in_scope(vehicle) else fleet_alerts([vehicle])[vehicle.id]
    return render_template("vehicles/detail.html", vehicle=vehicle, records=records, alert=alert)


@bp.route("/yeni", methods=["GET", "POST"])
@permission_required(VEHICLES_CREATE)
def new():
    form = VehicleForm()
    if form.validate_on_submit():
        try:
            vehicle = create_vehicle(form.vehicle_data(), current_user)
        except VehicleRuleError as error:
            _apply_rule_error(form, error)
        except IntegrityError:
            db.session.rollback()  # eş zamanlı kayıtta benzersizlik kısıtı devreye girer
            flash("Bu plaka / envanter kodu zaten kayıtlı.", "danger")
        else:
            flash(f"Kayıt oluşturuldu: {vehicle.identifier}", "success")
            return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
    return render_template("vehicles/form.html", form=form, vehicle=None, types=_vehicle_types())


@bp.route("/<int:vehicle_id>/duzenle", methods=["GET", "POST"])
@permission_required(VEHICLES_UPDATE)
def edit(vehicle_id):
    vehicle = _get_vehicle_or_404(vehicle_id)
    if vehicle.archived:
        flash("Arşivdeki kayıt düzenlenemez. Önce arşivden geri alın.", "warning")
        return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
    has_records = db.session.scalar(
        db.select(MaintenanceRecord.id).where(MaintenanceRecord.vehicle_id == vehicle.id).limit(1)) is not None
    form = VehicleForm(obj=vehicle if request.method == "GET" else None, lock_meter_type=has_records)
    if has_records and request.method == "POST":
        form.meter_type.data = vehicle.meter_type  # sayaç türü kilitli; sunucu tarafında sabitlenir
    if form.validate_on_submit():
        try:
            changed = update_vehicle(vehicle, form.vehicle_data(), current_user)
        except VehicleRuleError as error:
            db.session.rollback()
            _apply_rule_error(form, error)
        except IntegrityError:
            db.session.rollback()
            flash("Bu plaka / envanter kodu başka bir kayıtta kullanılıyor.", "danger")
        else:
            flash("Kayıt güncellendi." if changed else "Herhangi bir değişiklik yapılmadı.",
                  "success" if changed else "info")
            return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
    return render_template("vehicles/form.html", form=form, vehicle=vehicle, types=_vehicle_types(),
                           locked=has_records)


@bp.route("/<int:vehicle_id>/bakim-ekle", methods=["GET", "POST"])
@permission_required(MAINTENANCE_CREATE)
def add_maintenance_view(vehicle_id):
    vehicle = _get_vehicle_or_404(vehicle_id)
    if vehicle.archived:
        flash("Arşivdeki kayda bakım eklenemez. Önce arşivden geri alın.", "warning")
        return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
    form = MaintenanceForm()
    if request.method == "GET":
        form.maintenance_date.data = today_local()
        form.meter_reading.data = vehicle.current_meter
    if form.validate_on_submit():
        try:
            record = add_maintenance(vehicle, form.maintenance_data(), current_user)
        except VehicleRuleError as error:
            db.session.rollback()
            _apply_rule_error(form, error)
        else:
            flash(f"Bakım kaydı eklendi: {record.type_label}", "success")
            return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
    return render_template("vehicles/maintenance_form.html", form=form, vehicle=vehicle)


@bp.route("/<int:vehicle_id>/bakim/<int:record_id>/iptal", methods=["GET", "POST"])
@permission_required(MAINTENANCE_VOID)
def void_maintenance_view(vehicle_id, record_id):
    vehicle = _get_vehicle_or_404(vehicle_id)
    record = db.session.get(MaintenanceRecord, record_id)
    if record is None or record.vehicle_id != vehicle.id:
        abort(404)
    if record.is_void or vehicle.archived:
        flash("Bu bakım kaydı iptal edilemez (zaten iptal edilmiş veya kayıt arşivde).", "warning")
        return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
    form = VoidForm()
    if form.validate_on_submit():
        try:
            void_maintenance(record, form.reason.data, current_user)
        except VehicleRuleError as error:
            db.session.rollback()
            _apply_rule_error(form, error)
        else:
            flash("Bakım kaydı iptal edildi; uyarı hesabına artık dahil edilmez.", "success")
            return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
    return render_template("vehicles/void_form.html", form=form, vehicle=vehicle, record=record)


@bp.route("/<int:vehicle_id>/arsivle", methods=["POST"])
@permission_required(VEHICLES_UPDATE)
def archive(vehicle_id):
    vehicle = _get_vehicle_or_404(vehicle_id)
    try:
        archive_vehicle(vehicle, current_user)
        flash(f"Kayıt arşivlendi: {vehicle.identifier}", "success")
    except VehicleRuleError as error:
        db.session.rollback()
        flash(str(error), "danger")
    return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))


@bp.route("/<int:vehicle_id>/geri-al", methods=["POST"])
@permission_required(VEHICLES_UPDATE)
def restore(vehicle_id):
    vehicle = _get_vehicle_or_404(vehicle_id)
    try:
        restore_vehicle(vehicle, current_user)
        flash(f"Kayıt arşivden geri alındı: {vehicle.identifier}", "success")
    except VehicleRuleError as error:
        db.session.rollback()
        flash(str(error), "danger")
    return redirect(url_for("vehicles.detail", vehicle_id=vehicle.id))
