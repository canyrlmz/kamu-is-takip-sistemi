import re

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.decorators import permission_required
from app.extensions import db
from app.models.ticket import (
    CATEGORY_LABELS,
    PRIORITY_LABELS,
    STATUS_LABELS,
    Ticket,
)
from app.models.user import User
from app.permissions import TICKETS_ASSIGN, TICKETS_CREATE, TICKETS_UPDATE, TICKETS_VIEW, TICKETS_VIEW_ALL
from app.services.tickets import (
    TicketPermissionError,
    TicketRuleError,
    allowed_transitions,
    assign_ticket,
    can_assign,
    change_status,
    create_ticket,
    get_visible_ticket,
    visible_tickets_stmt,
)
from app.tickets import bp
from app.tickets.forms import AssignForm, StatusForm, TicketForm

ASSIGNMENT_FILTERS = ("atanmamis", "bana")
NUMBER_RE = re.compile(r"^(?:TLP-?)?(\d{1,9})$", re.IGNORECASE)


def _read_filters(args) -> dict:
    status, priority, category = args.get("durum", ""), args.get("oncelik", ""), args.get("kategori", "")
    assignment = args.get("atama", "")
    return {
        "durum": status if status in STATUS_LABELS else "",
        "oncelik": priority if priority in PRIORITY_LABELS else "",
        "kategori": category if category in CATEGORY_LABELS else "",
        "atama": assignment if assignment in ASSIGNMENT_FILTERS else "",
        "no": (args.get("no") or "").strip()[:20],
    }


def _get_visible_or_404(ticket_id: int) -> Ticket:
    ticket = get_visible_ticket(current_user, ticket_id)
    if ticket is None:  # yok veya görme yetkisi yok: ikisi de 404 (talep numarası tahmini sızdırılmaz)
        abort(404)
    return ticket


@bp.route("/")
@permission_required(TICKETS_VIEW, TICKETS_VIEW_ALL, any_of=True)
def index():
    filters = _read_filters(request.args)
    stmt = visible_tickets_stmt(current_user)  # personel için yalnızca kendi / atanan talepler
    if filters["durum"]:
        stmt = stmt.where(Ticket.status == filters["durum"])
    if filters["oncelik"]:
        stmt = stmt.where(Ticket.priority == filters["oncelik"])
    if filters["kategori"]:
        stmt = stmt.where(Ticket.category == filters["kategori"])
    if filters["atama"] == "atanmamis":
        stmt = stmt.where(Ticket.assigned_to_id.is_(None))
    elif filters["atama"] == "bana":
        stmt = stmt.where(Ticket.assigned_to_id == current_user.id)
    if filters["no"]:
        match = NUMBER_RE.match(filters["no"])
        stmt = stmt.where(Ticket.id == int(match.group(1))) if match else stmt.where(db.false())

    page = request.args.get("sayfa", 1, type=int)
    pagination = db.paginate(stmt.order_by(Ticket.id.desc()), page=page, per_page=20, error_out=False)
    page_params = {k: v for k, v in filters.items() if v}
    return render_template("tickets/list.html", pagination=pagination, filters=filters, page_params=page_params,
                           statuses=STATUS_LABELS, priorities=PRIORITY_LABELS, categories=CATEGORY_LABELS)


@bp.route("/yeni", methods=["GET", "POST"])
@permission_required(TICKETS_CREATE)
def new():
    form = TicketForm()
    if form.validate_on_submit():
        try:
            ticket = create_ticket(current_user, form.title.data, form.description.data, form.category.data,
                                   form.location.data, form.priority.data)
        except TicketRuleError as error:
            flash(str(error), "danger")
            return render_template("tickets/form.html", form=form)
        flash(f"Talebiniz alındı: {ticket.number}", "success")
        if not current_user.has_permission(TICKETS_VIEW) and not current_user.has_permission(TICKETS_VIEW_ALL):
            return redirect(url_for("dashboard.index"))  # talebi görüntüleme izni yoksa detay sayfası 404 verirdi
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    return render_template("tickets/form.html", form=form)


@bp.route("/<int:ticket_id>")
@permission_required(TICKETS_VIEW, TICKETS_VIEW_ALL, any_of=True)
def detail(ticket_id):
    ticket = _get_visible_or_404(ticket_id)
    return render_template(
        "tickets/detail.html", ticket=ticket, events=ticket.events,
        can_change_status=bool(allowed_transitions(ticket, current_user)),
        can_assign=can_assign(current_user) and not ticket.is_closed,
    )


@bp.route("/<int:ticket_id>/durum", methods=["GET", "POST"])
@permission_required(TICKETS_VIEW, TICKETS_VIEW_ALL, any_of=True)
def status(ticket_id):
    ticket = _get_visible_or_404(ticket_id)
    targets = allowed_transitions(ticket, current_user)
    if not targets:
        abort(403)  # talebi görebilir ama durumunu değiştiremez
    form = StatusForm()
    form.status.choices = [(s, STATUS_LABELS[s]) for s in targets]
    if form.validate_on_submit():
        try:
            change_status(ticket, current_user, form.status.data, form.note.data)
        except TicketPermissionError:
            abort(403)
        except TicketRuleError as error:
            flash(str(error), "danger")
            return render_template("tickets/status.html", form=form, ticket=ticket)
        flash(f"Durum güncellendi: {STATUS_LABELS[ticket.status]}", "success")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    return render_template("tickets/status.html", form=form, ticket=ticket)


@bp.route("/<int:ticket_id>/ata", methods=["GET", "POST"])
@permission_required(TICKETS_ASSIGN)
def assign(ticket_id):
    ticket = _get_visible_or_404(ticket_id)
    form = AssignForm()
    users = db.session.scalars(db.select(User).where(User.active.is_(True)).order_by(User.full_name)).all()
    # Yalnızca talepleri işleme yetkisi olan kullanıcılar sorumlu olabilir (servis katmanı da bunu doğrular).
    form.assignee_id.choices = [(u.id, f"{u.full_name} ({u.username})") for u in users
                                if u.has_permission(TICKETS_UPDATE)]
    if request.method == "GET" and ticket.assigned_to_id in dict(form.assignee_id.choices):
        form.assignee_id.data = ticket.assigned_to_id
    if form.validate_on_submit():
        try:
            assignee = db.session.get(User, form.assignee_id.data)
            assign_ticket(ticket, assignee, current_user, form.note.data)
        except TicketPermissionError:
            abort(403)
        except TicketRuleError as error:
            flash(str(error), "danger")
            return render_template("tickets/assign.html", form=form, ticket=ticket)
        flash(f"Talep {assignee.full_name} kullanıcısına atandı.", "success")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    return render_template("tickets/assign.html", form=form, ticket=ticket)
