"""Aşama 5.2: talep modülünde izin tabanlı görünürlük, durum geçişi ve atama kuralları."""
import pytest

from app.extensions import db
from app.models.ticket import Ticket
from app.models.user import User
from app.permissions import ROLE_ARIZA_KONTROL, ROLE_BIRIM_SORUMLUSU, ROLE_GORUNTULEYICI, ROLE_SUPERADMIN
from app.services import tickets as svc
from tests.authz_helpers import make_profile
from tests.conftest import login
from tests.ticket_helpers import audit_actions, force_state, get_ticket, make_ticket


@pytest.fixture()
def world(app):
    root = make_profile(app, "kok", ROLE_SUPERADMIN)
    creator = make_profile(app, "acan", ROLE_BIRIM_SORUMLUSU, {"tickets.view", "tickets.create"})
    worker = make_profile(app, "isci", ROLE_ARIZA_KONTROL, {"tickets.view", "tickets.update"})
    return {"root": root, "creator": creator, "worker": worker,
            "t_root": make_ticket(app, root, "Kök talebi"), "t_creator": make_ticket(app, creator, "Açanın talebi")}


def ids_in(html, world):
    return {name for name in ("t_root", "t_creator") if f"/talepler/{world[name]}" in html}


def test_view_permission_sees_only_own_and_assigned_tickets(app, client, world):
    login(client, "acan")
    assert ids_in(client.get("/talepler/").get_data(as_text=True), world) == {"t_creator"}
    assert client.get(f"/talepler/{world['t_root']}").status_code == 404  # varlığı bile sızdırılmaz
    force_state(app, world["t_root"], "new", assignee_id=world["creator"])  # kurulum: atanmış talep
    assert ids_in(client.get("/talepler/").get_data(as_text=True), world) == {"t_root", "t_creator"}


def test_view_all_permission_sees_every_ticket_without_other_rights(app, client, world):
    make_profile(app, "izleyici", ROLE_GORUNTULEYICI, {"tickets.view_all"})
    login(client, "izleyici")
    assert ids_in(client.get("/talepler/").get_data(as_text=True), world) == {"t_root", "t_creator"}
    assert client.get(f"/talepler/{world['t_root']}").status_code == 200
    assert client.get(f"/talepler/{world['t_root']}/durum").status_code == 403  # görmek değiştirmek değildir
    assert client.get(f"/talepler/{world['t_root']}/ata").status_code == 403
    assert client.get("/talepler/yeni").status_code == 403


def test_viewer_role_without_view_all_sees_nothing_of_others(app, client, world):
    make_profile(app, "izleyici", ROLE_GORUNTULEYICI, {"tickets.view"})
    login(client, "izleyici")
    assert ids_in(client.get("/talepler/").get_data(as_text=True), world) == set()
    assert client.get(f"/talepler/{world['t_creator']}").status_code == 404


def test_assignee_with_update_can_advance_but_not_reject_or_reopen(app, client, world):
    with app.app_context():
        svc.assign_ticket(db.session.get(Ticket, world["t_root"]), db.session.get(User, world["worker"]),
                          db.session.get(User, world["root"]))
        ticket = db.session.get(Ticket, world["t_root"])
        assert svc.allowed_transitions(ticket, db.session.get(User, world["worker"])) == ["reviewing", "in_progress"]
    login(client, "isci")
    page = client.get(f"/talepler/{world['t_root']}/durum").get_data(as_text=True)
    assert 'value="rejected"' not in page
    client.post(f"/talepler/{world['t_root']}/durum", data={"status": "rejected", "note": "ret gerekçesi"})
    assert get_ticket(app, world["t_root"]).status == "new"
    assert client.post(f"/talepler/{world['t_root']}/durum", data={"status": "in_progress"}).status_code == 302
    assert get_ticket(app, world["t_root"]).status == "in_progress"


def test_update_permission_is_useless_on_tickets_not_assigned_to_the_user(app, client, world):
    login(client, "isci")
    # isci hiçbir talebe atanmamış: kendi açtığı olmadığı için görmez bile
    assert client.get(f"/talepler/{world['t_root']}/durum").status_code == 404


def test_manage_permission_allows_reject_and_reopen_on_visible_tickets(app, client, world):
    make_profile(app, "yonetici2", ROLE_BIRIM_SORUMLUSU, {"tickets.view_all", "tickets.manage"})
    login(client, "yonetici2")
    assert client.post(f"/talepler/{world['t_root']}/durum",
                       data={"status": "rejected", "note": "Kapsam dışı"}).status_code == 302
    assert get_ticket(app, world["t_root"]).status == "rejected"
    assert client.post(f"/talepler/{world['t_root']}/durum",
                       data={"status": "reviewing", "note": "Yeniden açıldı"}).status_code == 302
    assert get_ticket(app, world["t_root"]).status == "reviewing"


def test_manage_without_visibility_cannot_touch_tickets(app, world):
    actor_id = make_profile(app, "gorunmez", ROLE_BIRIM_SORUMLUSU, {"tickets.manage"})
    with app.app_context():
        actor, ticket = db.session.get(User, actor_id), db.session.get(Ticket, world["t_root"])
        assert svc.allowed_transitions(ticket, actor) == []
        with pytest.raises(svc.TicketPermissionError):
            svc.change_status(ticket, actor, "rejected", "not")
    assert get_ticket(app, world["t_root"]).status == "new"


def test_assignment_requires_assign_permission_and_a_capable_assignee(app, client, world):
    make_profile(app, "atayan", ROLE_BIRIM_SORUMLUSU, {"tickets.view_all", "tickets.assign"})
    login(client, "atayan")
    page = client.get(f"/talepler/{world['t_root']}/ata").get_data(as_text=True)
    assert "isci" in page and "acan" not in page.split("<select", 1)[1].split("</select>", 1)[0]  # update izni olmayan listelenmez
    # Doğrudan istekle update izni olmayan birine atama reddedilir
    client.post(f"/talepler/{world['t_root']}/ata", data={"assignee_id": world["creator"]})
    assert get_ticket(app, world["t_root"]).assigned_to_id is None
    assert client.post(f"/talepler/{world['t_root']}/ata", data={"assignee_id": world["worker"]}).status_code == 302
    assert get_ticket(app, world["t_root"]).assigned_to_id == world["worker"]


def test_service_refuses_assignee_without_update_permission(app, world):
    with app.app_context():
        with pytest.raises(svc.TicketRuleError, match="işleme yetkisi"):
            svc.assign_ticket(db.session.get(Ticket, world["t_root"]), db.session.get(User, world["creator"]),
                              db.session.get(User, world["root"]))


def test_user_with_only_create_can_open_ticket_but_not_browse(app, client):
    make_profile(app, "bildiren", ROLE_BIRIM_SORUMLUSU, {"tickets.create"})
    login(client, "bildiren")
    assert client.get("/talepler/").status_code == 403
    response = client.post("/talepler/yeni", data={"title": "Işık yanmıyor", "description": "Koridor lambası",
                                                    "category": "other", "location": "Zemin kat", "priority": "normal"})
    assert response.status_code == 302 and "/talepler/" not in response.headers["Location"]  # detay 404 olurdu
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 1


def test_denied_ticket_actions_leave_no_audit_trail(app, client, world):
    login(client, "acan")
    before = audit_actions(app)
    client.post(f"/talepler/{world['t_creator']}/ata", data={"assignee_id": world["worker"]})
    client.post(f"/talepler/{world['t_creator']}/durum", data={"status": "reviewing"})
    assert audit_actions(app) == before


def test_closed_ticket_flow_still_requires_resolution_note_for_manage_users(app, client, world):
    make_profile(app, "yonetici2", ROLE_BIRIM_SORUMLUSU, {"tickets.view_all", "tickets.manage"})
    force_state(app, world["t_root"], "in_progress")
    login(client, "yonetici2")
    client.post(f"/talepler/{world['t_root']}/durum", data={"status": "resolved", "note": ""})
    assert get_ticket(app, world["t_root"]).status == "in_progress"  # iş kuralları izinle gevşemedi
