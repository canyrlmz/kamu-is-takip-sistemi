"""Denetim kaydı yazma yardımcısı.

Parola, çerez, gizli anahtar gibi hassas değerler asla `details` alanına yazılmamalıdır.
"""
from flask import has_request_context, request

from app.extensions import db
from app.models.audit_log import AuditLog


def log_event(
    action: str,
    *,
    user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: str | None = None,
    commit: bool = True,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        details=(details or None) and details[:255],
        ip_address=request.remote_addr if has_request_context() else None,
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry
