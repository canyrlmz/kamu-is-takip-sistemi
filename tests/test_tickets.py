import pytest

from app.extensions import db
from app.models.ticket import CATEGORY_LABELS, PRIORITY_LABELS
from app.models.user import ROLE_ADMIN
from tests.conftest import TEST_PASSWORD, get_csrf_token, login
from tests.ticket_helpers import (
    assign_via_service, audit_actions, audit_entries, events_of, force_state, get_ticket, make_ticket,
    ticket_count, ticket_data,
)


# --- Erişim ------------------------------------------------------------------

def test_anonymous_is_redirected_everywhere(app, client, staff_id):
    tid = make_ticket(app, staff_id)
    for url in ("/talepler/", "/talepler/yeni", f"/talepler/{tid}", f"/talepler/{tid}/durum", f"/talepler/{tid}/ata"):
        response = client.get(url)
        assert response.status_code == 302 and "/giris" in response.headers["Location"], url
    for url in ("/talepler/yeni", f"/talepler/{tid}/durum", f"/talepler/{tid}/ata"):
        assert client.post(url, data=ticket_data()).status_code == 302, url
    assert ticket_count(app) == 1


def test_no_delete_route_exists(app, client, admin_id, staff_id):
    tid = make_ticket(app, staff_id)
    login(client, "yonetici")
    assert client.post(f"/talepler/{tid}/sil").status_code == 404
    assert client.delete(f"/talepler/{tid}").status_code == 405
    assert ticket_count(app) == 1


def test_menu_link_is_active_for_both_roles(client, admin_id, staff_id):
    for username in ("personel", "yonetici"):
        login(client, username)
        html = client.get("/").get_data(as_text=True)
        assert 'href="/talepler/"' in html and "(Aşama 3)" not in html
        client.post("/cikis")


# --- Oluşturma ---------------------------------------------------------------

def test_staff_creates_ticket(app, client, admin_id, staff_id):
    login(client, "personel")
    response = client.post("/talepler/yeni", data=ticket_data(priority="urgent"))
    assert response.status_code == 302
    ticket = get_ticket(app, 1)
    assert ticket.number == "TLP-00001"
    assert ticket.status == "new" and ticket.priority == "urgent" and ticket.category == "printer"
    assert ticket.created_by_id == staff_id and ticket.assigned_to_id is None and ticket.closed_at is None
    assert ticket.created_at is not None and ticket.updated_at is not None
    assert events_of(app, 1) == [
        {"type": "created", "from": None, "to": "new", "note": None, "actor": staff_id,
         "from_assignee": None, "to_assignee": None}
    ]
    (entry,) = audit_entries(app, "ticket_created")
    assert entry["user_id"] == staff_id and entry["entity_type"] == "ticket" and entry["entity_id"] == 1
    assert "TLP-00001" in entry["details"]
    html = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "TLP-00001" in html and "Talebiniz alındı: TLP-00001" in html  # başarı bildirimi
    assert "Talebiniz alındı" not in client.get(response.headers["Location"]).get_data(as_text=True)  # yalnızca bir kez


def test_admin_can_create_ticket_too(app, client, admin_id):
    login(client, "yonetici")
    assert client.post("/talepler/yeni", data=ticket_data()).status_code == 302
    assert get_ticket(app, 1).created_by_id == admin_id


def test_ticket_numbers_are_unique_and_readable(app, staff_id):
    ids = [make_ticket(app, staff_id, title=f"Talep {i}") for i in range(3)]
    assert [get_ticket(app, i).number for i in ids] == ["TLP-00001", "TLP-00002", "TLP-00003"]


@pytest.mark.parametrize("category", list(CATEGORY_LABELS))
def test_all_categories_accepted(app, client, staff_id, category):
    login(client, "personel")
    assert client.post("/talepler/yeni", data=ticket_data(category=category)).status_code == 302
    assert get_ticket(app, 1).category == category


@pytest.mark.parametrize("priority", list(PRIORITY_LABELS))
def test_all_priorities_accepted(app, client, staff_id, priority):
    login(client, "personel")
    assert client.post("/talepler/yeni", data=ticket_data(priority=priority)).status_code == 302
    assert get_ticket(app, 1).priority == priority


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"title": ""}, "Başlık zorunludur"),
        ({"title": "   "}, "Başlık zorunludur"),
        ({"description": ""}, "Açıklama zorunludur"),
        ({"location": ""}, "Yer / konum zorunludur"),
        ({"title": "x" * 151}, "En fazla 150"),
        ({"description": "x" * 2001}, "En fazla 2000"),
        ({"location": "x" * 121}, "En fazla 120"),
        ({"category": "bogus"}, "Geçerli bir kategori seçin"),
        ({"category": ""}, "Geçerli bir kategori seçin"),
        ({"priority": "kritik"}, "Geçerli bir öncelik seçin"),
        ({"priority": ""}, "Geçerli bir öncelik seçin"),
    ],
)
def test_invalid_input_rejected_and_nothing_saved(app, client, staff_id, overrides, message):
    login(client, "personel")
    response = client.post("/talepler/yeni", data=ticket_data(**overrides))
    assert response.status_code == 200 and message in response.get_data(as_text=True)
    assert ticket_count(app) == 0
    assert "ticket_created" not in audit_actions(app)


def test_status_and_assignee_cannot_be_set_through_create_request(app, client, admin_id, staff_id):
    login(client, "personel")
    client.post("/talepler/yeni", data=ticket_data(status="resolved", assigned_to_id=admin_id,
                                                  created_by_id=admin_id, closed_at="2026-01-01"))
    ticket = get_ticket(app, 1)
    assert ticket.status == "new" and ticket.assigned_to_id is None and ticket.created_by_id == staff_id


def test_create_form_renders(client, staff_id):
    login(client, "personel")
    html = client.get("/talepler/yeni").get_data(as_text=True)
    for text in ("Başlık", "Açıklama", "Kategori", "Öncelik", "Yer / konum", "Ağ / İnternet", "Acil"):
        assert text in html


# --- Liste ve filtreler ------------------------------------------------------

@pytest.fixture()
def world(app, admin_id, staff_id, make_user):
    """personel: kendi 2 talebi; digeri: 1 talep; biri personele atanmış."""
    other = make_user(app, username="digeri", full_name="Diğer Kişi")
    a = make_ticket(app, staff_id, title="Kendi talebim bir", category="printer", priority="low")
    b = make_ticket(app, staff_id, title="Kendi talebim iki", category="network", priority="urgent")
    c = make_ticket(app, other, title="Başkasının talebi", category="software", priority="high")
    d = make_ticket(app, other, title="Bana atanan başkası", category="electrical")
    assign_via_service(app, d, staff_id, admin_id)
    return {"own1": a, "own2": b, "foreign": c, "assigned": d, "other": other}


def titles(response):
    html = response.get_data(as_text=True)
    return {t for t in ("Kendi talebim bir", "Kendi talebim iki", "Başkasının talebi", "Bana atanan başkası") if t in html}


def test_staff_sees_only_own_and_assigned(client, world):
    login(client, "personel")
    assert titles(client.get("/talepler/")) == {"Kendi talebim bir", "Kendi talebim iki", "Bana atanan başkası"}


def test_admin_sees_all(client, world):
    login(client, "yonetici")
    assert len(titles(client.get("/talepler/"))) == 4


def test_other_user_sees_own_only(client, world):
    login(client, "digeri")
    assert titles(client.get("/talepler/")) == {"Başkasının talebi", "Bana atanan başkası"}


def test_filters(client, world):
    login(client, "yonetici")
    q = lambda **p: titles(client.get("/talepler/", query_string=p))  # noqa: E731
    assert q(durum="new") == titles(client.get("/talepler/")) - set() and len(q(durum="new")) == 4
    assert q(durum="resolved") == set()
    assert q(oncelik="urgent") == {"Kendi talebim iki"}
    assert q(oncelik="low") == {"Kendi talebim bir"}
    assert q(kategori="software") == {"Başkasının talebi"}
    assert q(kategori="network", oncelik="urgent") == {"Kendi talebim iki"}
    assert q(kategori="network", oncelik="low") == set()
    assert q(atama="atanmamis") == {"Kendi talebim bir", "Kendi talebim iki", "Başkasının talebi"}
    assert q(atama="bana") == set()  # yönetici'ye atanan yok


@pytest.mark.parametrize("value", ["TLP-00002", "tlp-00002", "TLP00002", "tlp-2", "2", " 2 "])
def test_filter_by_ticket_number_formats(client, world, value):
    login(client, "yonetici")
    assert titles(client.get("/talepler/", query_string={"no": value})) == {"Kendi talebim iki"}


@pytest.mark.parametrize("value", ["TLP-99999", "abc", "TLP-", "1 OR 1=1", "%", "'; DROP TABLE tickets;--"])
def test_filter_by_bad_ticket_number_returns_nothing(app, client, world, value):
    login(client, "yonetici")
    response = client.get("/talepler/", query_string={"no": value})
    assert response.status_code == 200 and titles(response) == set()
    assert ticket_count(app) == 4
    assert "Seçtiğiniz filtrelere uyan talep bulunamadı" in response.get_data(as_text=True)


def test_staff_filters_never_widen_visibility(client, world):
    login(client, "personel")
    q = lambda **p: titles(client.get("/talepler/", query_string=p))  # noqa: E731
    assert q(kategori="software") == set()  # başkasının talebi filtreyle de görünmez
    assert q(no="3") == set()
    assert q(atama="bana") == {"Bana atanan başkası"}
    assert q(atama="atanmamis") == {"Kendi talebim bir", "Kendi talebim iki"}


def test_list_tolerates_garbage_parameters(client, world):
    login(client, "yonetici")
    params = {"durum": "<script>", "oncelik": "x", "kategori": "y", "atama": "z", "no": "x" * 500, "sayfa": "abc"}
    assert client.get("/talepler/", query_string=params).status_code == 200
    for value in ("-3", "9999"):
        assert client.get("/talepler/", query_string={"sayfa": value}).status_code == 200


def test_empty_list_message(client, admin_id, staff_id):
    login(client, "personel")
    assert "Henüz talep yok" in client.get("/talepler/").get_data(as_text=True)


def test_pagination_keeps_filters(app, client, admin_id, staff_id):
    for i in range(25):
        make_ticket(app, staff_id, title=f"Sayfa talebi {i:02d}", category="network")
    login(client, "yonetici")
    html = client.get("/talepler/", query_string={"kategori": "network"}).get_data(as_text=True)
    assert "Sayfa talebi 24" in html and "Sayfa talebi 00" not in html  # en yeni üstte
    assert "kategori=network" in html and "sayfa=2" in html
    page2 = client.get("/talepler/", query_string={"kategori": "network", "sayfa": 2}).get_data(as_text=True)
    assert "Sayfa talebi 00" in page2


def test_user_input_is_escaped(app, client, admin_id, staff_id):
    tid = make_ticket(app, staff_id, title="<script>alert(1)</script>", description="<img src=x onerror=alert(2)>",
                      location="<b>oda</b>")
    login(client, "yonetici")
    for url in ("/talepler/", f"/talepler/{tid}"):
        html = client.get(url).get_data(as_text=True)
        assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
        assert "<img src=x" not in html and "<b>oda</b>" not in html


# --- Detay erişimi -----------------------------------------------------------

def test_detail_access_rules(app, client, world, admin_id, staff_id):
    own, foreign, assigned = world["own1"], world["foreign"], world["assigned"]
    login(client, "personel")
    assert client.get(f"/talepler/{own}").status_code == 200
    assert client.get(f"/talepler/{assigned}").status_code == 200  # kendisine atanan
    response = client.get(f"/talepler/{foreign}")
    assert response.status_code == 404 and "Başkasının talebi" not in response.get_data(as_text=True)
    assert client.get("/talepler/99999").status_code == 404
    client.post("/cikis")
    login(client, "yonetici")
    for tid in (own, foreign, assigned):
        assert client.get(f"/talepler/{tid}").status_code == 200


def test_staff_cannot_reach_foreign_ticket_through_any_route(app, client, world):
    foreign = world["foreign"]
    login(client, "personel")
    assert client.get(f"/talepler/{foreign}/durum").status_code == 404
    assert client.post(f"/talepler/{foreign}/durum", data={"status": "reviewing"}).status_code == 404
    assert get_ticket(app, foreign).status == "new"


# --- CSRF --------------------------------------------------------------------

def test_ticket_posts_require_csrf(csrf_app, make_user):
    make_user(csrf_app, username="yonetici", role=ROLE_ADMIN)
    uid = make_user(csrf_app, username="personel")
    tid = make_ticket(csrf_app, uid)
    client = csrf_app.test_client()
    token = get_csrf_token(client)
    client.post("/giris", data={"username": "yonetici", "password": TEST_PASSWORD, "csrf_token": token})

    assert client.post("/talepler/yeni", data=ticket_data()).status_code == 400
    assert client.post(f"/talepler/{tid}/durum", data={"status": "reviewing"}).status_code == 400
    assert client.post(f"/talepler/{tid}/ata", data={"assignee_id": uid}).status_code == 400
    assert ticket_count(csrf_app) == 1 and get_ticket(csrf_app, tid).status == "new"

    token = get_csrf_token(client, "/talepler/yeni")
    assert client.post("/talepler/yeni", data=ticket_data(csrf_token=token)).status_code == 302
    token = get_csrf_token(client, f"/talepler/{tid}/durum")
    assert client.post(f"/talepler/{tid}/durum", data={"status": "reviewing", "csrf_token": token}).status_code == 302
    assert get_ticket(csrf_app, tid).status == "reviewing"


# --- Panel -------------------------------------------------------------------

def test_dashboard_counts_are_scoped_and_computed(app, client, world, admin_id, staff_id):
    from app.models.user import User
    from app.services.tickets import ticket_summary

    force_state(app, world["own2"], "in_progress", assignee_id=staff_id)
    force_state(app, world["foreign"], "resolved")
    with app.app_context():
        admin = db.session.get(User, admin_id)
        staff = db.session.get(User, staff_id)
        assert ticket_summary(admin) == {"open": 3, "in_progress": 1, "mine": 0, "unassigned": 1}
        # personel: own1(new), own2(in_progress, kendine atanmış), assigned(new, kendine atanmış)
        assert ticket_summary(staff) == {"open": 3, "in_progress": 1, "mine": 2, "unassigned": None}
    login(client, "personel")
    html = client.get("/").get_data(as_text=True)
    assert "Açık talep" in html and "Atanmamış (açık)" not in html
    client.post("/cikis")
    login(client, "yonetici")
    assert "Atanmamış (açık)" in client.get("/").get_data(as_text=True)
