"""Demirbaş ve zimmet iş kuralları.

Kurallar burada toplanır; route'lar yalnızca form/yetki işini yapar. Kural ihlalinde
`AssetRuleError` fırlatılır (mesaj kullanıcıya gösterilebilir).
"""
from datetime import date

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.asset import (
    STATUS_IN_STORAGE,
    STATUS_IN_USE,
    STATUS_MAINTENANCE,
    STATUS_SCRAP_CANDIDATE,
    Asset,
    AssetAssignment,
)
from app.models.audit_log import (
    ASSET_ARCHIVED,
    ASSET_ASSIGNED,
    ASSET_CREATED,
    ASSET_RESTORED,
    ASSET_RETURNED,
    ASSET_UPDATED,
)
from app.models.user import User
from app.permissions import ASSETS_ASSIGN, ASSETS_CREATE, ASSETS_UPDATE
from app.services.audit import log_event
from app.services.authz import ensure_permission
from app.utils import today_local, utcnow

# İadeden sonra seçilebilecek durumlar ("Kullanımda" yalnızca aktif zimmetle oluşur).
RETURN_STATUSES = (STATUS_IN_STORAGE, STATUS_MAINTENANCE, STATUS_SCRAP_CANDIDATE)


class AssetRuleError(Exception):
    """İş kuralı ihlali; mesaj kullanıcıya gösterilir."""


FIELD_LABELS = {
    "code": "kod", "name": "ad", "category": "kategori", "brand": "marka", "model": "model",
    "serial_number": "seri no", "location": "konum", "acquisition_date": "edinim tarihi",
    "status": "durum", "description": "açıklama",
}
EDITABLE_FIELDS = ["code", "name", "category", "brand", "model", "serial_number", "location",
                   "acquisition_date", "description"]


def get_active_assignment(asset_id: int) -> AssetAssignment | None:
    return db.session.scalar(
        db.select(AssetAssignment).where(
            AssetAssignment.asset_id == asset_id, AssetAssignment.return_date.is_(None)
        )
    )


def _last_return_date(asset_id: int) -> date | None:
    return db.session.scalar(
        db.select(func.max(AssetAssignment.return_date)).where(AssetAssignment.asset_id == asset_id)
    )


def create_asset(data: dict, acting_user: User) -> Asset:
    """Yeni demirbaş. `data`: EDITABLE_FIELDS + status. Kod/seri no çakışmasında IntegrityError çağırana iletilir
    (işlem geri alınmış olur)."""
    ensure_permission(acting_user, ASSETS_CREATE)
    asset = Asset(created_by_id=acting_user.id, updated_by_id=acting_user.id, status=data["status"])
    for name in EDITABLE_FIELDS:
        setattr(asset, name, data[name])
    try:
        db.session.add(asset)
        db.session.flush()
        log_event(ASSET_CREATED, user_id=acting_user.id, entity_type="asset", entity_id=asset.id,
                  details=f"Kod: {asset.code}", commit=False)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()  # eş zamanlı kayıtta benzersizlik kısıtı devreye girer
        raise
    return asset


def update_asset(asset: Asset, data: dict, acting_user: User, *, locked: bool) -> list[str]:
    """Demirbaşı günceller; değişen alan adlarını döndürür. Zimmetli (kilitli) demirbaşta durum değiştirilemez."""
    ensure_permission(acting_user, ASSETS_UPDATE)
    fields = EDITABLE_FIELDS + ([] if locked else ["status"])
    changed = [f for f in fields if getattr(asset, f) != data[f]]
    if not changed:
        return []
    for name in changed:
        setattr(asset, name, data[name])
    asset.updated_by_id = acting_user.id
    try:
        log_event(ASSET_UPDATED, user_id=acting_user.id, entity_type="asset", entity_id=asset.id,
                  details="Değişen alanlar: " + ", ".join(FIELD_LABELS[f] for f in changed), commit=False)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise
    return changed


def assign_asset(asset: Asset, assignee: User, start_date: date, note: str | None, acting_user: User) -> AssetAssignment:
    ensure_permission(acting_user, ASSETS_ASSIGN)
    if asset.archived:
        raise AssetRuleError("Arşivdeki demirbaş zimmetlenemez. Önce arşivden geri alın.")
    if get_active_assignment(asset.id) is not None:
        raise AssetRuleError("Bu demirbaş zaten zimmetli. Yeniden zimmetlemek için önce iade alınmalıdır.")
    if asset.status != STATUS_IN_STORAGE:
        raise AssetRuleError("Yalnızca 'Depoda' durumundaki demirbaş zimmetlenebilir.")
    if assignee is None or not assignee.active:
        raise AssetRuleError("Zimmet yalnızca etkin bir kullanıcıya verilebilir.")
    if start_date > today_local():
        raise AssetRuleError("Zimmet başlangıç tarihi gelecekte olamaz.")
    if asset.acquisition_date and start_date < asset.acquisition_date:
        raise AssetRuleError("Zimmet başlangıç tarihi edinim tarihinden önce olamaz.")
    previous_return = _last_return_date(asset.id)
    if previous_return and start_date < previous_return:
        raise AssetRuleError("Zimmet başlangıç tarihi, önceki zimmetin iade tarihinden önce olamaz.")

    assignment = AssetAssignment(
        asset_id=asset.id,
        assignee_id=assignee.id,
        start_date=start_date,
        assign_note=note or None,
        assigned_by_id=acting_user.id,
    )
    asset.status = STATUS_IN_USE
    asset.updated_by_id = acting_user.id
    try:
        db.session.add(assignment)
        db.session.flush()  # kısmi benzersiz indeks: eş zamanlı ikinci zimmet burada reddedilir
        log_event(
            ASSET_ASSIGNED,
            user_id=acting_user.id,
            entity_type="asset_assignment",
            entity_id=assignment.id,
            details=f"Demirbaş: {asset.code}, personel: {assignee.username}",
            commit=False,
        )
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise AssetRuleError("Bu demirbaş zaten zimmetli.") from exc
    return assignment


def return_asset(
    asset: Asset, return_date: date, status_after: str, note: str | None, acting_user: User
) -> AssetAssignment:
    ensure_permission(acting_user, ASSETS_ASSIGN)
    assignment = get_active_assignment(asset.id)
    if assignment is None:
        raise AssetRuleError("Bu demirbaşın aktif bir zimmeti yok.")
    if status_after not in RETURN_STATUSES:
        raise AssetRuleError("İade sonrası durum geçersiz.")
    if return_date > today_local():
        raise AssetRuleError("İade tarihi gelecekte olamaz.")
    if return_date < assignment.start_date:
        raise AssetRuleError("İade tarihi zimmet başlangıç tarihinden önce olamaz.")

    assignment.return_date = return_date
    assignment.return_note = note or None
    assignment.returned_by_id = acting_user.id
    assignment.returned_at = utcnow()
    asset.status = status_after
    asset.updated_by_id = acting_user.id
    log_event(
        ASSET_RETURNED,
        user_id=acting_user.id,
        entity_type="asset_assignment",
        entity_id=assignment.id,
        details=f"Demirbaş: {asset.code}, personel: {assignment.assignee.username}",
        commit=False,
    )
    db.session.commit()
    return assignment


def archive_asset(asset: Asset, acting_user: User) -> None:
    ensure_permission(acting_user, ASSETS_UPDATE)
    if asset.archived:
        raise AssetRuleError("Bu demirbaş zaten arşivde.")
    if get_active_assignment(asset.id) is not None:
        raise AssetRuleError("Zimmetli demirbaş arşivlenemez. Önce zimmet iade alınmalıdır.")
    asset.archived = True
    asset.updated_by_id = acting_user.id
    log_event(
        ASSET_ARCHIVED, user_id=acting_user.id, entity_type="asset", entity_id=asset.id,
        details=f"Kod: {asset.code}", commit=False,
    )
    db.session.commit()


def restore_asset(asset: Asset, acting_user: User) -> None:
    ensure_permission(acting_user, ASSETS_UPDATE)
    if not asset.archived:
        raise AssetRuleError("Bu demirbaş arşivde değil.")
    asset.archived = False
    asset.updated_by_id = acting_user.id
    log_event(
        ASSET_RESTORED, user_id=acting_user.id, entity_type="asset", entity_id=asset.id,
        details=f"Kod: {asset.code}", commit=False,
    )
    db.session.commit()


def asset_summary() -> dict:
    """Kontrol paneli özeti; tüm değerler veritabanından hesaplanır (arşivdekiler hariç)."""
    active_assignment = (
        db.select(AssetAssignment.id)
        .where(AssetAssignment.asset_id == Asset.id, AssetAssignment.return_date.is_(None))
        .exists()
    )
    total = db.session.scalar(db.select(func.count()).select_from(Asset).where(Asset.archived.is_(False)))
    assigned = db.session.scalar(
        db.select(func.count())
        .select_from(AssetAssignment)
        .join(Asset, Asset.id == AssetAssignment.asset_id)
        .where(AssetAssignment.return_date.is_(None), Asset.archived.is_(False))
    )
    available = db.session.scalar(
        db.select(func.count())
        .select_from(Asset)
        .where(Asset.archived.is_(False), Asset.status == STATUS_IN_STORAGE, ~active_assignment)
    )
    return {"total": total, "assigned": assigned, "available": available}
