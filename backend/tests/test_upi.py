from urllib.parse import parse_qs, urlparse

import pytest

from app.services import payment_service, settings_service


def create_link(db, customer, admin):
    link = payment_service.ensure_payment_link(db, customer, actor=admin)
    return link, payment_service._link_out(link, customer)


def test_upi_button_renders_app_chooser_and_customer_enters_amount(client, db, customer, admin):
    link, payload = create_link(db, customer, admin)
    assert payload.provider == "UPI"
    assert "/pay/" in payload.url

    response = client.get(urlparse(payload.url).path)
    assert response.status_code == 200
    assert "upi://pay?" in response.text
    assert "Open UPI apps" in response.text
    assert "Enter the amount you wish to pay" in response.text

    query = parse_qs(urlparse(link.extra_data["upi_uri"]).query)
    assert query["pa"] == ["essvee@testbank"]
    assert query["pn"] == ["ESSVEE Test"]
    assert query["cu"] == ["INR"]
    assert "am" not in query  # customer can pay any amount


def test_upi_fixed_amount_mode_prefills_outstanding(db, customer, admin):
    settings_service.set_value(db, "upi_amount_mode", "FIXED_OUTSTANDING")
    link, _ = create_link(db, customer, admin)
    query = parse_qs(urlparse(link.extra_data["upi_uri"]).query)
    assert query["am"] == ["5000.00"]


def test_public_base_url_must_be_https_outside_localhost(db, customer, admin):
    settings_service.set_value(db, "public_base_url", "http://example.com")
    with pytest.raises(Exception, match="HTTPS"):
        payment_service.ensure_payment_link(db, customer, actor=admin)


def test_razorpay_mode_still_works(db, customer, admin):
    settings_service.set_value(db, "payment_provider", "RAZORPAY")
    _, payload = create_link(db, customer, admin)
    assert payload.provider == "RAZORPAY"
    assert payload.url.startswith("https://rzp.io/")


def test_upi_settings_are_reported_and_checkable(client):
    body = client.get("/api/settings").json()
    assert body["configured"]["upi"] is True
    assert body["upi"]["payment_provider"] == "UPI"
    assert body["upi"]["upi_amount_mode"] == "CUSTOMER_ENTERS"

    result = client.post("/api/settings/test/upi")
    assert result.status_code == 200
    assert result.json()["success"] is True
    assert result.json()["detail"]["vpa"] == "essvee@testbank"


def test_public_redirect_rejects_unknown_token(client):
    response = client.get("/pay/not-a-real-token-123456")
    assert response.status_code == 404
