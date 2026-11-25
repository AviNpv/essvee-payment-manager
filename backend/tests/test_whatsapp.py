import hashlib
import hmac
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.core.errors import ValidationError
from app.models import ConsentStatus, MessageStatus, WhatsAppMessage
from app.schemas import CustomerCreate
from app.services import customer_service, payment_service, settings_service, whatsapp_service


def _send_reminder(db, customer, admin):
    """Exercise the same internal reminder path used by confirmed sheet sends."""
    link = payment_service.ensure_payment_link(db, customer, actor=admin)
    before = whatsapp_service.existing_message_ids(db, customer.id)
    message = whatsapp_service.send_reminder(
        db, customer, link.url, idempotency_suffix=f"manual:{link.id}"
    )
    if message.status != MessageStatus.FAILED.value and message.id not in before:
        customer.reminder_count += 1
        customer.last_reminder_at = datetime.now(timezone.utc)
        db.commit()
    return message


def signed_meta_webhook(client, payload, secret="test_app_secret"):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/webhooks/whatsapp",
        content=raw,
        headers={"content-type": "application/json", "x-hub-signature-256": signature},
    )


def inbound_payload(text, message_id=None):
    return {
        "id": message_id or f"meta_{uuid4().hex[:10]}",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": f"inbound_{uuid4().hex[:10]}",
                                    "from": "919876543210",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }


def test_templates_lists_three_approved_templates():
    names = [template["name"] for template in whatsapp_service.template_definitions()["templates"]]
    assert names == ["payment_reminder", "payment_confirmation", "partial_payment_reminder"]


def test_reminder_requires_opt_in(db, customer, admin):
    customer.consent_status = ConsentStatus.OPT_OUT.value
    db.commit()
    with pytest.raises(ValidationError, match="opted in|not opted in"):
        _send_reminder(db, customer, admin)
    assert db.query(WhatsAppMessage).count() == 0


def test_reminder_is_idempotent_for_same_link(db, customer, admin):
    first = _send_reminder(db, customer, admin)
    assert first.status == MessageStatus.SENT.value
    second = _send_reminder(db, customer, admin)
    assert second.id == first.id
    assert db.query(WhatsAppMessage).count() == 1
    db.refresh(customer)
    assert customer.reminder_count == 1
    assert customer.last_reminder_at is not None


def test_reminder_contains_pay_now_button_and_rendered_preview(db, customer, admin):
    message = _send_reminder(db, customer, admin)
    assert message.template_name == "payment_reminder"
    assert "/pay/" in message.button_url
    assert "5,000.00" in message.body_preview
    components = message.request_payload["template"]["components"]
    assert any(component["type"] == "button" and component["sub_type"] == "url" for component in components)


def test_webhook_verification_token(client, db):
    settings_service.set_value(db, "whatsapp_webhook_verify_token", "verify-me")
    response = client.get(
        "/api/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "challenge-123"},
    )
    assert response.status_code == 200
    assert response.text == "challenge-123"
    wrong = client.get(
        "/api/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "challenge-123"},
    )
    assert wrong.status_code == 403


def test_invalid_meta_signature_rejected(client, db):
    settings_service.set_value(db, "whatsapp_app_secret", "test_app_secret")
    response = client.post(
        "/api/webhooks/whatsapp",
        content=json.dumps(inbound_payload("hello")).encode(),
        headers={"x-hub-signature-256": "sha256=deadbeef"},
    )
    assert response.status_code == 401
    assert db.query(WhatsAppMessage).count() == 0


def test_stop_keyword_records_opt_out_and_is_idempotent(client, db, customer):
    settings_service.set_value(db, "whatsapp_app_secret", "test_app_secret")
    payload = inbound_payload("STOP")
    first = signed_meta_webhook(client, payload)
    assert first.status_code == 200
    assert first.json()["processed"] == 1
    db.refresh(customer)
    assert customer.consent_status == ConsentStatus.OPT_OUT.value
    duplicate = signed_meta_webhook(client, payload)
    assert duplicate.json()["status"] == "duplicate"
    assert db.query(WhatsAppMessage).count() == 1


def test_delivery_status_webhook_updates_message(client, db, customer):
    message = WhatsAppMessage(
        customer_id=customer.id,
        direction="OUTBOUND",
        message_type="REMINDER",
        template_name="payment_reminder",
        status=MessageStatus.SENT.value,
        provider_message_id="wamid.status.test",
        phone_number=customer.whatsapp_number,
        idempotency_key="reminder:status-test",
    )
    db.add(message)
    db.commit()
    payload = {
        "id": "meta_status_1",
        "entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.status.test", "status": "delivered"}]}}]}],
    }
    response = signed_meta_webhook(client, payload)
    assert response.status_code == 200
    assert response.json()["processed"] == 1
    db.refresh(message)
    assert message.status == MessageStatus.DELIVERED.value
    assert message.delivered_at is not None


def test_rate_limit_blocks_excess_daily_messages(db, customer, admin):
    config = settings_service.get_reminder(db)
    config.daily_limit = 1
    db.commit()
    _send_reminder(db, customer, admin)
    other = customer_service.create(
        db,
        CustomerCreate(name="Another Customer", whatsapp_number="9876543211", opening_balance=1000),
        actor=admin,
    )
    with pytest.raises(ValidationError, match="limit"):
        _send_reminder(db, other, admin)


def test_missing_credentials_do_not_fake_success(db, customer, admin):
    settings_service.set_value(db, "whatsapp_access_token", "")
    message = _send_reminder(db, customer, admin)
    assert message.status == MessageStatus.FAILED.value
    assert "not configured" in message.error
    assert whatsapp_service.is_configured(db) is False
