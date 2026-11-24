from urllib.parse import parse_qs, urlparse
from uuid import UUID

from app.models import PaymentLink
from app.services import settings_service


def create_link(client, customer):
    response = client.post(
        "/api/payment-links/generate", json={"customer_id": str(customer.id)}
    )
    assert response.status_code == 201, response.text
    return response.json()


def uri_for(db, link_id):
    link = db.get(PaymentLink, UUID(link_id))
    return link.extra_data["upi_uri"]


def test_upi_button_renders_app_chooser_and_customer_enters_amount(client, db, customer):
    link = create_link(client, customer)
    assert link["provider"] == "UPI"
    assert "/pay/" in link["url"]

    response = client.get(urlparse(link["url"]).path)
    assert response.status_code == 200
    assert "upi://pay?" in response.text
    assert "Open UPI apps" in response.text
    assert "Enter the amount you wish to pay" in response.text

    query = parse_qs(urlparse(uri_for(db, link["id"])).query)
    assert query["pa"] == ["essvee@testbank"]
    assert query["pn"] == ["ESSVEE Test"]
    assert query["cu"] == ["INR"]
    assert "am" not in query  # customer can pay any amount


def test_upi_fixed_amount_mode_prefills_outstanding(client, db, customer):
    settings_service.set_value(db, "upi_amount_mode", "FIXED_OUTSTANDING")
    link = create_link(client, customer)
    query = parse_qs(urlparse(uri_for(db, link["id"])).query)
    assert query["am"] == ["5000.00"]


def test_public_base_url_must_be_https_outside_localhost(client, db, customer):
    settings_service.set_value(db, "public_base_url", "http://example.com")
    response = client.post(
        "/api/payment-links/generate", json={"customer_id": str(customer.id)}
    )
    assert response.status_code == 503
    assert "HTTPS" in response.json()["error"]["message"]


def test_razorpay_mode_still_works(client, db, customer):
    settings_service.set_value(db, "payment_provider", "RAZORPAY")
    link = create_link(client, customer)
    assert link["provider"] == "RAZORPAY"
    assert link["url"].startswith("https://rzp.io/")


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
