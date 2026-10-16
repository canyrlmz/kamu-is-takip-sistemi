"""Bakım uyarı hesabı: saf fonksiyonlar için küçük, bağımsız birim testleri (veritabanı kullanmaz)."""
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.services import vehicles as svc
from app.services.vehicles import (
    INACTIVE, NORMAL, OVERDUE, UNSCHEDULED, UPCOMING, WARN_DAYS, WARN_METER,
    active_plans, compute_vehicle_alert, evaluate_plan,
)

TODAY = date(2026, 10, 16)


@dataclass
class Rec:
    id: int
    maintenance_type: str = "periodic"
    maintenance_date: date = date(2026, 1, 1)
    next_date: date | None = None
    next_meter: int | None = None
    is_void: bool = False


def days(n):
    return TODAY + timedelta(days=n)


# --- merkezi eşikler ---------------------------------------------------------
def test_thresholds_are_central_and_units_are_separate():
    assert WARN_DAYS == 30
    assert WARN_METER == {"km": 1000, "hours": 50}


# --- tek plan değerlendirmesi ------------------------------------------------
@pytest.mark.parametrize("offset,expected", [
    (-1, OVERDUE), (0, UPCOMING), (WARN_DAYS, UPCOMING), (WARN_DAYS + 1, NORMAL),
])
def test_date_boundaries(offset, expected):
    status, days_left, meter_left = evaluate_plan(days(offset), None, 0, "km", TODAY)
    assert status == expected and days_left == offset and meter_left is None


@pytest.mark.parametrize("meter_type,left,expected", [
    ("km", 0, OVERDUE), ("km", -5, OVERDUE), ("km", 1, UPCOMING), ("km", 1000, UPCOMING), ("km", 1001, NORMAL),
    ("hours", 0, OVERDUE), ("hours", 50, UPCOMING), ("hours", 51, NORMAL),
])
def test_meter_boundaries_use_the_vehicle_unit(meter_type, left, expected):
    current = 20_000
    status, _, meter_left = evaluate_plan(None, current + left, current, meter_type, TODAY)
    assert status == expected and meter_left == left


def test_km_and_hours_are_never_mixed():
    # Aynı sayılar: 500 kalan sayaç km için yaklaşan (<=1000) ama saat için normaldir (>50).
    assert evaluate_plan(None, 10_500, 10_000, "km", TODAY)[0] == UPCOMING
    assert evaluate_plan(None, 10_500, 10_000, "hours", TODAY)[0] == NORMAL
    # 40 kalan: saat için yaklaşan, km için de yaklaşan; 1500 kalan: ikisi için de normal.
    assert evaluate_plan(None, 10_040, 10_000, "hours", TODAY)[0] == UPCOMING
    assert evaluate_plan(None, 11_500, 10_000, "km", TODAY)[0] == NORMAL


def test_unknown_meter_type_is_rejected_not_guessed():
    with pytest.raises(ValueError):
        evaluate_plan(None, 100, 0, "miles", TODAY)


def test_worst_of_date_and_meter_wins():
    assert evaluate_plan(days(200), 10_000, 10_500, "km", TODAY)[0] == OVERDUE      # sayaç aşıldı
    assert evaluate_plan(days(-3), 99_999, 10_000, "km", TODAY)[0] == OVERDUE       # tarih geçti
    assert evaluate_plan(days(10), 99_999, 10_000, "km", TODAY)[0] == UPCOMING
    assert evaluate_plan(days(200), 99_999, 10_000, "km", TODAY)[0] == NORMAL


# --- belirleyici kayıt ve plan türleri -----------------------------------------
def test_latest_valid_record_per_type_is_decisive():
    old = Rec(1, maintenance_date=date(2026, 1, 1), next_date=days(-100))   # gecikmiş görünürdü
    new = Rec(2, maintenance_date=date(2026, 6, 1), next_date=days(100))
    assert [r.id for r in active_plans([old, new])] == [2]
    assert [r.id for r in active_plans([new, old])] == [2]  # sıra önemsiz


def test_same_date_tie_is_broken_by_id():
    a = Rec(1, maintenance_date=date(2026, 6, 1), next_date=days(-5))
    b = Rec(2, maintenance_date=date(2026, 6, 1), next_date=days(300))
    assert [r.id for r in active_plans([a, b])] == [2]


def test_backdated_entry_does_not_override_newer_record():
    newer = Rec(1, maintenance_date=date(2026, 8, 1), next_date=days(200))
    backfilled = Rec(2, maintenance_date=date(2026, 2, 1), next_date=days(-50))  # sonradan girilen eski kayıt
    assert [r.id for r in active_plans([newer, backfilled])] == [1]


def test_voided_records_are_ignored():
    valid = Rec(1, maintenance_date=date(2026, 3, 1), next_date=days(40))
    voided_newer = Rec(2, maintenance_date=date(2026, 9, 1), next_date=days(-10), is_void=True)
    assert [r.id for r in active_plans([valid, voided_newer])] == [1]  # iptal sonrası önceki kayıt yeniden belirleyici


def test_plan_closes_when_latest_valid_record_has_no_next_values():
    planned = Rec(1, maintenance_date=date(2026, 3, 1), next_date=days(-20), next_meter=5000)
    closing = Rec(2, maintenance_date=date(2026, 9, 1))  # sonraki bakım belirtilmemiş
    assert active_plans([planned, closing]) == []
    # Kapatan kayıt iptal edilirse eski plan yeniden aktif olur.
    closing.is_void = True
    assert [r.id for r in active_plans([planned, closing])] == [1]


def test_types_are_independent_and_do_not_overwrite_each_other():
    oil = Rec(1, "oil_filter", date(2026, 3, 1), next_date=days(-5))
    tire = Rec(2, "tire", date(2026, 9, 1), next_date=days(200))     # daha yeni ama başka tür
    brake_closed = Rec(3, "brake", date(2026, 9, 2))                 # planı kapalı tür
    plans = active_plans([oil, tire, brake_closed])
    assert {r.maintenance_type for r in plans} == {"oil_filter", "tire"}
    alert = compute_vehicle_alert("km", 0, [oil, tire, brake_closed], TODAY)
    assert alert.status == OVERDUE  # yeni lastik kaydı, geciken yağ bakımını gizlemez
    assert {p.maintenance_type: p.status for p in alert.plans} == {"oil_filter": OVERDUE, "tire": NORMAL}


# --- genel araç durumu ---------------------------------------------------------
def test_overall_priority_overdue_over_upcoming_over_normal():
    normal = Rec(1, "tire", next_date=days(200))
    upcoming = Rec(2, "brake", next_date=days(10))
    overdue = Rec(3, "oil_filter", next_date=days(-1))
    assert compute_vehicle_alert("km", 0, [normal], TODAY).status == NORMAL
    assert compute_vehicle_alert("km", 0, [normal, upcoming], TODAY).status == UPCOMING
    assert compute_vehicle_alert("km", 0, [normal, upcoming, overdue], TODAY).status == OVERDUE
    plans = compute_vehicle_alert("km", 0, [normal, upcoming, overdue], TODAY).plans
    assert [p.status for p in plans] == [OVERDUE, UPCOMING, NORMAL]  # en kritik önce


def test_unscheduled_only_when_no_active_plan_in_any_type():
    assert compute_vehicle_alert("km", 0, [], TODAY).status == UNSCHEDULED
    assert compute_vehicle_alert("km", 0, [Rec(1)], TODAY).status == UNSCHEDULED  # kayıt var, plan yok
    only_voided = Rec(1, next_date=days(5), is_void=True)
    assert compute_vehicle_alert("km", 0, [only_voided], TODAY).status == UNSCHEDULED
    mixed = [Rec(1, "oil_filter"), Rec(2, "tire", next_date=days(100))]
    assert compute_vehicle_alert("km", 0, mixed, TODAY).status == NORMAL  # bir tür aktifse planlanmamış değildir


def test_meter_threshold_uses_current_meter_so_meter_updates_change_result():
    rec = Rec(1, next_meter=20_000)
    assert compute_vehicle_alert("km", 10_000, [rec], TODAY).status == NORMAL
    assert compute_vehicle_alert("km", 19_500, [rec], TODAY).status == UPCOMING
    assert compute_vehicle_alert("km", 20_000, [rec], TODAY).status == OVERDUE


def test_alert_scope_excludes_archived_and_out_of_service():
    @dataclass
    class V:
        archived: bool
        status: str

    assert svc.alert_in_scope(V(False, "active"))
    assert svc.alert_in_scope(V(False, "in_maintenance"))
    assert not svc.alert_in_scope(V(False, "out_of_service"))
    assert not svc.alert_in_scope(V(True, "out_of_service"))
    assert INACTIVE not in (OVERDUE, UPCOMING, NORMAL, UNSCHEDULED)


# --- tutar ayrıştırma ---------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("1250,50", 125050), ("1250.50", 125050), ("1250", 125000), ("0,5", 50), ("12,05", 1205), ("", None), ("  ", None),
])
def test_parse_money(text, expected):
    assert svc.parse_money_kurus(text) == expected


@pytest.mark.parametrize("text", ["-5", "-0,50", "1.250,50", "1,250.50", "12,345", "abc", "1e3", "99999999999"])
def test_parse_money_rejects_bad_input(text):
    with pytest.raises(ValueError):
        svc.parse_money_kurus(text)


def test_identifier_normalization():
    assert svc.normalize_identifier(" 78 abc 123 ") == "78ABC123"
    assert svc.normalize_identifier("iş-01") == "IŞ-01"  # Python büyük harf dönüşümü; tutarlı olması yeterli
