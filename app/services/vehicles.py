"""Araç / iş makinesi ve bakım iş kuralları.

İki bölümden oluşur:
1. Saf hesaplama (veritabanına dokunmaz): bakım planı ve uyarı durumu. Eşikler yalnızca burada tanımlıdır.
2. Veritabanı işlemleri: kayıt oluşturma/düzenleme, bakım ekleme, iptal, arşiv. Kural ihlalinde
   `VehicleRuleError` fırlatılır (mesaj kullanıcıya gösterilebilir; `field` varsa ilgili forma bağlanır).

Uyarılar HİÇBİR YERDE saklanmaz veya önbelleğe alınmaz; her çağrıda güncel kayıtlardan hesaplanır.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func

from app.extensions import db
from app.models.audit_log import (
    MAINTENANCE_ADDED,
    MAINTENANCE_VOIDED,
    VEHICLE_ARCHIVED,
    VEHICLE_CREATED,
    VEHICLE_RESTORED,
    VEHICLE_UPDATED,
)
from app.models.vehicle import (
    MAINTENANCE_TYPE_LABELS,
    MAX_MODEL_YEAR_LIMIT,
    METER_LABELS,
    MIN_MODEL_YEAR,
    MIN_VOID_REASON_LENGTH,
    STATUS_LABELS,
    STATUS_OUT_OF_SERVICE,
    MaintenanceRecord,
    MaintenanceVoid,
    Vehicle,
)
from app.permissions import (
    MAINTENANCE_CREATE,
    MAINTENANCE_VOID,
    VEHICLES_CREATE,
    VEHICLES_UPDATE,
)
from app.services.audit import log_event
from app.services.authz import ensure_permission
from app.utils import today_local

# ---------------------------------------------------------------------------
# Merkezi eşikler ve sınırlar (başka yerde tekrar edilmez)
# ---------------------------------------------------------------------------
WARN_DAYS = 30                                 # tarih için "yaklaşan" eşiği (gün)
WARN_METER = {"km": 1000, "hours": 50}         # sayaç için "yaklaşan" eşiği; birimler karıştırılmaz
MAX_METER = 10_000_000
MAX_COST_KURUS = 10_000_000_00                 # 10 milyon TL
IDENTIFIER_RE = re.compile(r"^[A-Z0-9ÇĞİÖŞÜ][A-Z0-9ÇĞİÖŞÜ._/-]{1,29}$")

# Uyarı durumları
OVERDUE = "overdue"
UPCOMING = "upcoming"
NORMAL = "normal"
UNSCHEDULED = "unscheduled"
INACTIVE = "inactive"                          # arşivli / kullanım dışı: uyarı kapsamı dışında
ALERT_LABELS = {
    OVERDUE: "Gecikmiş", UPCOMING: "Yaklaşan", NORMAL: "Normal",
    UNSCHEDULED: "Planlanmamış", INACTIVE: "Kapsam dışı",
}
ALERT_BADGES = {
    OVERDUE: "danger", UPCOMING: "warning", NORMAL: "success", UNSCHEDULED: "secondary", INACTIVE: "light",
}
_SEVERITY = {NORMAL: 1, UPCOMING: 2, OVERDUE: 3}


class VehicleRuleError(Exception):
    """İş kuralı ihlali; mesaj kullanıcıya gösterilir."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


# ---------------------------------------------------------------------------
# 1) Saf hesaplama
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlanAlert:
    """Tek bir bakım türünün aktif planı ve durumu."""
    maintenance_type: str
    record_id: int
    status: str
    next_date: date | None
    next_meter: int | None
    days_left: int | None      # negatifse tarih geçmiştir
    meter_left: int | None     # aracın kendi sayaç biriminde; negatifse eşik aşılmıştır


@dataclass(frozen=True)
class VehicleAlert:
    status: str
    plans: tuple[PlanAlert, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return ALERT_LABELS[self.status]

    @property
    def badge(self) -> str:
        return ALERT_BADGES[self.status]


def _meter_warning(meter_type: str) -> int:
    try:
        return WARN_METER[meter_type]
    except KeyError:  # bilinmeyen sayaç türü için tahmin yürütülmez
        raise ValueError(f"Bilinmeyen sayaç türü: {meter_type!r}") from None


def evaluate_plan(next_date, next_meter, current_meter: int, meter_type: str, today: date) -> tuple[str, int | None, int | None]:
    """(durum, kalan_gün, kalan_sayaç) döndürür.

    Tarih ve sayaç ölçütleri ayrı değerlendirilir; en kötüsü geçerlidir.
    Gecikmiş: tarih bugünden önce VEYA güncel sayaç eşiğe ulaşmış/aşmış.
    Yaklaşan: tarih bugün+WARN_DAYS içinde VEYA kalan sayaç <= WARN_METER[meter_type].
    """
    warn_meter = _meter_warning(meter_type)
    states = []
    days_left = meter_left = None
    if next_date is not None:
        days_left = (next_date - today).days
        states.append(OVERDUE if days_left < 0 else UPCOMING if days_left <= WARN_DAYS else NORMAL)
    if next_meter is not None:
        meter_left = next_meter - current_meter
        states.append(OVERDUE if meter_left <= 0 else UPCOMING if meter_left <= warn_meter else NORMAL)
    status = max(states, key=_SEVERITY.__getitem__) if states else NORMAL
    return status, days_left, meter_left


def active_plans(records) -> list:
    """Her bakım türü için BELİRLEYİCİ kaydı seçer ve yalnızca aktif planı olanları döndürür.

    Kural: iptal edilmemiş kayıtlar arasında, türü başına en son (bakım tarihi, id) kayıt belirleyicidir.
    Bu kayıtta sonraki tarih veya sayaç eşiğinden biri doluysa tür için aktif plan vardır; ikisi de boşsa
    plan kapanmıştır. Belirleyici kayıt iptal edilirse aynı türün bir önceki geçerli kaydı belirleyici olur.
    Türler birbirinden bağımsızdır.
    """
    latest = {}
    for record in records:
        if record.is_void:
            continue
        key = (record.maintenance_date, record.id)
        current = latest.get(record.maintenance_type)
        if current is None or key > (current.maintenance_date, current.id):
            latest[record.maintenance_type] = record
    return [r for r in latest.values() if r.next_date is not None or r.next_meter is not None]


def compute_vehicle_alert(meter_type: str, current_meter: int, records, today: date) -> VehicleAlert:
    """Genel araç durumu: gecikmiş > yaklaşan > normal. Aktif plan yoksa planlanmamış."""
    plans = []
    for record in active_plans(records):
        status, days_left, meter_left = evaluate_plan(
            record.next_date, record.next_meter, current_meter, meter_type, today
        )
        plans.append(PlanAlert(record.maintenance_type, record.id, status, record.next_date, record.next_meter,
                               days_left, meter_left))
    if not plans:
        return VehicleAlert(UNSCHEDULED)
    plans.sort(key=lambda p: (-_SEVERITY[p.status], p.next_date or date.max, p.maintenance_type))
    return VehicleAlert(max(plans, key=lambda p: _SEVERITY[p.status]).status, tuple(plans))


def alert_in_scope(vehicle) -> bool:
    return not vehicle.archived and vehicle.status != STATUS_OUT_OF_SERVICE


# ---------------------------------------------------------------------------
# Uyarıların veritabanından hesaplanması (önbellek yok)
# ---------------------------------------------------------------------------
def fleet_alerts(vehicles, today: date | None = None) -> dict[int, VehicleAlert]:
    """Verilen araçların uyarıları: {araç_id: VehicleAlert}. Tek sorguyla güncel kayıtlardan hesaplanır."""
    today = today or today_local()
    vehicles = list(vehicles)
    scoped = [v for v in vehicles if alert_in_scope(v)]
    by_vehicle: dict[int, list] = {v.id: [] for v in scoped}
    if scoped:
        for record in db.session.scalars(
            db.select(MaintenanceRecord).where(MaintenanceRecord.vehicle_id.in_(by_vehicle))
        ):
            by_vehicle[record.vehicle_id].append(record)
    result = {}
    for v in vehicles:
        result[v.id] = (
            compute_vehicle_alert(v.meter_type, v.current_meter, by_vehicle[v.id], today)
            if v.id in by_vehicle else VehicleAlert(INACTIVE)
        )
    return result


def vehicle_summary(today: date | None = None) -> dict:
    """Kontrol paneli özeti (arşivdekiler hariç; kullanım dışı araçlar uyarı sayılarına girmez)."""
    vehicles = db.session.scalars(db.select(Vehicle).where(Vehicle.archived.is_(False))).all()
    alerts = fleet_alerts(vehicles, today)
    counts = {key: 0 for key in (OVERDUE, UPCOMING, UNSCHEDULED)}
    for alert in alerts.values():
        if alert.status in counts:
            counts[alert.status] += 1
    return {"total": len(vehicles), "overdue": counts[OVERDUE], "upcoming": counts[UPCOMING],
            "unscheduled": counts[UNSCHEDULED]}


# ---------------------------------------------------------------------------
# 2) Yardımcılar
# ---------------------------------------------------------------------------
def normalize_identifier(raw) -> str:
    """Plaka / envanter kodu: büyük harf, tüm boşluklar silinir ('78 abc 123' -> '78ABC123')."""
    return re.sub(r"\s+", "", raw or "").upper()


_MONEY_RE = re.compile(r"^\d+([.,]\d{1,2})?$")


def parse_money_kurus(text) -> int | None:
    """'1250,50' veya '1250.50' -> 125050 kuruş. Boş -> None. Binlik ayırıcı ve negatif değer reddedilir."""
    text = (text or "").strip().replace(" ", "")
    if not text:
        return None
    if not _MONEY_RE.match(text):
        raise ValueError("Tutarı 1250,50 biçiminde girin (negatif değer ve binlik ayırıcı olmadan).")
    lira, _, cents = text.replace(",", ".").partition(".")
    kurus = int(lira) * 100 + int(cents.ljust(2, "0") or 0)
    if kurus > MAX_COST_KURUS:
        raise ValueError("Tutar çok büyük.")
    return kurus


def _check_meter(value, label, field_name):
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        raise VehicleRuleError(f"{label} tam sayı olmalıdır.", field_name)
    if value < 0:
        raise VehicleRuleError(f"{label} negatif olamaz.", field_name)
    if value > MAX_METER:
        raise VehicleRuleError(f"{label} en fazla {MAX_METER:,} olabilir.".replace(",", "."), field_name)


def _valid_records(vehicle_id: int):
    """İptal edilmemiş bakım kayıtları."""
    return db.select(MaintenanceRecord).where(
        MaintenanceRecord.vehicle_id == vehicle_id, ~MaintenanceRecord.void.has()
    )


def _max_valid_reading(vehicle_id: int) -> int | None:
    return db.session.scalar(
        db.select(func.max(MaintenanceRecord.meter_reading)).where(
            MaintenanceRecord.vehicle_id == vehicle_id, ~MaintenanceRecord.void.has()
        )
    )


def _check_vehicle_data(data: dict, today: date) -> None:
    if not (data.get("vehicle_type") or "").strip():
        raise VehicleRuleError("Araç / makine türü zorunludur.", "vehicle_type")
    if not IDENTIFIER_RE.match(data.get("identifier") or ""):
        raise VehicleRuleError(
            "Plaka / envanter kodu 2-30 karakter olmalı; harf, rakam, nokta, tire, alt çizgi ve eğik çizgi içerebilir.",
            "identifier")
    if data.get("meter_type") not in METER_LABELS:
        raise VehicleRuleError("Geçerli bir sayaç türü seçin.", "meter_type")
    if data.get("status") not in STATUS_LABELS:
        raise VehicleRuleError("Geçerli bir durum seçin.", "status")
    _check_meter(data.get("current_meter"), "Güncel sayaç", "current_meter")
    year = data.get("model_year")
    if year is not None and not (MIN_MODEL_YEAR <= year <= min(today.year + 1, MAX_MODEL_YEAR_LIMIT)):
        raise VehicleRuleError(f"Model yılı {MIN_MODEL_YEAR} ile {today.year + 1} arasında olmalıdır.", "model_year")


def _identifier_taken(identifier: str, exclude_id: int | None = None) -> bool:
    stmt = db.select(Vehicle.id).where(Vehicle.identifier == identifier)
    if exclude_id is not None:
        stmt = stmt.where(Vehicle.id != exclude_id)
    return db.session.scalar(stmt) is not None


VEHICLE_FIELDS = ("vehicle_type", "identifier", "brand", "model", "model_year", "meter_type", "current_meter",
                  "status", "description")
FIELD_LABELS = {
    "vehicle_type": "tür", "identifier": "plaka/kod", "brand": "marka", "model": "model",
    "model_year": "model yılı", "meter_type": "sayaç türü", "current_meter": "güncel sayaç",
    "status": "durum", "description": "açıklama",
}


# ---------------------------------------------------------------------------
# 3) Araç işlemleri
# ---------------------------------------------------------------------------
def create_vehicle(data: dict, acting_user) -> Vehicle:
    ensure_permission(acting_user, VEHICLES_CREATE)
    data = {**data, "identifier": normalize_identifier(data.get("identifier"))}
    _check_vehicle_data(data, today_local())
    if _identifier_taken(data["identifier"]):
        raise VehicleRuleError("Bu plaka / envanter kodu zaten kayıtlı.", "identifier")
    vehicle = Vehicle(created_by_id=acting_user.id, updated_by_id=acting_user.id)
    for name in VEHICLE_FIELDS:
        setattr(vehicle, name, data.get(name))
    vehicle.archived = False
    db.session.add(vehicle)
    db.session.flush()
    log_event(VEHICLE_CREATED, user_id=acting_user.id, entity_type="vehicle", entity_id=vehicle.id,
              details=f"Plaka/kod: {vehicle.identifier}", commit=False)
    db.session.commit()
    return vehicle


def update_vehicle(vehicle: Vehicle, data: dict, acting_user) -> list[str]:
    """Değişen alan adlarını döndürür (boşsa hiçbir şey kaydedilmez)."""
    ensure_permission(acting_user, VEHICLES_UPDATE)
    if vehicle.archived:
        raise VehicleRuleError("Arşivdeki kayıt düzenlenemez. Önce arşivden geri alın.")
    data = {**data, "identifier": normalize_identifier(data.get("identifier"))}
    _check_vehicle_data(data, today_local())
    if _identifier_taken(data["identifier"], exclude_id=vehicle.id):
        raise VehicleRuleError("Bu plaka / envanter kodu başka bir kayıtta kullanılıyor.", "identifier")
    if data["meter_type"] != vehicle.meter_type:
        has_records = db.session.scalar(
            db.select(MaintenanceRecord.id).where(MaintenanceRecord.vehicle_id == vehicle.id).limit(1)
        )
        if has_records is not None:  # iptal edilmiş kayıtlar da sayılır; veritabanı tetikleyicisiyle uyumlu
            raise VehicleRuleError(
                "Bakım kaydı bulunan kaydın sayaç türü (kilometre / saat) değiştirilemez.", "meter_type")
    highest = _max_valid_reading(vehicle.id)
    if highest is not None and data["current_meter"] < highest:
        raise VehicleRuleError(
            f"Güncel sayaç, kayıtlı en yüksek bakım sayacından ({highest}) düşük olamaz. "
            "Hatalı bir bakım kaydı varsa önce onu iptal edin.", "current_meter")
    changed = [f for f in VEHICLE_FIELDS if getattr(vehicle, f) != data.get(f)]
    if not changed:
        return []
    old_meter = vehicle.current_meter
    for name in changed:
        setattr(vehicle, name, data.get(name))
    vehicle.updated_by_id = acting_user.id
    details = "Değişen alanlar: " + ", ".join(FIELD_LABELS[f] for f in changed)
    if "current_meter" in changed:
        details += f" (sayaç {old_meter} → {vehicle.current_meter})"
    log_event(VEHICLE_UPDATED, user_id=acting_user.id, entity_type="vehicle", entity_id=vehicle.id,
              details=details, commit=False)
    db.session.commit()
    return changed


def archive_vehicle(vehicle: Vehicle, acting_user) -> None:
    ensure_permission(acting_user, VEHICLES_UPDATE)
    if vehicle.archived:
        raise VehicleRuleError("Kayıt zaten arşivde.")
    if vehicle.status != STATUS_OUT_OF_SERVICE:
        raise VehicleRuleError("Yalnızca 'Kullanım dışı' durumundaki kayıt arşivlenebilir. Önce durumu değiştirin.")
    vehicle.archived = True
    vehicle.updated_by_id = acting_user.id
    log_event(VEHICLE_ARCHIVED, user_id=acting_user.id, entity_type="vehicle", entity_id=vehicle.id,
              details=f"Plaka/kod: {vehicle.identifier}", commit=False)
    db.session.commit()


def restore_vehicle(vehicle: Vehicle, acting_user) -> None:
    ensure_permission(acting_user, VEHICLES_UPDATE)
    if not vehicle.archived:
        raise VehicleRuleError("Kayıt arşivde değil.")
    vehicle.archived = False
    vehicle.updated_by_id = acting_user.id
    log_event(VEHICLE_RESTORED, user_id=acting_user.id, entity_type="vehicle", entity_id=vehicle.id,
              details=f"Plaka/kod: {vehicle.identifier}", commit=False)
    db.session.commit()


# ---------------------------------------------------------------------------
# 4) Bakım işlemleri
# ---------------------------------------------------------------------------
def add_maintenance(vehicle: Vehicle, data: dict, acting_user) -> MaintenanceRecord:
    """Yeni bakım kaydı ekler. Önceki kayıtlara dokunulmaz; sayaç yalnızca yukarı güncellenir."""
    ensure_permission(acting_user, MAINTENANCE_CREATE)
    if vehicle.archived:
        raise VehicleRuleError("Arşivdeki kayda bakım eklenemez. Önce arşivden geri alın.")
    today = today_local()
    if data.get("maintenance_type") not in MAINTENANCE_TYPE_LABELS:
        raise VehicleRuleError("Geçerli bir bakım türü seçin.", "maintenance_type")
    mdate = data.get("maintenance_date")
    if not isinstance(mdate, date):
        raise VehicleRuleError("Bakım tarihi zorunludur.", "maintenance_date")
    if mdate > today:
        raise VehicleRuleError("Bakım tarihi gelecekte olamaz. Gelecek bakım için 'sonraki bakım' alanlarını kullanın.",
                               "maintenance_date")
    reading = data.get("meter_reading")
    _check_meter(reading, "Bakım sayacı", "meter_reading")
    description = (data.get("description") or "").strip()
    if not description:
        raise VehicleRuleError("Yapılan işlem açıklaması zorunludur.", "description")
    cost = data.get("cost_kurus")
    if cost is not None and (not isinstance(cost, int) or cost < 0 or cost > MAX_COST_KURUS):
        raise VehicleRuleError("Maliyet negatif olamaz ve geçerli bir tutar olmalıdır.", "cost")
    next_date, next_meter = data.get("next_date"), data.get("next_meter")
    if next_date is not None and next_date < mdate:
        raise VehicleRuleError("Sonraki bakım tarihi, bakım tarihinden önce olamaz.", "next_date")
    if next_meter is not None:
        _check_meter(next_meter, "Sonraki bakım sayacı", "next_meter")
        if next_meter <= reading:
            raise VehicleRuleError(
                f"Sonraki bakım sayacı, bakım sayacından ({reading}) büyük olmalıdır.", "next_meter")

    # Sayaç tutarlılığı: önceki tarihli geçerli kayıtların en büyüğünden küçük, sonraki tarihli olanların
    # en küçüğünden büyük olamaz. İptal edilmiş kayıtlar hesaba katılmaz.
    before_max = db.session.scalar(
        db.select(func.max(MaintenanceRecord.meter_reading)).where(
            MaintenanceRecord.vehicle_id == vehicle.id, ~MaintenanceRecord.void.has(),
            MaintenanceRecord.maintenance_date < mdate))
    after_min = db.session.scalar(
        db.select(func.min(MaintenanceRecord.meter_reading)).where(
            MaintenanceRecord.vehicle_id == vehicle.id, ~MaintenanceRecord.void.has(),
            MaintenanceRecord.maintenance_date > mdate))
    if before_max is not None and reading < before_max:
        raise VehicleRuleError(f"Sayaç, daha önceki tarihli bakımın sayacından ({before_max}) düşük olamaz.",
                               "meter_reading")
    if after_min is not None and reading > after_min:
        raise VehicleRuleError(f"Sayaç, daha sonraki tarihli bakımın sayacından ({after_min}) yüksek olamaz.",
                               "meter_reading")

    duplicate = db.session.scalar(
        _valid_records(vehicle.id).where(
            MaintenanceRecord.maintenance_date == mdate,
            MaintenanceRecord.maintenance_type == data["maintenance_type"],
            func.lower(func.trim(MaintenanceRecord.description)) == description.lower(),
        ).limit(1)
    )
    if duplicate is not None:
        raise VehicleRuleError("Aynı tarih, tür ve açıklamayla bir bakım kaydı zaten var.")

    record = MaintenanceRecord(
        vehicle_id=vehicle.id, maintenance_type=data["maintenance_type"], maintenance_date=mdate,
        meter_reading=reading, description=description, provider=(data.get("provider") or "").strip() or None,
        cost_kurus=cost, next_date=next_date, next_meter=next_meter,
        notes=(data.get("notes") or "").strip() or None, created_by_id=acting_user.id,
    )
    db.session.add(record)
    db.session.flush()
    meter_raised = reading > vehicle.current_meter
    if meter_raised:
        vehicle.current_meter = reading
        vehicle.updated_by_id = acting_user.id
    log_event(MAINTENANCE_ADDED, user_id=acting_user.id, entity_type="maintenance_record", entity_id=record.id,
              details=f"Plaka/kod: {vehicle.identifier}; tür: {record.type_label}"
                      + ("; araç sayacı güncellendi" if meter_raised else ""),
              commit=False)
    db.session.commit()
    return record


def void_maintenance(record: MaintenanceRecord, reason, acting_user) -> MaintenanceVoid:
    """Bakım kaydını gerekçeyle iptal eder. Kayıt değişmez/silinmez; iptal geri alınamaz.

    İptal edilen kayıt uyarı hesabından çıkar. Araç sayacı otomatik düşürülmez (gerekirse araç düzenlenir).
    """
    ensure_permission(acting_user, MAINTENANCE_VOID)
    reason = (reason or "").strip()
    if len(reason) < MIN_VOID_REASON_LENGTH:
        raise VehicleRuleError(f"İptal gerekçesi en az {MIN_VOID_REASON_LENGTH} karakter olmalıdır.", "reason")
    if len(reason) > 500:
        raise VehicleRuleError("İptal gerekçesi en fazla 500 karakter olabilir.", "reason")
    vehicle = record.vehicle
    if vehicle.archived:
        raise VehicleRuleError("Arşivdeki kaydın bakımı iptal edilemez. Önce arşivden geri alın.")
    if record.void is not None:
        raise VehicleRuleError("Bu bakım kaydı zaten iptal edilmiş.")
    void = MaintenanceVoid(record_id=record.id, reason=reason, voided_by_id=acting_user.id)
    db.session.add(void)
    db.session.flush()
    log_event(MAINTENANCE_VOIDED, user_id=acting_user.id, entity_type="maintenance_record", entity_id=record.id,
              details=f"Plaka/kod: {vehicle.identifier}; tür: {record.type_label}", commit=False)
    db.session.commit()
    db.session.refresh(record)
    return void
