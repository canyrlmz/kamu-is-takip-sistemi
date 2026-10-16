"""Talep testleri için ortak yardımcılar (fixture değil, düz fonksiyonlar)."""
from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.ticket import CLOSED_STATUSES, Ticket, TicketEvent
from app.models.user import User
from app.services import tickets as svc
from app.utils import utcnow


def ticket_data(**overrides):
    data = {
        "title": "Yazıcı kağıt sıkıştırıyor",
        "description": "İkinci kattaki yazıcı sürekli kağıt sıkıştırıyor.",
        "category": "printer",
        "priority": "normal",
        "location": "2. kat, Oda 12",
    }
    data.update(overrides)
    return data


def make_ticket(app, creator_id, title="Yazıcı çalışmıyor", **kw):
    """Servis katmanı üzerinden talep oluşturur (geçmiş ve denetim kaydı dahil)."""
    with app.app_context():
        creator = db.session.get(User, creator_id)
        ticket = svc.create_ticket(
            creator, title, kw.get("description", "Açıklama metni"), kw.get("category", "printer"),
            kw.get("location", "Oda 3"), kw.get("priority", "normal"),
        )
        return ticket.id


def assign_via_service(app, ticket_id, assignee_id, actor_id, note=None):
    with app.app_context():
        svc.assign_ticket(db.session.get(Ticket, ticket_id), db.session.get(User, assignee_id),
                          db.session.get(User, actor_id), note)


def force_state(app, ticket_id, status, assignee_id=None):
    """Test kurulumu: talebi doğrudan istenen duruma getirir (iş kurallarını atlar)."""
    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        ticket.status = status
        closed = status in CLOSED_STATUSES
        ticket.closed_at = utcnow() if closed else None
        ticket.resolution_note = "Önceki not" if closed else None
        if assignee_id is not None:
            ticket.assigned_to_id = assignee_id
        db.session.commit()


def get_ticket(app, ticket_id):
    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        db.session.expunge(ticket)
        return ticket


def events_of(app, ticket_id):
    with app.app_context():
        rows = db.session.scalars(
            db.select(TicketEvent).where(TicketEvent.ticket_id == ticket_id).order_by(TicketEvent.id)
        ).all()
        return [
            {"type": e.event_type, "from": e.from_status, "to": e.to_status, "note": e.note,
             "actor": e.actor_id, "from_assignee": e.from_assignee_id, "to_assignee": e.to_assignee_id}
            for e in rows
        ]


def audit_actions(app):
    with app.app_context():
        return [e.action for e in db.session.scalars(db.select(AuditLog).order_by(AuditLog.id)).all()]


def audit_entries(app, action):
    with app.app_context():
        rows = db.session.scalars(db.select(AuditLog).where(AuditLog.action == action)).all()
        return [{"user_id": e.user_id, "entity_type": e.entity_type, "entity_id": e.entity_id,
                 "details": e.details} for e in rows]


def ticket_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count()).select_from(Ticket))
