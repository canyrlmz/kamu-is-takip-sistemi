import pytest

from app.extensions import db
from app.models.ticket import STATUS_LABELS, Ticket, TicketEvent
from app.models.user import User
from app.services import tickets as svc
from tests.conftest import login
from tests.ticket_helpers import (
    assign_via_service, audit_actions, audit_entries, events_of, force_state, get_ticket, make_ticket,
)

ALL_STATUSES = list(STATUS_LABELS)
VALID_PAIRS = [(a, b) for a, targets in svc.ADMIN_TRANSITIONS.items() for b in sorted(targets)]
INVALID_PAIRS = [(a, b) for a in ALL_STATUSES for b in ALL_STATUSES
                 if a != b and b not in svc.ADMIN_TRANSITIONS.get(a, set())]


def post_status(client, tid, status, note="Not metni"):
    return client.post(f"/talepler/{tid}/durum", data={"status": status, "note": note})


@pytest.fixture()
def ctx(app, client, admin_id, staff_id, make_user):
    other = make_user(app, username="digeri", full_name="Diğer Kişi")
    tid = make_ticket(app, staff_id)  # personel açtı
    return {"tid": tid, "admin": admin_id, "staff": staff_id, "other": other}


# --- Yönetici geçişleri --------------------------------------------------------

@pytest.mark.parametrize("source, target", VALID_PAIRS)
def test_admin_valid_transitions(app, client, ctx, source, target):
    tid = ctx["tid"]
    force_state(app, tid, source)
    before = len(events_of(app, tid))
    login(client, "yonetici")
    assert post_status(client, tid, target).status_code == 302
    ticket = get_ticket(app, tid)
    assert ticket.status == target
    assert (ticket.closed_at is not None) == (target in ("resolved", "rejected"))
    last = events_of(app, tid)[-1]
    assert len(events_of(app, tid)) == before + 1
    assert last["type"] == "status_changed" and last["from"] == source and last["to"] == target
    assert last["actor"] == ctx["admin"] and last["note"] == "Not metni"


@pytest.mark.parametrize("source, target", INVALID_PAIRS)
def test_admin_invalid_transitions_rejected(app, client, ctx, source, target):
    tid = ctx["tid"]
    force_state(app, tid, source)
    login(client, "yonetici")
    response = post_status(client, tid, target)
    # Geçersiz hedef seçenek olarak sunulmadığı için form doğrulaması reddeder; durum değişmez.
    assert response.status_code == 200 and "Geçerli bir durum seçin" in response.get_data(as_text=True)
    assert get_ticket(app, tid).status == source
    assert [e["type"] for e in events_of(app, tid)] == ["created"]


@pytest.mark.parametrize("bad", ["bogus", "", "in_use", "NEW", "<script>"])
def test_invalid_status_values_rejected(app, client, ctx, bad):
    login(client, "yonetici")
    response = post_status(client, ctx["tid"], bad)
    assert response.status_code == 200 and "Geçerli bir durum seçin" in response.get_data(as_text=True)
    assert get_ticket(app, ctx["tid"]).status == "new"


@pytest.mark.parametrize("source, target", INVALID_PAIRS)
def test_service_rejects_invalid_transitions(app, ctx, source, target):
    tid = ctx["tid"]
    force_state(app, tid, source)
    with app.app_context():
        admin = db.session.get(User, ctx["admin"])
        with pytest.raises(svc.TicketRuleError, match="geçilemez"):
            svc.change_status(db.session.get(Ticket, tid), admin, target, "Not")
        assert db.session.get(Ticket, tid).status == source


def test_service_rejects_unknown_and_same_status(app, ctx):
    with app.app_context():
        admin, ticket = db.session.get(User, ctx["admin"]), db.session.get(Ticket, ctx["tid"])
        with pytest.raises(svc.TicketRuleError, match="Geçersiz durum"):
            svc.change_status(ticket, admin, "bogus", "Not")
        with pytest.raises(svc.TicketRuleError, match="zaten bu durumda"):
            svc.change_status(ticket, admin, "new", "Not")


# --- Not zorunluluğu ve kapanış alanları ---------------------------------------

@pytest.mark.parametrize("source, target", [("reviewing", "resolved"), ("new", "rejected"), ("resolved", "in_progress"),
                                            ("rejected", "reviewing")])
def test_note_required_for_close_reject_and_reopen(app, client, ctx, source, target):
    tid = ctx["tid"]
    force_state(app, tid, source)
    login(client, "yonetici")
    for blank in ("", "   "):
        response = post_status(client, tid, target, note=blank)
        assert response.status_code == 200 and "zorunludur" in response.get_data(as_text=True)
    assert get_ticket(app, tid).status == source
    assert post_status(client, tid, target, note="Gerekçe").status_code == 302


def test_note_optional_for_normal_progress(app, client, ctx):
    login(client, "yonetici")
    assert post_status(client, ctx["tid"], "reviewing", note="").status_code == 302
    assert events_of(app, ctx["tid"])[-1]["note"] is None


def test_note_length_limit(app, client, ctx):
    login(client, "yonetici")
    response = post_status(client, ctx["tid"], "reviewing", note="x" * 501)
    assert response.status_code == 200 and "En fazla 500" in response.get_data(as_text=True)
    assert get_ticket(app, ctx["tid"]).status == "new"


def test_close_reopen_cycle_preserves_history(app, client, ctx):
    tid = ctx["tid"]
    login(client, "yonetici")
    post_status(client, tid, "in_progress", note="")
    post_status(client, tid, "resolved", note="Kablo değiştirildi")
    ticket = get_ticket(app, tid)
    assert ticket.status == "resolved" and ticket.closed_at is not None and ticket.resolution_note == "Kablo değiştirildi"
    post_status(client, tid, "in_progress", note="Sorun tekrarladı")
    ticket = get_ticket(app, tid)
    assert ticket.status == "in_progress" and ticket.closed_at is None and ticket.resolution_note is None
    post_status(client, tid, "resolved", note="Kalıcı çözüm")
    assert get_ticket(app, tid).resolution_note == "Kalıcı çözüm"
    history = events_of(app, tid)
    assert [e["to"] for e in history] == ["new", "in_progress", "resolved", "in_progress", "resolved"]
    assert [e["note"] for e in history][2:] == ["Kablo değiştirildi", "Sorun tekrarladı", "Kalıcı çözüm"]  # eski kapanış korunur


def test_rejection_shows_reason_on_detail(app, client, ctx):
    login(client, "yonetici")
    post_status(client, ctx["tid"], "rejected", note="Kapsam dışı talep")
    html = client.get(f"/talepler/{ctx['tid']}").get_data(as_text=True)
    assert "Ret gerekçesi" in html and "Kapsam dışı talep" in html


# --- Atanan personel -----------------------------------------------------------

def test_assignee_can_progress_own_assigned_ticket(app, client, ctx):
    tid = ctx["tid"]
    assign_via_service(app, tid, ctx["other"], ctx["admin"])
    login(client, "digeri")
    assert post_status(client, tid, "reviewing", note="").status_code == 302
    assert post_status(client, tid, "in_progress", note="").status_code == 302
    assert post_status(client, tid, "resolved", note="Çözüldü").status_code == 302
    ticket = get_ticket(app, tid)
    assert ticket.status == "resolved" and ticket.closed_at is not None
    assert [e["actor"] for e in events_of(app, tid)[-3:]] == [ctx["other"]] * 3


def test_assignee_cannot_reject(app, client, ctx):
    tid = ctx["tid"]
    assign_via_service(app, tid, ctx["other"], ctx["admin"])
    login(client, "digeri")
    response = post_status(client, tid, "rejected", note="Red")
    assert response.status_code == 200 and "Geçerli bir durum seçin" in response.get_data(as_text=True)
    assert get_ticket(app, tid).status == "new"
    with app.app_context():
        with pytest.raises(svc.TicketPermissionError):
            svc.change_status(db.session.get(Ticket, tid), db.session.get(User, ctx["other"]), "rejected", "Red")


def test_assignee_cannot_reopen_closed_ticket(app, client, ctx):
    tid = ctx["tid"]
    force_state(app, tid, "resolved", assignee_id=ctx["other"])
    login(client, "digeri")
    assert client.get(f"/talepler/{tid}/durum").status_code == 403
    assert post_status(client, tid, "in_progress", note="Tekrar").status_code == 403
    assert get_ticket(app, tid).status == "resolved"
    assert "Durumu değiştir" not in client.get(f"/talepler/{tid}").get_data(as_text=True)
    with app.app_context():
        with pytest.raises(svc.TicketPermissionError):
            svc.change_status(db.session.get(Ticket, tid), db.session.get(User, ctx["other"]), "in_progress", "Tekrar")


def test_creator_who_is_not_assignee_cannot_change_status(app, client, ctx):
    tid = ctx["tid"]
    login(client, "personel")  # talebi açan ama atanan değil
    assert client.get(f"/talepler/{tid}/durum").status_code == 403
    assert post_status(client, tid, "reviewing", note="x").status_code == 403
    assert get_ticket(app, tid).status == "new"
    assert "Durumu değiştir" not in client.get(f"/talepler/{tid}").get_data(as_text=True)
    with app.app_context():
        with pytest.raises(svc.TicketPermissionError):
            svc.change_status(db.session.get(Ticket, tid), db.session.get(User, ctx["staff"]), "reviewing", None)


def test_unrelated_staff_gets_404_not_403(app, client, ctx):
    login(client, "digeri")
    assert client.get(f"/talepler/{ctx['tid']}/durum").status_code == 404
    assert post_status(client, ctx["tid"], "reviewing").status_code == 404
    assert get_ticket(app, ctx["tid"]).status == "new"


# --- Sorumlu atama -------------------------------------------------------------

def test_staff_cannot_open_or_post_assignment(app, client, ctx):
    tid = ctx["tid"]
    login(client, "personel")  # hem talebi açan hem personel
    assert client.get(f"/talepler/{tid}/ata").status_code == 403
    assert client.post(f"/talepler/{tid}/ata", data={"assignee_id": ctx["staff"]}).status_code == 403
    assert client.post("/talepler/99999/ata", data={"assignee_id": ctx["staff"]}).status_code == 403  # varlık sızmaz
    assert get_ticket(app, tid).assigned_to_id is None
    assert "Sorumlu ata" not in client.get(f"/talepler/{tid}").get_data(as_text=True)


def test_admin_assigns_then_reassigns(app, client, ctx):
    tid, admin, staff, other = ctx["tid"], ctx["admin"], ctx["staff"], ctx["other"]
    login(client, "yonetici")
    assert client.post(f"/talepler/{tid}/ata", data={"assignee_id": other, "note": "Ağ uzmanı"}).status_code == 302
    assert get_ticket(app, tid).assigned_to_id == other
    assert client.post(f"/talepler/{tid}/ata", data={"assignee_id": staff}).status_code == 302
    assert get_ticket(app, tid).assigned_to_id == staff
    history = events_of(app, tid)[1:]
    assert history[0] == {"type": "assigned", "from": None, "to": None, "note": "Ağ uzmanı", "actor": admin,
                          "from_assignee": None, "to_assignee": other}
    assert history[1]["type"] == "reassigned" and history[1]["from_assignee"] == other and history[1]["to_assignee"] == staff
    assert audit_actions(app).count("ticket_assigned") == 1 and audit_actions(app).count("ticket_reassigned") == 1
    (assigned,) = audit_entries(app, "ticket_assigned")
    assert assigned["user_id"] == admin and assigned["entity_id"] == tid and "digeri" in assigned["details"]
    (reassigned,) = audit_entries(app, "ticket_reassigned")
    assert "digeri → personel" in reassigned["details"]


def test_assignment_form_lists_only_active_users(app, client, ctx, make_user):
    make_user(app, username="pasif", full_name="Pasif Kişi", active=False)
    login(client, "yonetici")
    html = client.get(f"/talepler/{ctx['tid']}/ata").get_data(as_text=True)
    assert "Pasif Kişi" not in html and "Diğer Kişi" in html and "Yönetici Örnek" in html


@pytest.mark.parametrize("bad", ["inactive", 99999, "abc", ""])
def test_invalid_assignee_rejected(app, client, ctx, make_user, bad):
    inactive = make_user(app, username="pasif", full_name="Pasif Kişi", active=False)
    login(client, "yonetici")
    value = inactive if bad == "inactive" else bad
    response = client.post(f"/talepler/{ctx['tid']}/ata", data={"assignee_id": value})
    assert response.status_code == 200 and "etkin bir kullanıcı seçin" in response.get_data(as_text=True)
    assert get_ticket(app, ctx["tid"]).assigned_to_id is None
    assert "ticket_assigned" not in audit_actions(app)


def test_service_rejects_inactive_assignee_even_if_form_is_bypassed(app, ctx, make_user):
    inactive = make_user(app, username="pasif", active=False)
    with app.app_context():
        admin, ticket = db.session.get(User, ctx["admin"]), db.session.get(Ticket, ctx["tid"])
        with pytest.raises(svc.TicketRuleError, match="etkin bir kullanıcı"):
            svc.assign_ticket(ticket, db.session.get(User, inactive), admin)
        with pytest.raises(svc.TicketRuleError, match="etkin bir kullanıcı"):
            svc.assign_ticket(ticket, None, admin)
        with pytest.raises(svc.TicketPermissionError):
            svc.assign_ticket(ticket, admin, db.session.get(User, ctx["staff"]))


def test_same_assignee_and_closed_ticket_rejected(app, client, ctx):
    tid = ctx["tid"]
    login(client, "yonetici")
    client.post(f"/talepler/{tid}/ata", data={"assignee_id": ctx["other"]})
    response = client.post(f"/talepler/{tid}/ata", data={"assignee_id": ctx["other"]})
    assert "zaten bu kullanıcıya atanmış" in response.get_data(as_text=True)
    force_state(app, tid, "resolved")
    response = client.post(f"/talepler/{tid}/ata", data={"assignee_id": ctx["staff"]})
    assert "Kapalı talebe" in response.get_data(as_text=True)
    assert get_ticket(app, tid).assigned_to_id == ctx["other"]
    assert len(events_of(app, tid)) == 2  # oluşturma + ilk atama


def test_visibility_follows_assignment(app, client, ctx):
    tid = ctx["tid"]
    other_client = app.test_client()
    login(other_client, "digeri")
    assert other_client.get(f"/talepler/{tid}").status_code == 404
    assign_via_service(app, tid, ctx["other"], ctx["admin"])
    assert other_client.get(f"/talepler/{tid}").status_code == 200
    assign_via_service(app, tid, ctx["admin"], ctx["admin"])  # yeniden atama: eski sorumlu erişimini kaybeder
    assert other_client.get(f"/talepler/{tid}").status_code == 404
    login(client, "personel")
    assert client.get(f"/talepler/{tid}").status_code == 200  # talebi açan hep görür


# --- Geçmiş ve denetim ---------------------------------------------------------

def test_history_page_shows_actions_actors_and_notes(app, client, ctx):
    tid = ctx["tid"]
    login(client, "yonetici")
    client.post(f"/talepler/{tid}/ata", data={"assignee_id": ctx["other"], "note": "Uzmana yönlendirildi"})
    post_status(client, tid, "in_progress", note="İşe başlandı")
    html = client.get(f"/talepler/{tid}").get_data(as_text=True)
    for text in ("Talep oluşturuldu", "Sorumlu atandı", "Diğer Kişi", "Uzmana yönlendirildi", "İşe başlandı",
                 "Yönetici Örnek", "Personel Örnek", "İşlemde"):
        assert text in html
    client.post("/cikis")
    login(client, "personel")  # talebi açan personel de geçmişi görür
    assert "İşe başlandı" in client.get(f"/talepler/{tid}").get_data(as_text=True)


def test_status_change_is_written_to_audit_log_without_note_text(app, client, ctx):
    login(client, "yonetici")
    post_status(client, ctx["tid"], "reviewing", note="Gizli ayrıntı notu")
    (entry,) = audit_entries(app, "ticket_status_changed")
    assert entry["user_id"] == ctx["admin"] and entry["entity_type"] == "ticket" and entry["entity_id"] == ctx["tid"]
    assert "Yeni → İnceleniyor" in entry["details"] and "Gizli ayrıntı notu" not in entry["details"]


def test_failed_actions_leave_no_history_or_audit(app, client, ctx):
    login(client, "yonetici")
    post_status(client, ctx["tid"], "resolved", note="")  # kapsam dışı geçiş + not yok
    client.post(f"/talepler/{ctx['tid']}/ata", data={"assignee_id": 99999})
    assert [e["type"] for e in events_of(app, ctx["tid"])] == ["created"]
    assert audit_actions(app) == ["ticket_created", "login_success"]  # talep girişten önce fixture ile açıldı


def test_history_rows_cannot_be_updated_or_deleted(app, ctx):
    with app.app_context():
        event = db.session.scalars(db.select(TicketEvent)).first()
        event.note = "sessizce değiştirildi"
        with pytest.raises(RuntimeError, match="değiştirilemez"):
            db.session.commit()
        db.session.rollback()
        db.session.delete(db.session.scalars(db.select(TicketEvent)).first())
        with pytest.raises(RuntimeError, match="silinemez"):
            db.session.commit()
        db.session.rollback()
        assert db.session.scalar(db.select(db.func.count()).select_from(TicketEvent)) == 1


# --- Veritabanı kısıtları ------------------------------------------------------

@pytest.mark.parametrize("changes", [
    {"status": "bogus"}, {"priority": "kritik"}, {"category": "bogus"},
    {"status": "resolved"},              # kapanış tarihi olmadan kapalı durum
])
def test_database_constraints_reject_bad_rows(app, ctx, changes):
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        ticket = db.session.get(Ticket, ctx["tid"])
        for key, value in changes.items():
            setattr(ticket, key, value)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_database_rejects_closed_at_on_open_ticket(app, ctx):
    from sqlalchemy.exc import IntegrityError

    from app.utils import utcnow

    with app.app_context():
        db.session.get(Ticket, ctx["tid"]).closed_at = utcnow()
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_service_create_validation(app, staff_id):
    with app.app_context():
        user = db.session.get(User, staff_id)
        for kwargs in ({"category": "bogus"}, {"priority": "bogus"}, {"title": " "}, {"location": ""}):
            args = {"title": "T", "description": "D", "category": "printer", "location": "L", "priority": "normal"}
            args.update(kwargs)
            with pytest.raises(svc.TicketRuleError):
                svc.create_ticket(user, **args)
        assert db.session.scalar(db.select(db.func.count()).select_from(Ticket)) == 0
