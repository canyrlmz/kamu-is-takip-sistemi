from sqlalchemy import event

from app.extensions import db
from app.utils import utcnow

# Durumlar
STATUS_NEW = "new"
STATUS_REVIEWING = "reviewing"
STATUS_IN_PROGRESS = "in_progress"
STATUS_RESOLVED = "resolved"
STATUS_REJECTED = "rejected"

STATUS_LABELS = {
    STATUS_NEW: "Yeni",
    STATUS_REVIEWING: "İnceleniyor",
    STATUS_IN_PROGRESS: "İşlemde",
    STATUS_RESOLVED: "Çözüldü",
    STATUS_REJECTED: "Reddedildi",
}
STATUS_BADGES = {
    STATUS_NEW: "primary",
    STATUS_REVIEWING: "info",
    STATUS_IN_PROGRESS: "warning",
    STATUS_RESOLVED: "success",
    STATUS_REJECTED: "secondary",
}
OPEN_STATUSES = (STATUS_NEW, STATUS_REVIEWING, STATUS_IN_PROGRESS)
CLOSED_STATUSES = (STATUS_RESOLVED, STATUS_REJECTED)

# Öncelikler
PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
PRIORITY_URGENT = "urgent"

PRIORITY_LABELS = {
    PRIORITY_LOW: "Düşük",
    PRIORITY_NORMAL: "Normal",
    PRIORITY_HIGH: "Yüksek",
    PRIORITY_URGENT: "Acil",
}
PRIORITY_BADGES = {
    PRIORITY_LOW: "secondary",
    PRIORITY_NORMAL: "primary",
    PRIORITY_HIGH: "warning",
    PRIORITY_URGENT: "danger",
}

# Kategoriler
CATEGORY_LABELS = {
    "computer": "Bilgisayar",
    "printer": "Yazıcı",
    "network": "Ağ / İnternet",
    "software": "Yazılım",
    "electrical": "Elektrik",
    "other": "Diğer",
}

# Geçmiş olay türleri
EVENT_CREATED = "created"
EVENT_STATUS_CHANGED = "status_changed"
EVENT_ASSIGNED = "assigned"
EVENT_REASSIGNED = "reassigned"


def _sql_in(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Ticket(db.Model):
    """Arıza / iş talebi. Kayıtlar silinmez; süreç durum alanıyla izlenir."""

    __tablename__ = "tickets"
    __table_args__ = (
        db.CheckConstraint(_sql_in("status", STATUS_LABELS), name="ck_tickets_status"),
        db.CheckConstraint(_sql_in("priority", PRIORITY_LABELS), name="ck_tickets_priority"),
        db.CheckConstraint(_sql_in("category", CATEGORY_LABELS), name="ck_tickets_category"),
        # Kapanış tarihi yalnızca Çözüldü / Reddedildi durumlarında dolu olabilir (ve olmak zorundadır).
        db.CheckConstraint(
            f"({_sql_in('status', CLOSED_STATUSES)}) = (closed_at IS NOT NULL)",
            name="ck_tickets_closed_at",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(2000), nullable=False)
    category = db.Column(db.String(30), nullable=False, index=True)
    location = db.Column(db.String(120), nullable=False)
    priority = db.Column(db.String(20), nullable=False, default=PRIORITY_NORMAL, index=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_NEW, index=True)
    resolution_note = db.Column(db.String(500), nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)

    created_by = db.relationship("User", foreign_keys=[created_by_id], lazy="joined")
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id], lazy="joined")
    events = db.relationship(
        "TicketEvent", back_populates="ticket", order_by="TicketEvent.id", lazy="select"
    )

    @property
    def number(self) -> str:
        """Okunabilir, benzersiz ve değişmez talep numarası (kimlikten türetilir)."""
        return f"TLP-{self.id:05d}" if self.id is not None else "TLP-?????"

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def status_badge(self) -> str:
        return STATUS_BADGES.get(self.status, "secondary")

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABELS.get(self.priority, self.priority)

    @property
    def priority_badge(self) -> str:
        return PRIORITY_BADGES.get(self.priority, "secondary")

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    def __repr__(self) -> str:
        return f"<Ticket {self.number}>"


class TicketEvent(db.Model):
    """Talep işlem geçmişi. Eklenir, ama değiştirilemez ve silinemez."""

    __tablename__ = "ticket_events"
    __table_args__ = (
        db.CheckConstraint(
            _sql_in("event_type", (EVENT_CREATED, EVENT_STATUS_CHANGED, EVENT_ASSIGNED, EVENT_REASSIGNED)),
            name="ck_ticket_events_type",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    event_type = db.Column(db.String(20), nullable=False)
    from_status = db.Column(db.String(20), nullable=True)
    to_status = db.Column(db.String(20), nullable=True)
    from_assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    to_assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    note = db.Column(db.String(500), nullable=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    ticket = db.relationship("Ticket", back_populates="events")
    actor = db.relationship("User", foreign_keys=[actor_id], lazy="joined")
    from_assignee = db.relationship("User", foreign_keys=[from_assignee_id], lazy="joined")
    to_assignee = db.relationship("User", foreign_keys=[to_assignee_id], lazy="joined")

    @property
    def from_status_label(self) -> str | None:
        return STATUS_LABELS.get(self.from_status) if self.from_status else None

    @property
    def to_status_label(self) -> str | None:
        return STATUS_LABELS.get(self.to_status) if self.to_status else None


# Geçmiş kayıtları uygulama üzerinden değiştirilemez ve silinemez (AuditLog ile aynı yaklaşım).
# SQLite için ayrıca veritabanı tetikleyicileri migration ile eklenir.
@event.listens_for(TicketEvent, "before_update")
def _block_event_update(mapper, connection, target):
    raise RuntimeError("Talep geçmişi kayıtları değiştirilemez.")


@event.listens_for(TicketEvent, "before_delete")
def _block_event_delete(mapper, connection, target):
    raise RuntimeError("Talep geçmişi kayıtları silinemez.")
