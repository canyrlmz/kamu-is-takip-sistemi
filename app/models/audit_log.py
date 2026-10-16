from sqlalchemy import event

from app.extensions import db
from app.utils import utcnow

# İşlem türleri
LOGIN_SUCCESS = "login_success"
LOGIN_FAILED = "login_failed"
LOGOUT = "logout"
USER_CREATED = "user_created"
USER_ACTIVATED = "user_activated"
USER_DEACTIVATED = "user_deactivated"
USER_UPDATED = "user_updated"
USER_ROLE_CHANGED = "user_role_changed"  # Aşama 5 migration'ı da bu işlem adıyla kayıt yazar
USER_SENIORITY_CHANGED = "user_seniority_changed"
USER_PERMISSIONS_CHANGED = "user_permissions_changed"
SUPERADMIN_TRANSFERRED = "superadmin_transferred"
ASSET_CREATED = "asset_created"
ASSET_UPDATED = "asset_updated"
ASSET_ARCHIVED = "asset_archived"
ASSET_RESTORED = "asset_restored"
ASSET_ASSIGNED = "asset_assigned"
ASSET_RETURNED = "asset_returned"
TICKET_CREATED = "ticket_created"
TICKET_STATUS_CHANGED = "ticket_status_changed"
TICKET_ASSIGNED = "ticket_assigned"
TICKET_REASSIGNED = "ticket_reassigned"
VEHICLE_CREATED = "vehicle_created"
VEHICLE_UPDATED = "vehicle_updated"
VEHICLE_ARCHIVED = "vehicle_archived"
VEHICLE_RESTORED = "vehicle_restored"
MAINTENANCE_ADDED = "maintenance_added"
MAINTENANCE_VOIDED = "maintenance_voided"

ACTION_LABELS = {
    LOGIN_SUCCESS: "Başarılı giriş",
    LOGIN_FAILED: "Başarısız giriş denemesi",
    LOGOUT: "Çıkış",
    USER_CREATED: "Kullanıcı oluşturuldu",
    USER_ACTIVATED: "Kullanıcı etkinleştirildi",
    USER_DEACTIVATED: "Kullanıcı devre dışı bırakıldı",
    USER_UPDATED: "Kullanıcı bilgileri güncellendi",
    USER_ROLE_CHANGED: "Kullanıcı rolü değişti",
    USER_SENIORITY_CHANGED: "Kullanıcı kıdem seviyesi değişti",
    USER_PERMISSIONS_CHANGED: "Kullanıcı izinleri değişti",
    SUPERADMIN_TRANSFERRED: "Süper admin yetkisi devredildi",
    ASSET_CREATED: "Demirbaş oluşturuldu",
    ASSET_UPDATED: "Demirbaş güncellendi",
    ASSET_ARCHIVED: "Demirbaş arşivlendi",
    ASSET_RESTORED: "Demirbaş arşivden geri alındı",
    ASSET_ASSIGNED: "Zimmet verildi",
    ASSET_RETURNED: "Zimmet iade alındı",
    TICKET_CREATED: "Talep oluşturuldu",
    TICKET_STATUS_CHANGED: "Talep durumu değişti",
    TICKET_ASSIGNED: "Talep sorumluya atandı",
    TICKET_REASSIGNED: "Talep yeniden atandı",
    VEHICLE_CREATED: "Araç / iş makinesi oluşturuldu",
    VEHICLE_UPDATED: "Araç / iş makinesi güncellendi",
    VEHICLE_ARCHIVED: "Araç / iş makinesi arşivlendi",
    VEHICLE_RESTORED: "Araç / iş makinesi arşivden geri alındı",
    MAINTENANCE_ADDED: "Bakım kaydı eklendi",
    MAINTENANCE_VOIDED: "Bakım kaydı iptal edildi",
}


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(50), nullable=False, index=True)
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)

    user = db.relationship("User", lazy="joined")

    @property
    def action_label(self) -> str:
        return ACTION_LABELS.get(self.action, self.action)


# Denetim kayıtları uygulama üzerinden değiştirilemez ve silinemez.
@event.listens_for(AuditLog, "before_update")
def _block_update(mapper, connection, target):
    raise RuntimeError("Denetim kayıtları değiştirilemez.")


@event.listens_for(AuditLog, "before_delete")
def _block_delete(mapper, connection, target):
    raise RuntimeError("Denetim kayıtları silinemez.")
