import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.models import (
    BalanceTransaction,
    MessageStatus,
    Payment,
    PaymentLink,
    PaymentLinkStatus,
    PaymentStatus,
    TransactionEffect,
    TransactionType,
    WebhookEvent,
)


def signed_webhook(client, payload: dict, secret: str = "test_webhook_secret"):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={"content-type": "application/json", "x-razorpay-signature": signature},
    )


def gateway_payload(customer_id, amount=250000, payment_id=None, event="payment.captured"):
    return {
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id or f"pay_{uuid4().hex[:12]}",
                    "amount": amount,
                    "status": "captured" if not event.endswith("failed") else "failed",
                    "created_at": int(time.time()),
                    "notes": {"customer_id": str(customer_id)},
                }
            }
        },
    }


def test_manual_payment_requires_confirmation(client, db, customer):
    response = client.post(
        "/api/payments/manual",
        json={"customer_id": str(customer.id), "amount": 2000, "method": "CASH", "reference": "RCPT-1"},
    )
    assert response.status_code == 201
    payment = response.json()
    assert payment["status"] == PaymentStatus.AWAITING_CONFIRMATION.value
    assert Decimal(payment["amount"]) == Decimal("2000.00")

    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("5000.00")
    assert customer.total_paid == Decimal("0.00")

    confirmed = client.post(f"/api/payments/{payment['id']}/confirm")
    assert confirmed.status_code == 200
    assert Decimal(confirmed.json()["balance_after"]) == Decimal("3000.00")

    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("3000.00")
    assert customer.total_paid == Decimal("2000.00")
    txn = db.query(BalanceTransaction).filter(BalanceTransaction.payment_id == UUID(payment["id"])).one()
    assert txn.transaction_type == TransactionType.PAYMENT.value
    assert txn.effect == TransactionEffect.DECREASE.value
    assert txn.balance_after == Decimal("3000.00")


def test_confirmation_sends_message_but_never_financial_rollback(client, db, customer):
    payment = client.post(
        "/api/payments/manual", json={"customer_id": str(customer.id), "amount": 1000}
    ).json()
    result = client.post(f"/api/payments/{payment['id']}/confirm")
    assert result.status_code == 200
    assert result.json()["message_status"] == MessageStatus.SENT.value
    messages = client.get(f"/api/whatsapp/messages?customer_id={customer.id}").json()
    assert messages["total"] == 1
    assert messages["items"][0]["message_type"] == "CONFIRMATION"


def test_double_confirmation_rejected(client, customer):
    payment = client.post(
        "/api/payments/manual", json={"customer_id": str(customer.id), "amount": 1000}
    ).json()
    client.post(f"/api/payments/{payment['id']}/confirm")
    second = client.post(f"/api/payments/{payment['id']}/confirm")
    assert second.status_code == 409


def test_reject_does_not_change_balance(client, db, customer):
    payment = client.post(
        "/api/payments/manual", json={"customer_id": str(customer.id), "amount": 1000}
    ).json()
    response = client.post(
        f"/api/payments/{payment['id']}/reject", json={"reason": "Amount was entered incorrectly"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == PaymentStatus.REJECTED.value
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("5000.00")
    assert db.query(BalanceTransaction).count() == 1  # opening balance only


def test_overpayment_cannot_make_balance_negative(client, db, customer):
    payment = client.post(
        "/api/payments/manual", json={"customer_id": str(customer.id), "amount": 6000}
    ).json()
    result = client.post(f"/api/payments/{payment['id']}/confirm")
    assert result.status_code == 409
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("5000.00")
    assert db.query(Payment).filter(Payment.status == PaymentStatus.CONFIRMED.value).count() == 0


def test_reversal_restores_balance_with_ledger_entry(client, db, customer):
    payment = client.post(
        "/api/payments/manual", json={"customer_id": str(customer.id), "amount": 2000}
    ).json()
    client.post(f"/api/payments/{payment['id']}/confirm")
    response = client.post(
        f"/api/payments/{payment['id']}/reverse", json={"reason": "Bank returned the transaction"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == PaymentStatus.REVERSED.value
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("5000.00")
    reversal = (
        db.query(BalanceTransaction)
        .filter(BalanceTransaction.transaction_type == TransactionType.REVERSAL.value)
        .one()
    )
    assert reversal.effect == TransactionEffect.INCREASE.value


def test_webhook_signature_is_enforced(client, db):
    raw = json.dumps({"event": "payment.captured", "payload": {}}).encode()
    response = client.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={"x-razorpay-signature": "bad-signature"},
    )
    assert response.status_code == 401
    event = db.query(WebhookEvent).one()
    assert event.signature_valid is False
    assert event.processing_error == "invalid_signature"
    assert db.query(Payment).count() == 0


def test_webhook_creates_pending_payment_only(client, db, customer):
    payload = gateway_payload(customer.id, payment_id="pay_webhook_1")
    response = signed_webhook(client, payload)
    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["amount"] == "2500.00"
    payment = db.query(Payment).one()
    assert payment.status == PaymentStatus.AWAITING_CONFIRMATION.value
    assert payment.gateway_payment_id == "pay_webhook_1"
    assert payment.extra_data["verified"] is True
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("5000.00")

    duplicate = signed_webhook(client, payload)
    assert duplicate.json()["status"] == "duplicate"
    assert db.query(Payment).count() == 1

    confirmed = client.post(f"/api/payments/{payment.id}/confirm")
    assert confirmed.status_code == 200
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("2500.00")


def test_failed_webhook_marks_payment_failed(client, db, customer):
    payload = gateway_payload(customer.id, event="payment.failed")
    response = signed_webhook(client, payload)
    assert response.status_code == 200
    payment = db.query(Payment).one()
    assert payment.status == PaymentStatus.FAILED.value
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("5000.00")


def test_webhook_for_unknown_customer_is_skipped(client, db):
    payload = gateway_payload(uuid4())
    response = signed_webhook(client, payload)
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    assert db.query(Payment).count() == 0


def test_payment_link_is_reused_and_replaced(client, db, customer):
    first = client.post(
        "/api/payment-links/generate", json={"customer_id": str(customer.id)}
    )
    assert first.status_code == 201
    reused = client.post(
        "/api/payment-links/generate", json={"customer_id": str(customer.id)}
    )
    assert reused.status_code == 201
    assert reused.json()["id"] == first.json()["id"]

    replaced = client.post(
        "/api/payment-links/generate", json={"customer_id": str(customer.id), "force_new": True}
    )
    assert replaced.status_code == 201
    assert replaced.json()["id"] != first.json()["id"]
    old = db.get(PaymentLink, UUID(first.json()["id"]))
    assert old.status == PaymentLinkStatus.CANCELLED.value
    assert db.query(PaymentLink).count() == 2


def test_link_requires_balance_and_credentials(client, db, customer):
    client.post(
        "/api/payments/manual", json={"customer_id": str(customer.id), "amount": 5000}
    )
    paid = db.query(Payment).one()
    client.post(f"/api/payments/{paid.id}/confirm")
    response = client.post(
        "/api/payment-links/generate", json={"customer_id": str(customer.id)}
    )
    assert response.status_code == 422

    from app.services import settings_service

    settings_service.set_value(db, "upi_vpa", "")
    new_customer = client.post(
        "/api/customers",
        json={"name": "Second Customer", "whatsapp_number": "9876543211", "opening_balance": 1000},
    ).json()
    missing = client.post(
        "/api/payment-links/generate", json={"customer_id": new_customer["id"]}
    )
    assert missing.status_code == 409
    assert "not configured" in missing.json()["error"]["message"]


def test_expired_links_are_flagged_by_reconcile(client, db, customer):
    link = client.post(
        "/api/payment-links/generate", json={"customer_id": str(customer.id)}
    ).json()
    row = db.get(PaymentLink, UUID(link["id"]))
    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    result = client.post("/api/scheduler/run?job=reconcile")
    assert result.status_code == 200
    assert result.json()["expired"] == 1
    db.refresh(row)
    assert row.status == PaymentLinkStatus.EXPIRED.value
