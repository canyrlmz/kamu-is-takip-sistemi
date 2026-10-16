"""Aşama 5.2 koruması: uygulama kodunda rol adına bağlı eski yetki kontrolleri geri gelmesin."""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
FORBIDDEN = [
    r"\bis_admin\b", r"\bROLE_ADMIN\b", r"\bROLE_STAFF\b", r"\broles_required\b", r"\bWRITE_ROLES\b",
    r"\bcan_manage_\w+", r"current_user\.role\b", r"\.role\s+in\s+\(", r"\.role\s*==\s*['\"]",
]
# Eski adlar yalnızca geriye dönük uyumluluk takma adı olarak tanımlanır.
ALLOWED_DEFINITIONS = {APP / "models" / "user.py"}


def _sources():
    return [p for p in APP.rglob("*") if p.suffix in {".py", ".html"} and "__pycache__" not in p.parts]


def test_no_role_name_based_access_checks_remain_in_application_code():
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN:
            for match in re.finditer(pattern, text):
                if path in ALLOWED_DEFINITIONS and "ROLE_" in match.group(0):
                    continue
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(APP)}:{line}: {match.group(0)}")
    assert not offenders, "Eski rol tabanlı kontrol bulundu:\n" + "\n".join(offenders)


def test_every_route_module_uses_permission_decorators_not_bare_login_required_for_data():
    """Veri sunan modül route'larında çıplak @login_required kalmamalı (izin dekoratörü kullanılmalı)."""
    for module in ("assets", "tickets", "vehicles", "admin", "dashboard"):
        for path in (APP / module).glob("routes.py"):
            text = path.read_text(encoding="utf-8")
            if module == "dashboard":
                continue  # kontrol paneli yalnızca giriş ister; içeriği izne göre süzülür
            assert "@login_required" not in text, path
