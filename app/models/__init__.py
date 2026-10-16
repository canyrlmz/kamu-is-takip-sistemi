"""Tüm modeller burada içe aktarılır; Flask-Migrate modelleri bu sayede görür."""
from app.models.asset import Asset, AssetAssignment
from app.models.audit_log import AuditLog
from app.models.permission import UserPermission
from app.models.ticket import Ticket, TicketEvent
from app.models.user import User
from app.models.vehicle import MaintenanceRecord, MaintenanceVoid, Vehicle

__all__ = [
    "Asset", "AssetAssignment", "AuditLog", "Ticket", "TicketEvent", "User",
    "Vehicle", "MaintenanceRecord", "MaintenanceVoid", "UserPermission",
]
