from sqlalchemy import event

from app.extensions import db
from app.utils import utcnow

# Sayaç türleri (kilometre ve çalışma saati asla birbirine karıştırılmaz)
METER_KM = "km"
METER_HOURS = "hours"
METER_LABELS = {METER_KM: "Kilometre", METER_HOURS: "Çalışma saati"}
METER_UNITS = {METER_KM: "km", METER_HOURS: "saat"}

# Araç durumları
STATUS_ACTIVE = "active"
STATUS_IN_MAINTENANCE = "in_maintenance"
STATUS_OUT_OF_SERVICE = "out_of_service"
STATUS_LABELS = {
    STATUS_ACTIVE: "Aktif",
    STATUS_IN_MAINTENANCE: "Bakımda",
    STATUS_OUT_OF_SERVICE: "Kullanım dışı",
}
STATUS_BADGES = {
    STATUS_ACTIVE: "success",
    STATUS_IN_MAINTENANCE: "warning",
    STATUS_OUT_OF_SERVICE: "secondary",
}

# Bakım türleri. Her tür kendi bakım planını taşır (planlar birbirinin üzerine yazılmaz).
MAINT_PERIODIC = "periodic"
MAINT_OIL_FILTER = "oil_filter"
MAINT_TIRE = "tire"
MAINT_BRAKE = "brake"
MAINT_REPAIR = "repair"
MAINT_INSPECTION = "inspection"
MAINT_OTHER = "other"
MAINTENANCE_TYPE_LABELS = {
    MAINT_PERIODIC: "Periyodik bakım",
    MAINT_OIL_FILTER: "Yağ / filtre",
    MAINT_TIRE: "Lastik",
    MAINT_BRAKE: "Fren",
    MAINT_REPAIR: "Onarım",
    MAINT_INSPECTION: "Muayene",
    MAINT_OTHER: "Diğer",
}

MIN_MODEL_YEAR = 1950
MAX_MODEL_YEAR_LIMIT = 2100  # Veritabanı sınırı; uygulama "bu yıl + 1" ile daha dar doğrular.
MIN_VOID_REASON_LENGTH = 5


def _sql_in(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Vehicle(db.Model):
    """Araç veya iş makinesi. Kayıtlar silinmez; kullanım dışı olanlar arşivlenir."""

    __tablename__ = "vehicles"
    __table_args__ = (
        db.CheckConstraint(_sql_in("meter_type", METER_LABELS), name="ck_vehicles_meter_type"),
        db.CheckConstraint(_sql_in("status", STATUS_LABELS), name="ck_vehicles_status"),
        db.CheckConstraint("current_meter >= 0", name="ck_vehicles_current_meter"),
        db.CheckConstraint(
            f"model_year IS NULL OR (model_year >= {MIN_MODEL_YEAR} AND model_year <= {MAX_MODEL_YEAR_LIMIT})",
            name="ck_vehicles_model_year",
        ),
        # Tanımlayıcı uygulamada normalleştirilir (büyük harf, boşluksuz); veritabanı da boşluğu reddeder.
        db.CheckConstraint("length(identifier) > 0 AND instr(identifier, ' ') = 0", name="ck_vehicles_identifier"),
        # Yalnızca kullanım dışı kayıtlar arşive alınabilir.
        db.CheckConstraint(f"archived = 0 OR status = '{STATUS_OUT_OF_SERVICE}'", name="ck_vehicles_archive_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    vehicle_type = db.Column(db.String(60), nullable=False, index=True)
    identifier = db.Column(db.String(30), nullable=False, unique=True)  # plaka veya kurum içi envanter kodu
    brand = db.Column(db.String(60), nullable=True)
    model = db.Column(db.String(60), nullable=True)
    model_year = db.Column(db.Integer, nullable=True)
    meter_type = db.Column(db.String(10), nullable=False)
    current_meter = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE, index=True)
    description = db.Column(db.String(1000), nullable=True)
    archived = db.Column(db.Boolean, nullable=False, default=False, index=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])
    maintenance_records = db.relationship(
        "MaintenanceRecord",
        back_populates="vehicle",
        order_by="MaintenanceRecord.maintenance_date.desc(), MaintenanceRecord.id.desc()",
        lazy="select",
    )

    @property
    def meter_label(self) -> str:
        return METER_LABELS.get(self.meter_type, self.meter_type)

    @property
    def meter_unit(self) -> str:
        return METER_UNITS.get(self.meter_type, "")

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def status_badge(self) -> str:
        return STATUS_BADGES.get(self.status, "secondary")

    def __repr__(self) -> str:
        return f"<Vehicle {self.identifier}>"


class MaintenanceRecord(db.Model):
    """Bakım kaydı. Eklenir; üzerine yazılamaz ve silinemez. Hatalı kayıt MaintenanceVoid ile iptal edilir."""

    __tablename__ = "maintenance_records"
    __table_args__ = (
        db.CheckConstraint(_sql_in("maintenance_type", MAINTENANCE_TYPE_LABELS), name="ck_maint_records_type"),
        db.CheckConstraint("meter_reading >= 0", name="ck_maint_records_meter"),
        db.CheckConstraint("cost_kurus IS NULL OR cost_kurus >= 0", name="ck_maint_records_cost"),
        db.CheckConstraint("next_date IS NULL OR next_date >= maintenance_date", name="ck_maint_records_next_date"),
        db.CheckConstraint("next_meter IS NULL OR next_meter > meter_reading", name="ck_maint_records_next_meter"),
        db.Index("ix_maint_records_plan", "vehicle_id", "maintenance_type", "maintenance_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False, index=True)
    maintenance_type = db.Column(db.String(20), nullable=False)
    maintenance_date = db.Column(db.Date, nullable=False)
    meter_reading = db.Column(db.Integer, nullable=False)  # aracın sayaç türünde (km veya saat)
    description = db.Column(db.String(1000), nullable=False)
    provider = db.Column(db.String(120), nullable=True)
    cost_kurus = db.Column(db.Integer, nullable=True)  # kuruş cinsinden tam sayı
    next_date = db.Column(db.Date, nullable=True)
    next_meter = db.Column(db.Integer, nullable=True)  # aracın sayaç türünde eşik
    notes = db.Column(db.String(1000), nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    vehicle = db.relationship("Vehicle", back_populates="maintenance_records")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    void = db.relationship("MaintenanceVoid", back_populates="record", uselist=False, lazy="joined")

    @property
    def is_void(self) -> bool:
        return self.void is not None

    @property
    def type_label(self) -> str:
        return MAINTENANCE_TYPE_LABELS.get(self.maintenance_type, self.maintenance_type)


class MaintenanceVoid(db.Model):
    """Bakım kaydının gerekçeli iptali. Her kayıt en fazla bir kez iptal edilir; iptal geri alınamaz."""

    __tablename__ = "maintenance_voids"
    __table_args__ = (
        db.CheckConstraint(
            f"length(trim(reason)) >= {MIN_VOID_REASON_LENGTH}", name="ck_maint_voids_reason"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey("maintenance_records.id"), nullable=False, unique=True)
    reason = db.Column(db.String(500), nullable=False)
    voided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    record = db.relationship("MaintenanceRecord", back_populates="void")
    voided_by = db.relationship("User", foreign_keys=[voided_by_id])


# Uygulama katmanı koruması (AuditLog / TicketEvent ile aynı yaklaşım). SQLite tetikleyicileri migration'dadır.
for _model, _name in ((MaintenanceRecord, "Bakım kayıtları"), (MaintenanceVoid, "Bakım iptal kayıtları")):
    @event.listens_for(_model, "before_update")
    def _block_update(mapper, connection, target, _name=_name):
        raise RuntimeError(f"{_name} değiştirilemez.")

    @event.listens_for(_model, "before_delete")
    def _block_delete(mapper, connection, target, _name=_name):
        raise RuntimeError(f"{_name} silinemez.")
