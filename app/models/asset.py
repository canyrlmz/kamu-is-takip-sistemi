from app.extensions import db
from app.utils import utcnow

# Demirbaş durumları
STATUS_IN_USE = "in_use"
STATUS_IN_STORAGE = "in_storage"
STATUS_MAINTENANCE = "maintenance"
STATUS_SCRAP_CANDIDATE = "scrap_candidate"

STATUS_LABELS = {
    STATUS_IN_USE: "Kullanımda",
    STATUS_IN_STORAGE: "Depoda",
    STATUS_MAINTENANCE: "Bakımda",
    STATUS_SCRAP_CANDIDATE: "Hurda adayı",
}
STATUS_BADGES = {
    STATUS_IN_USE: "success",
    STATUS_IN_STORAGE: "secondary",
    STATUS_MAINTENANCE: "warning",
    STATUS_SCRAP_CANDIDATE: "danger",
}


class Asset(db.Model):
    """Demirbaş. Kayıtlar silinmez; pasife alma `archived` alanıyla yapılır."""

    __tablename__ = "assets"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('in_use', 'in_storage', 'maintenance', 'scrap_candidate')",
            name="ck_assets_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(80), nullable=False, index=True)
    brand = db.Column(db.String(80), nullable=True)
    model = db.Column(db.String(80), nullable=True)
    # Boşsa NULL saklanır; dolu seri numaraları benzersizdir (birden çok NULL serbesttir).
    serial_number = db.Column(db.String(100), unique=True, nullable=True)
    location = db.Column(db.String(120), nullable=True)
    acquisition_date = db.Column(db.Date, nullable=True)
    # Kural: durum "Kullanımda" ise ve yalnızca ise aktif bir zimmet vardır.
    status = db.Column(db.String(20), nullable=False, default=STATUS_IN_STORAGE, index=True)
    description = db.Column(db.String(1000), nullable=True)
    archived = db.Column(db.Boolean, nullable=False, default=False, index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def status_badge(self) -> str:
        return STATUS_BADGES.get(self.status, "secondary")

    def __repr__(self) -> str:
        return f"<Asset {self.code}>"


class AssetAssignment(db.Model):
    """Zimmet hareketi. İade edilince satır silinmez; `return_date` doldurulur."""

    __tablename__ = "asset_assignments"
    __table_args__ = (
        db.CheckConstraint("return_date IS NULL OR return_date >= start_date", name="ck_assignment_dates"),
        # Bir demirbaşın aynı anda yalnızca bir aktif (iade edilmemiş) zimmeti olabilir.
        db.Index(
            "uq_asset_assignments_one_active",
            "asset_id",
            unique=True,
            sqlite_where=db.text("return_date IS NULL"),
            postgresql_where=db.text("return_date IS NULL"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id"), nullable=False, index=True)
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False)
    return_date = db.Column(db.Date, nullable=True)
    assign_note = db.Column(db.String(500), nullable=True)
    return_note = db.Column(db.String(500), nullable=True)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    returned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    returned_at = db.Column(db.DateTime, nullable=True)

    asset = db.relationship("Asset", backref=db.backref("assignments", lazy="select"))
    assignee = db.relationship("User", foreign_keys=[assignee_id], lazy="joined")
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id], lazy="joined")
    returned_by = db.relationship("User", foreign_keys=[returned_by_id], lazy="joined")

    @property
    def is_active(self) -> bool:
        return self.return_date is None
