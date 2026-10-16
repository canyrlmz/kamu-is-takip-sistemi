from tests.conftest import login


def test_staff_cannot_open_admin_pages(client, staff_id):
    login(client, "personel")
    assert client.get("/yonetim/denetim-kayitlari").status_code == 403


def test_admin_can_open_audit_log(client, admin_id):
    login(client, "yonetici")
    response = client.get("/yonetim/denetim-kayitlari")
    assert response.status_code == 200
    assert "Denetim kayıtları" in response.get_data(as_text=True)


def test_audit_log_pagination_handles_bad_page_numbers(client, admin_id):
    login(client, "yonetici")
    for value in ("abc", "-5", "9999"):
        assert client.get("/yonetim/denetim-kayitlari", query_string={"sayfa": value}).status_code == 200


def test_dashboard_hides_admin_content_from_staff(client, admin_id, staff_id):
    login(client, "personel")
    html = client.get("/").get_data(as_text=True)
    assert "Son denetim kayıtları" not in html
    assert "Denetim Kayıtları" not in html
    client.post("/cikis")
    login(client, "yonetici")
    html = client.get("/").get_data(as_text=True)
    assert "Son denetim kayıtları" in html
