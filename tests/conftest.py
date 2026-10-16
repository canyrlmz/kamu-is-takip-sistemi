import re

import pytest

from app import create_app
from app.extensions import db
from app.models.user import ROLE_ADMIN, ROLE_STAFF, User
from app.permissions import LEGACY_STAFF_PERMISSIONS

# Yalnızca testlerde kullanılan, gerçek olmayan örnek parola.
TEST_PASSWORD = "Test-Parola-123!"


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def csrf_app():
    app = create_app("testing", {"WTF_CSRF_ENABLED": True})
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_user():
    def _make(app, username="ayse", role=ROLE_STAFF, active=True, password=TEST_PASSWORD, full_name="Ayşe Örnek"):
        with app.app_context():
            user = User(username=username, full_name=full_name, role=role, active=active)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            if role == ROLE_STAFF:  # Aşama 5: eski personel = arıza kontrol + bugünkü yetkilerinin karşılığı
                user.set_permissions(LEGACY_STAFF_PERMISSIONS)
            db.session.commit()
            return user.id

    return _make


@pytest.fixture()
def admin_id(app, make_user):
    return make_user(app, username="yonetici", role=ROLE_ADMIN, full_name="Yönetici Örnek")


@pytest.fixture()
def staff_id(app, make_user):
    return make_user(app, username="personel", role=ROLE_STAFF, full_name="Personel Örnek")


def login(client, username, password=TEST_PASSWORD, **kwargs):
    return client.post("/giris", data={"username": username, "password": password}, **kwargs)


def get_csrf_token(client, url="/giris"):
    html = client.get(url).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html) or re.search(
        r'value="([^"]+)"[^>]*name="csrf_token"', html
    )
    assert match, "CSRF belirteci sayfada bulunamadı"
    return match.group(1)


# --- Aşama 2: demirbaş yardımcıları -------------------------------------------
from datetime import date, timedelta  # noqa: E402

from app.models.asset import STATUS_IN_STORAGE, Asset  # noqa: E402
from app.utils import today_local  # noqa: E402


def local_today(app) -> date:
    with app.app_context():
        return today_local()


def iso(d: date) -> str:
    return d.isoformat()


@pytest.fixture()
def make_asset():
    def _make(app, code="DMB-0001", name="Örnek Bilgisayar", category="Bilgisayar", status=STATUS_IN_STORAGE,
              serial_number=None, archived=False, acquisition_date=None, location=None, brand=None, model=None):
        with app.app_context():
            asset = Asset(code=code, name=name, category=category, status=status, serial_number=serial_number,
                          archived=archived, acquisition_date=acquisition_date, location=location,
                          brand=brand, model=model)
            db.session.add(asset)
            db.session.commit()
            return asset.id

    return _make
