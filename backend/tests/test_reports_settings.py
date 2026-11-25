from decimal import Decimal

from app.core.security import mask_secret
from app.models import AuditLog
from app.schemas import ManualPaymentCreate
from app.services import payment_service, settings_service


def test_settings_credentials_are_masked_and_masked_values_are_unchanged(client, db):
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"]["whatsapp"] is True
    assert body["configured"]["razorpay"] is True
    assert body["whatsapp"]["whatsapp_access_token"] == mask_secret("test_token")
    assert "test_token" not in str(body)

    updated = client.put(
        "/api/settings/whatsapp",
        json={"whatsapp_access_token": body["whatsapp"]["whatsapp_access_token"], "company_name": "Ignored"},
    )
    assert updated.status_code == 200
    assert settings_service.get(db, "whatsapp_access_token") == "test_token"


def test_setting_new_secret_replaces_old_value(client, db):
    response = client.put(
        "/api/settings/whatsapp", json={"whatsapp_access_token": "brand-new-token"}
    )
    assert response.status_code == 200
    assert settings_service.get(db, "whatsapp_access_token") == "brand-new-token"


def test_clear_credential_updates_configuration_flag(client, db):
    response = client.post("/api/settings/clear/razorpay_key_secret")
    assert response.status_code == 200
    body = client.get("/api/settings").json()
    assert body["configured"]["razorpay"] is False
    assert body["razorpay"]["razorpay_key_secret"] == ""


def test_invalid_credential_key_rejected(client):
    response = client.post("/api/settings/clear/company_name")
    assert response.status_code == 422


def test_health_endpoints(client):
    root = client.get("/health")
    assert root.status_code == 200
    assert root.json()["status"] == "ok"
    api = client.get("/api/health")
    assert api.status_code == 200
    assert api.json()["database"] == "ok"
    assert api.json()["razorpay_mode"] in ("test", "live")


def test_dashboard_and_reports_reflect_confirmed_payments_only(client, db, customer, admin):
    pending = payment_service.create_manual(
        db,
        ManualPaymentCreate(customer_id=customer.id, amount=1500),
        actor=admin,
    )
    before = client.get("/api/reports/dashboard").json()
    assert Decimal(before["stats"]["total_collected"]) == Decimal("0.00")
    assert Decimal(before["stats"]["pending_amount"]) == Decimal("1500.00")

    payment_service.confirm(db, pending.id, actor=admin, send_confirmation=False)
    after = client.get("/api/reports/dashboard").json()
    assert Decimal(after["stats"]["total_collected"]) == Decimal("1500.00")
    assert Decimal(after["stats"]["pending_amount"]) == Decimal("0.00")
    assert after["stats"]["customers_with_outstanding"] == 1

    collections = client.get("/api/reports/collections").json()
    assert collections["count"] == 1
    assert Decimal(collections["rows"][0]["Amount"]) == Decimal("1500.00")

    outstanding = client.get("/api/reports/outstanding").json()
    assert Decimal(outstanding["rows"][0]["Outstanding Balance"]) == Decimal("3500.00")


def test_report_exports(client, customer):
    for fmt, magic in (("xlsx", b"PK"), ("csv", b"Amount")):
        response = client.post("/api/reports/export", json={"report": "collections", "format": fmt})
        assert response.status_code == 200
        assert magic in response.content[:200] if fmt == "xlsx" else magic in response.content


def test_audit_log_is_attached_to_financial_actions(client, db, customer, admin):
    payment = payment_service.create_manual(
        db,
        ManualPaymentCreate(customer_id=customer.id, amount=500),
        actor=admin,
    )
    payment_service.confirm(db, payment.id, actor=admin, send_confirmation=False)
    logs = client.get("/api/audit-logs").json()
    actions = {entry["action"] for entry in logs}
    assert {"payment.manual_entry", "payment.confirm"}.issubset(actions)
    entries = client.get("/api/audit-logs?limit=500").json()
    assert any(item["entity_id"] == str(payment.id) for item in entries)
