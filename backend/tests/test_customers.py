from decimal import Decimal

from app.core.errors import ValidationError
from app.services import customer_service


def test_phone_normalisation_variants():
    assert customer_service.normalize_phone("9876543210") == "+919876543210"
    assert customer_service.normalize_phone("919876543210") == "+919876543210"
    assert customer_service.normalize_phone("+91 98765 43210") == "+919876543210"
    assert customer_service.normalize_phone("09876543210") == "+919876543210"
    assert customer_service.normalize_phone("00 91 9876543210") == "+919876543210"
    assert customer_service.normalize_phone("+442071838750") == "+442071838750"


def test_invalid_phone_detection():
    for value in ["12345", "abcdefghij", "", "12345678901234567890"]:
        try:
            customer_service.normalize_phone(value)
            assert False, f"Expected {value} to be rejected"
        except ValidationError:
            pass


def test_add_customer_via_api(client):
    response = client.post(
        "/api/customers",
        json={"name": "  Anil  Sharma ", "whatsapp_number": "9876543211", "opening_balance": 5000},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Anil Sharma"
    assert body["whatsapp_number"] == "+919876543211"
    assert Decimal(body["outstanding_balance"]) == Decimal("5000.00")


def test_duplicate_phone_detection(client, customer):
    response = client.post(
        "/api/customers",
        json={"name": "Duplicate", "whatsapp_number": "+919876543210"},
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["error"]["message"]


def test_invalid_phone_api_rejection(client):
    response = client.post(
        "/api/customers", json={"name": "Bad Number", "whatsapp_number": "12345"}
    )
    assert response.status_code in (400, 422)


def test_update_customer(client, customer):
    response = client.put(
        f"/api/customers/{customer.id}",
        json={"name": "Rahul Updated", "status": "INACTIVE", "exclude_from_reminders": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Rahul Updated"
    assert body["status"] == "INACTIVE"
    assert body["exclude_from_reminders"] is True


def test_search_by_name_and_phone(client, customer):
    assert client.get("/api/customers?search=Rahul").json()["total"] == 1
    assert client.get("/api/customers?search=9876543210").json()["total"] == 1
    assert client.get("/api/customers?search=missing").json()["total"] == 0


def test_consent_and_exclusion(client, customer):
    assert client.put(
        f"/api/customers/{customer.id}", json={"consent_status": "OPT_OUT"}
    ).json()["consent_status"] == "OPT_OUT"
    result = client.post(f"/api/customers/{customer.id}/toggle-exclusion").json()
    assert result["exclude_from_reminders"] is True
