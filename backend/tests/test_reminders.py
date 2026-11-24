from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models import CustomerStatus, SchedulerRun
from app.schemas import BulkPreviewRequest, ManualPaymentCreate
from app.services import customer_service, payment_service, reminder_service, settings_service


@pytest.fixture(autouse=True)
def no_quiet_hours(monkeypatch):
    """Tests control time-of-day explicitly; real quiet hours are tested separately."""
    monkeypatch.setattr(reminder_service, "is_quiet_hours", lambda db: False)


def add_customer(db, name, phone, balance=1000, **overrides):
    data = {"name": name, "whatsapp_number": phone, "opening_balance": balance}
    data.update(overrides)
    return customer_service.create(db, customer_service.CustomerCreate(**data))


def test_eligibility_reasons(db, customer):
    config = settings_service.get_reminder(db)
    config.first_reminder_after_days = 0
    db.commit()

    zero = add_customer(db, "Zero Balance", "9876543211", balance=0)
    opted_out = add_customer(db, "Opted Out", "9876543212", consent_status="OPT_OUT")
    inactive = add_customer(db, "Inactive", "9876543213", status=CustomerStatus.INACTIVE.value)
    excluded = add_customer(db, "Excluded", "9876543214", exclude_from_reminders=True)

    payment_service.create_manual(db, ManualPaymentCreate(customer_id=customer.id, amount=100))

    recent = add_customer(db, "Recently Reminded", "9876543215")
    recent.last_reminder_at = datetime.now(timezone.utc)
    recent.reminder_count = 1
    db.commit()

    result = reminder_service.eligible_customers(db)
    eligible_ids = {c.id for c in result["customers"]}
    assert customer.id not in eligible_ids  # awaiting confirmation
    assert zero.id not in eligible_ids
    assert opted_out.id not in eligible_ids
    assert inactive.id not in eligible_ids
    assert excluded.id not in eligible_ids
    assert recent.id not in eligible_ids

    reasons = result["reason_breakdown"]
    assert reasons["zero_balance"] >= 1
    assert reasons["no_consent"] >= 1
    assert reasons["inactive"] >= 1
    assert reasons["excluded"] >= 1
    assert reasons["awaiting_confirmation"] >= 1
    assert reasons["within_interval"] >= 1


def test_eligible_customer_included_when_clean(db, customer):
    config = settings_service.get_reminder(db)
    config.first_reminder_after_days = 0
    db.commit()
    result = reminder_service.eligible_customers(db)
    assert [c.id for c in result["customers"]] == [customer.id]
    assert result["reason_breakdown"]["eligible"] == 1


def test_bulk_preview_token_required(db, customer):
    config = settings_service.get_reminder(db)
    config.first_reminder_after_days = 0
    db.commit()
    preview = reminder_service.preview(db, BulkPreviewRequest())
    assert preview["eligible_count"] == 1
    assert preview["confirmation_token"]
    with pytest.raises(Exception):
        reminder_service.validate_confirmation_token("not-a-token")


def test_bulk_send_api_uses_preview_token(client, db, customer):
    config = settings_service.get_reminder(db)
    config.first_reminder_after_days = 0
    db.commit()
    preview = client.post("/api/whatsapp/reminders/preview", json={"respect_quiet_hours": False})
    assert preview.status_code == 200
    body = preview.json()
    assert body["eligible_count"] == 1
    assert body["total_outstanding"] == "5000.00"

    forbidden = client.post(
        "/api/whatsapp/reminders/bulk", json={"confirmation_token": "invalid", "customer_ids": [str(customer.id)]}
    )
    assert forbidden.status_code == 422

    result = client.post(
        "/api/whatsapp/reminders/bulk", json={"confirmation_token": body["confirmation_token"]}
    )
    assert result.status_code == 200
    assert result.json() == {"sent": 1, "failed": 0, "skipped": 0}
    db.refresh(customer)
    assert customer.reminder_count == 1
    assert customer.last_reminder_at is not None


def test_bulk_send_rejects_untargeted_customer(client, db, customer):
    config = settings_service.get_reminder(db)
    config.first_reminder_after_days = 0
    db.commit()
    token = client.post("/api/whatsapp/reminders/preview", json={}).json()["confirmation_token"]
    other = add_customer(db, "Other", "9876543211")
    response = client.post(
        "/api/whatsapp/reminders/bulk",
        json={"confirmation_token": token, "customer_ids": [str(other.id)]},
    )
    assert response.status_code == 422


def test_bulk_send_blocked_in_quiet_hours(client, db, customer, monkeypatch):
    config = settings_service.get_reminder(db)
    config.first_reminder_after_days = 0
    db.commit()
    token = client.post(
        "/api/whatsapp/reminders/preview", json={"respect_quiet_hours": False}
    ).json()["confirmation_token"]
    monkeypatch.setattr(reminder_service, "is_quiet_hours", lambda db: True)
    response = client.post("/api/whatsapp/reminders/bulk", json={"confirmation_token": token})
    assert response.status_code == 422
    assert "Quiet hours" in response.json()["error"]["message"]


def test_automation_disabled_job_is_skipped(client, db, customer):
    result = client.post("/api/scheduler/run?job=reminders")
    assert result.status_code == 200
    assert result.json()["reason"] == "automation_disabled"
    assert db.query(SchedulerRun).filter(SchedulerRun.job_name == "automated_reminders").one().status == "SKIPPED"


def test_automation_enabled_job_sends_once(client, db, customer):
    config = settings_service.get_reminder(db)
    config.automation_enabled = True
    config.first_reminder_after_days = 0
    db.commit()
    first = client.post("/api/scheduler/run?job=reminders")
    assert first.json()["sent"] == 1
    second = client.post("/api/scheduler/run?job=reminders")
    assert second.json()["sent"] == 0
    db.refresh(customer)
    assert customer.reminder_count == 1


def test_retry_job_does_not_resend_failed_without_credentials(client, db, customer):
    from app.models import MessageStatus, WhatsAppMessage

    message = WhatsAppMessage(
        customer_id=customer.id,
        direction="OUTBOUND",
        message_type="REMINDER",
        template_name="payment_reminder",
        status=MessageStatus.FAILED.value,
        phone_number=customer.whatsapp_number,
        idempotency_key=f"reminder:retry-{uuid4()}",
        retry_count=0,
        request_payload={"messaging_product": "whatsapp"},
    )
    db.add(message)
    db.commit()
    result = client.post("/api/scheduler/run?job=retry")
    assert result.status_code == 200
    assert result.json()["attempted"] == 1
