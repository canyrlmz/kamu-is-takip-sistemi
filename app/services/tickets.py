"""Talep (arıza / iş talebi) iş kuralları.

Durum geçişleri, atama ve yetki kontrolü burada yapılır; route'lar yalnızca form/HTTP işini yapar.
İki hata türü vardır:
- TicketRuleError: iş kuralı ihlali (mesaj kullanıcıya gösterilir).
- TicketPermissionError: kullanıcının bu işlem için yetkisi yok (route 403 döndürür).
"""
from sqlalchemy import func, or_

from app.extensions import db
from app.models.audit_log import (
    TICKET_ASSIGNED,
    TICKET_CREATED,
    TICKET_REASSIGNED,
    TICKET_STATUS_CHANGED,
)
from app.models.ticket import (
    CATEGORY_LABELS,
    CLOSED_STATUSES,
    EVENT_ASSIGNED,
    EVENT_CREATED,
    EVENT_REASSIGNED,
    EVENT_STATUS_CHANGED,
    OPEN_STATUSES,
    PRIORITY_LABELS,
    PRIORITY_NORMAL,
    STATUS_IN_PROGRESS,
    STATUS_LABELS,
    STATUS_NEW,
    STATUS_RESOLVED,
    STATUS_REVIEWING,
    STATUS_REJECTED,
    Ticket,
    TicketEvent,
)
from app.models.user import User
from app.permissions import (
    TICKETS_ASSIGN,
    TICKETS_CREATE,
    TICKETS_MANAGE,
    TICKETS_UPDATE,
    TICKETS_VIEW,
    TICKETS_VIEW_ALL,
)
from app.services.audit import log_event
from app.services.authz import PermissionDenied, ensure_permission
from app.utils import utcnow

MAX_NOTE_LENGTH = 500

# tickets.manage: tüm geçerli geçişler. Yeniden açma (Çözüldü/Reddedildi → açık durum) yalnızca bu izinle yapılır.
ADMIN_TRANSITIONS = {
    STATUS_NEW: {STATUS_REVIEWING, STATUS_IN_PROGRESS, STATUS_REJECTED},
    STATUS_REVIEWING: {STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_REJECTED},
    STATUS_IN_PROGRESS: {STATUS_REVIEWING, STATUS_RESOLVED},
    STATUS_RESOLVED: {STATUS_IN_PROGRESS},
    STATUS_REJECTED: {STATUS_REVIEWING},
}
# tickets.update + kendisine atanmış talep: yalnızca talebi ilerletir; reddedemez, yeniden açamaz.
ASSIGNEE_TRANSITIONS = {
    STATUS_NEW: {STATUS_REVIEWING, STATUS_IN_PROGRESS},
    STATUS_REVIEWING: {STATUS_IN_PROGRESS, STATUS_RESOLVED},
    STATUS_IN_PROGRESS: {STATUS_REVIEWING, STATUS_RESOLVED},
}
STATUS_ORDER = list(STATUS_LABELS)  # formlarda sabit sıra


class TicketRuleError(Exception):
    """İş kuralı ihlali; mesaj kullanıcıya gösterilir."""


class TicketPermissionError(TicketRuleError, PermissionDenied):
    """Kullanıcının bu işlem için yetkisi yok."""


# --- Görünürlük / yetki ------------------------------------------------------

def visible_tickets_stmt(user: User):
    """Kullanıcının görebileceği taleplerin sorgusu.

    - tickets.view_all: tüm talepler
    - tickets.view: yalnızca kendi açtığı veya kendisine atanan talepler
    - ikisi de yoksa (veya hesap devre dışıysa): hiçbiri
    """
    stmt = db.select(Ticket)
    if user.has_permission(TICKETS_VIEW_ALL):
        return stmt
    if user.has_permission(TICKETS_VIEW):
        return stmt.where(or_(Ticket.created_by_id == user.id, Ticket.assigned_to_id == user.id))
    return stmt.where(db.false())


def get_visible_ticket(user: User, ticket_id: int) -> Ticket | None:
    """Talep yoksa veya kullanıcı göremiyorsa None döner (route 404 verir)."""
    return db.session.scalar(visible_tickets_stmt(user).where(Ticket.id == ticket_id))


def can_see(ticket: Ticket, user: User) -> bool:
    if user.has_permission(TICKETS_VIEW_ALL):
        return True
    return user.has_permission(TICKETS_VIEW) and user.id in (ticket.created_by_id, ticket.assigned_to_id)


def allowed_transitions(ticket: Ticket, actor: User) -> list[str]:
    """Kullanıcının bu talepte uygulayabileceği hedef durumlar (sabit sırayla). Talebi göremiyorsa hiçbiri."""
    targets: set = set()
    if can_see(ticket, actor):
        if actor.has_permission(TICKETS_MANAGE):
            targets = ADMIN_TRANSITIONS.get(ticket.status, set())
        elif ticket.assigned_to_id == actor.id and actor.has_permission(TICKETS_UPDATE):
            targets = ASSIGNEE_TRANSITIONS.get(ticket.status, set())
    return [s for s in STATUS_ORDER if s in targets]


def can_assign(actor: User) -> bool:
    return actor.has_permission(TICKETS_ASSIGN)


# --- Yardımcılar -------------------------------------------------------------

def clean_note(note: str | None) -> str | None:
    note = (note or "").strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise TicketRuleError(f"Not en fazla {MAX_NOTE_LENGTH} karakter olabilir.")
    return note or None


def _add_event(ticket: Ticket, actor: User, event_type: str, **fields) -> TicketEvent:
    event = TicketEvent(ticket_id=ticket.id, event_type=event_type, actor_id=actor.id, **fields)
    db.session.add(event)
    return event


# --- İşlemler ----------------------------------------------------------------

def create_ticket(creator: User, title: str, description: str, category: str, location: str,
                  priority: str = PRIORITY_NORMAL) -> Ticket:
    ensure_permission(creator, TICKETS_CREATE)
    title, description, location = (title or "").strip(), (description or "").strip(), (location or "").strip()
    if not title or not description or not location:
        raise TicketRuleError("Başlık, açıklama ve konum zorunludur.")
    if len(title) > 150 or len(description) > 2000 or len(location) > 120:
        raise TicketRuleError("Alan uzunluk sınırı aşıldı.")
    if category not in CATEGORY_LABELS:
        raise TicketRuleError("Geçersiz kategori.")
    if priority not in PRIORITY_LABELS:
        raise TicketRuleError("Geçersiz öncelik.")

    ticket = Ticket(title=title, description=description, category=category, location=location,
                    priority=priority, status=STATUS_NEW, created_by_id=creator.id)
    db.session.add(ticket)
    db.session.flush()  # kimlik (ve dolayısıyla talep numarası) oluşur
    _add_event(ticket, creator, EVENT_CREATED, to_status=STATUS_NEW)
    log_event(TICKET_CREATED, user_id=creator.id, entity_type="ticket", entity_id=ticket.id,
              details=f"Talep: {ticket.number}, öncelik: {PRIORITY_LABELS[priority]}", commit=False)
    db.session.commit()
    return ticket


def change_status(ticket: Ticket, actor: User, new_status: str, note: str | None = None) -> TicketEvent:
    may_manage = actor.has_permission(TICKETS_MANAGE)
    may_work = ticket.assigned_to_id == actor.id and actor.has_permission(TICKETS_UPDATE)
    if not can_see(ticket, actor) or not (may_manage or may_work):
        raise TicketPermissionError("Bu talebin durumunu değiştirme yetkiniz yok.")
    if new_status not in STATUS_LABELS:
        raise TicketRuleError("Geçersiz durum.")
    if new_status == ticket.status:
        raise TicketRuleError("Talep zaten bu durumda.")
    if new_status not in ADMIN_TRANSITIONS.get(ticket.status, set()):
        raise TicketRuleError(
            f"'{STATUS_LABELS[ticket.status]}' durumundan '{STATUS_LABELS[new_status]}' durumuna geçilemez."
        )
    if new_status not in allowed_transitions(ticket, actor):
        raise TicketPermissionError("Bu durum değişikliğini yapma yetkiniz yok.")

    note = clean_note(note)
    reopening = ticket.is_closed
    if (new_status in CLOSED_STATUSES or reopening) and note is None:
        raise TicketRuleError(
            "Çözüm / ret açıklaması zorunludur." if new_status in CLOSED_STATUSES
            else "Yeniden açma gerekçesi (not) zorunludur."
        )

    old_status = ticket.status
    ticket.status = new_status
    if new_status in CLOSED_STATUSES:
        ticket.closed_at = utcnow()
        ticket.resolution_note = note
    elif reopening:
        ticket.closed_at = None  # önceki kapanış, değiştirilemez geçmişte kayıtlı kalır
        ticket.resolution_note = None

    event = _add_event(ticket, actor, EVENT_STATUS_CHANGED, from_status=old_status, to_status=new_status, note=note)
    log_event(TICKET_STATUS_CHANGED, user_id=actor.id, entity_type="ticket", entity_id=ticket.id,
              details=f"Talep: {ticket.number}, {STATUS_LABELS[old_status]} → {STATUS_LABELS[new_status]}",
              commit=False)
    db.session.commit()
    return event


def assign_ticket(ticket: Ticket, assignee: User | None, actor: User, note: str | None = None) -> TicketEvent:
    if not can_assign(actor) or not can_see(ticket, actor):
        raise TicketPermissionError("Sorumlu atama yetkiniz yok.")
    if ticket.is_closed:
        raise TicketRuleError("Kapalı talebe sorumlu atanamaz. Önce talebi yeniden açın.")
    if assignee is None or not assignee.active:
        raise TicketRuleError("Sorumlu olarak yalnızca etkin bir kullanıcı seçilebilir.")
    if not assignee.has_permission(TICKETS_UPDATE):
        # Talebi işleyemeyecek (veya göremeyecek) birine atama, talebi sahipsiz bırakırdı.
        raise TicketRuleError("Sorumlu olarak yalnızca talepleri işleme yetkisi olan bir kullanıcı seçilebilir.")
    if ticket.assigned_to_id == assignee.id:
        raise TicketRuleError("Talep zaten bu kullanıcıya atanmış.")

    note = clean_note(note)
    previous = ticket.assigned_to
    ticket.assigned_to_id = assignee.id
    ticket.assigned_to = assignee
    reassigned = previous is not None
    event = _add_event(
        ticket, actor, EVENT_REASSIGNED if reassigned else EVENT_ASSIGNED,
        from_assignee_id=previous.id if previous else None, to_assignee_id=assignee.id, note=note,
    )
    detail = f"Talep: {ticket.number}, sorumlu: " + (f"{previous.username} → {assignee.username}" if reassigned else assignee.username)
    log_event(TICKET_REASSIGNED if reassigned else TICKET_ASSIGNED, user_id=actor.id,
              entity_type="ticket", entity_id=ticket.id, details=detail, commit=False)
    db.session.commit()
    return event


def ticket_summary(user: User) -> dict:
    """Kontrol paneli özeti; yalnızca kullanıcının görebildiği talepler sayılır."""
    visible = visible_tickets_stmt(user).subquery()

    def count(*conditions):
        stmt = db.select(func.count()).select_from(visible)
        for cond in conditions:
            stmt = stmt.where(cond)
        return db.session.scalar(stmt)

    return {
        "open": count(visible.c.status.in_(OPEN_STATUSES)),
        "in_progress": count(visible.c.status == STATUS_IN_PROGRESS),
        "mine": count(visible.c.status.in_(OPEN_STATUSES), visible.c.assigned_to_id == user.id),
        "unassigned": count(visible.c.status.in_(OPEN_STATUSES), visible.c.assigned_to_id.is_(None))
        if user.has_permission(TICKETS_VIEW_ALL) else None,
    }
