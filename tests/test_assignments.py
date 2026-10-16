from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.asset import Asset, AssetAssignment
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services import assets as svc
from tests.conftest import iso, local_today, login


def assign(client, asset_id, assignee_id, start, note="Teslim edildi"):
    return client.post(f"/demirbaslar/{asset_id}/zimmet-ver",
                       data={"assignee_id": assignee_id, "start_date": iso(start), "note": note})


def give_back(client, asset_id, when, status_after="in_storage", note="İade edildi"):
    return client.post(f"/demirbaslar/{asset_id}/iade-al",
                       data={"return_date": iso(when), "status_after": status_after, "note": note})


def rows(app, asset_id):
    with app.app_context():
        return db.session.scalars(
            db.select(AssetAssignment).where(AssetAssignment.asset_id == asset_id).order_by(AssetAssignment.id)
        ).all()


def asset_status(app, asset_id):
    with app.app_context():
        return db.session.get(Asset, asset_id).status


def audit_by_action(app, action):
    with app.app_context():
        return db.session.scalars(db.select(AuditLog).where(AuditLog.action == action)).all()


@pytest.fixture()
def setup(app, client, admin_id, staff_id, make_asset):
    login(client, "yonetici")
    return {"asset": make_asset(app, code="ZMT-001"), "today": local_today(app)}


def test_assign_creates_active_assignment_and_marks_in_use(app, client, admin_id, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    response = assign(client, aid, staff_id, today - timedelta(days=2), note="Kutusuyla teslim")
    assert response.status_code == 302
    (a,) = rows(app, aid)
    assert a.assignee_id == staff_id and a.assigned_by_id == admin_id
    assert a.start_date == today - timedelta(days=2) and a.return_date is None and a.assign_note == "Kutusuyla teslim"
    assert asset_status(app, aid) == "in_use"
    entry = audit_by_action(app, "asset_assigned")[0]
    assert entry.user_id == admin_id and entry.entity_type == "asset_assignment" and entry.entity_id == a.id
    assert "ZMT-001" in entry.details and "personel" in entry.details
    html = client.get(f"/demirbaslar/{aid}").get_data(as_text=True)
    assert "Personel Örnek" in html and "Devam ediyor" in html and "Kutusuyla teslim" in html


def test_second_active_assignment_is_blocked(app, client, admin_id, staff_id, make_user, setup):
    aid, today = setup["asset"], setup["today"]
    other = make_user(app, username="digeri", full_name="Diğer Kişi")
    assign(client, aid, staff_id, today)
    response = assign(client, aid, other, today)
    assert response.status_code == 200  # form yeniden gösterilir
    assert len(rows(app, aid)) == 1 and rows(app, aid)[0].assignee_id == staff_id
    # zimmetli demirbaş için zimmet verme ekranı açılmaz
    assert "Zimmet ver" not in client.get(f"/demirbaslar/{aid}").get_data(as_text=True)


def test_service_rejects_double_assignment(app, admin_id, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    with app.app_context():
        asset, user, admin = db.session.get(Asset, aid), db.session.get(User, staff_id), db.session.get(User, admin_id)
        svc.assign_asset(asset, user, today, None, admin)
        with pytest.raises(svc.AssetRuleError, match="zaten zimmetli"):
            svc.assign_asset(asset, user, today, None, admin)


def test_database_itself_rejects_two_active_assignments(app, admin_id, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    with app.app_context():
        for _ in range(2):
            db.session.add(AssetAssignment(asset_id=aid, assignee_id=staff_id, assigned_by_id=admin_id, start_date=today))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_race_condition_is_handled_by_database_constraint(app, admin_id, staff_id, setup, monkeypatch):
    aid, today = setup["asset"], setup["today"]
    with app.app_context():
        db.session.add(AssetAssignment(asset_id=aid, assignee_id=staff_id, assigned_by_id=admin_id, start_date=today))
        db.session.commit()
        # Kural kontrolünü atlatan eş zamanlı istek simülasyonu: veritabanı kısıtı devreye girmeli.
        monkeypatch.setattr(svc, "get_active_assignment", lambda asset_id: None)
        asset, user, admin = db.session.get(Asset, aid), db.session.get(User, staff_id), db.session.get(User, admin_id)
        with pytest.raises(svc.AssetRuleError, match="zaten zimmetli"):
            svc.assign_asset(asset, user, today, None, admin)
        assert db.session.get(Asset, aid).status == "in_storage"  # geri alındı
    assert len(rows(app, aid)) == 1


@pytest.mark.parametrize("status", ["maintenance", "scrap_candidate"])
def test_only_assets_in_storage_can_be_assigned(app, client, admin_id, staff_id, make_asset, status):
    login(client, "yonetici")
    aid = make_asset(app, code="ZMT-ST", status=status)
    response = assign(client, aid, staff_id, local_today(app))
    assert "durumundaki demirbaş zimmetlenebilir" in response.get_data(as_text=True)
    assert rows(app, aid) == []


def test_assignee_must_be_an_active_listed_user(app, client, admin_id, make_user, setup):
    aid, today = setup["asset"], setup["today"]
    passive = make_user(app, username="pasif", active=False)
    html = client.get(f"/demirbaslar/{aid}/zimmet-ver").get_data(as_text=True)
    assert "pasif" not in html and "Yönetici Örnek" in html
    for bad in (passive, 99999, "abc", ""):
        response = assign(client, aid, bad, today)
        assert response.status_code == 200 and "Listeden bir personel seçin" in response.get_data(as_text=True)
    assert rows(app, aid) == []


def test_assignment_date_rules(app, client, admin_id, staff_id, make_asset):
    login(client, "yonetici")
    today = local_today(app)
    aid = make_asset(app, code="ZMT-DT", acquisition_date=today - timedelta(days=10))
    assert "gelecekte olamaz" in assign(client, aid, staff_id, today + timedelta(days=1)).get_data(as_text=True)
    assert "edinim tarihinden önce" in assign(client, aid, staff_id, today - timedelta(days=11)).get_data(as_text=True)
    bad = client.post(f"/demirbaslar/{aid}/zimmet-ver", data={"assignee_id": staff_id, "start_date": "yanlis"})
    assert "Geçerli bir tarih girin" in bad.get_data(as_text=True)
    assert rows(app, aid) == []
    assert assign(client, aid, staff_id, today - timedelta(days=10)).status_code == 302


def test_return_keeps_history_and_updates_asset(app, client, admin_id, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    assign(client, aid, staff_id, today - timedelta(days=5))
    response = give_back(client, aid, today - timedelta(days=1), status_after="maintenance", note="Ekran kırık")
    assert response.status_code == 302
    (a,) = rows(app, aid)  # satır silinmedi
    assert a.return_date == today - timedelta(days=1) and a.return_note == "Ekran kırık"
    assert a.returned_by_id == admin_id and a.returned_at is not None and a.assign_note == "Teslim edildi"
    assert asset_status(app, aid) == "maintenance"
    entry = audit_by_action(app, "asset_returned")[0]
    assert entry.user_id == admin_id and entry.entity_id == a.id


def test_history_lists_everyone_the_asset_was_given_to(app, client, staff_id, make_user, setup):
    aid, today = setup["asset"], setup["today"]
    second = make_user(app, username="ikinci", full_name="İkinci Kişi")
    assign(client, aid, staff_id, today - timedelta(days=30))
    give_back(client, aid, today - timedelta(days=20))
    assert asset_status(app, aid) == "in_storage"
    assign(client, aid, second, today - timedelta(days=10), note="İkinci teslim")
    give_back(client, aid, today - timedelta(days=3), note="İkinci iade")
    assert assign(client, aid, staff_id, today - timedelta(days=2)).status_code == 302  # yeniden zimmetlenebilir
    assert len(rows(app, aid)) == 3
    html = client.get(f"/demirbaslar/{aid}").get_data(as_text=True)
    for text in ("Personel Örnek", "İkinci Kişi", "İkinci teslim", "İkinci iade", "Devam ediyor"):
        assert text in html
    assert html.count("Personel Örnek") >= 2  # aynı kişi iki ayrı satırda


def test_history_survives_assignee_deactivation(app, client, admin_id, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    assign(client, aid, staff_id, today - timedelta(days=4))
    client.post(f"/yonetim/kullanicilar/{staff_id}/devre-disi")
    give_back(client, aid, today)  # devre dışı kullanıcıdan da iade alınabilir
    assert "Personel Örnek" in client.get(f"/demirbaslar/{aid}").get_data(as_text=True)


def test_return_validation(app, client, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    assign(client, aid, staff_id, today - timedelta(days=5))
    assert "başlangıç tarihinden önce" in give_back(client, aid, today - timedelta(days=6)).get_data(as_text=True)
    assert "gelecekte olamaz" in give_back(client, aid, today + timedelta(days=1)).get_data(as_text=True)
    assert "Geçerli bir durum" in give_back(client, aid, today, status_after="in_use").get_data(as_text=True)
    assert "Geçerli bir durum" in give_back(client, aid, today, status_after="bogus").get_data(as_text=True)
    assert rows(app, aid)[0].return_date is None and asset_status(app, aid) == "in_use"


def test_return_without_active_assignment(app, client, admin_id, setup):
    aid = setup["asset"]
    assert client.get(f"/demirbaslar/{aid}/iade-al").status_code == 302
    assert give_back(client, aid, setup["today"]).status_code == 302
    assert audit_by_action(app, "asset_returned") == []
    with app.app_context():
        with pytest.raises(svc.AssetRuleError, match="aktif bir zimmeti yok"):
            svc.return_asset(db.session.get(Asset, aid), setup["today"], "in_storage", None, db.session.get(User, admin_id))


def test_return_cannot_be_recorded_twice(app, client, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    assign(client, aid, staff_id, today - timedelta(days=3))
    give_back(client, aid, today - timedelta(days=1))
    give_back(client, aid, today)  # ikinci iade denemesi
    assert rows(app, aid)[0].return_date == today - timedelta(days=1)
    assert len(audit_by_action(app, "asset_returned")) == 1


def test_new_assignment_cannot_start_before_previous_return(app, client, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    assign(client, aid, staff_id, today - timedelta(days=10))
    give_back(client, aid, today - timedelta(days=2))
    response = assign(client, aid, staff_id, today - timedelta(days=5))
    assert "önceki zimmetin iade tarihinden önce" in response.get_data(as_text=True)
    assert len(rows(app, aid)) == 1


def test_assigned_asset_cannot_be_archived_or_have_status_edited(app, client, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    assign(client, aid, staff_id, today)
    client.post(f"/demirbaslar/{aid}/arsivle")
    with app.app_context():
        assert db.session.get(Asset, aid).archived is False
    client.post(f"/demirbaslar/{aid}/duzenle", data={"code": "ZMT-001", "name": "Örnek Bilgisayar",
                                                      "category": "Bilgisayar", "status": "maintenance"})
    assert asset_status(app, aid) == "in_use"  # durum alanı istekle değiştirilemez
    html = client.get(f"/demirbaslar/{aid}/duzenle").get_data(as_text=True)
    assert "Zimmetli demirbaşın durumu değiştirilemez" in html


def test_assignment_actions_require_post_and_csrf_free_gets_change_nothing(app, client, staff_id, setup):
    aid, today = setup["asset"], setup["today"]
    assert client.get(f"/demirbaslar/{aid}/arsivle").status_code == 405
    assert client.get(f"/demirbaslar/{aid}/geri-al").status_code == 405
    client.get(f"/demirbaslar/{aid}/zimmet-ver")
    assert rows(app, aid) == []


def test_empty_dates_are_reported_as_required(app, client, staff_id, setup):
    aid = setup["asset"]
    response = client.post(f"/demirbaslar/{aid}/zimmet-ver", data={"assignee_id": staff_id, "start_date": ""})
    assert "Başlangıç tarihi zorunludur" in response.get_data(as_text=True)
    assert rows(app, aid) == []
