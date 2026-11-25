import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.errors import ConflictError, ValidationError
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
    WhatsAppMessage,
)
from app.schemas import CustomerCreate, ManualPaymentCreate
from app.services import customer_service, payment_service, settings_service


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


def make_payment(db, customer, amount, admin, **overrides):
    data = {"customer_id": customer.id, "amount": amount}
    data.update(overrides)
    return payment_service.create_manual(db, ManualPaymentCreate(**data), actor=admin)


def test_manual_payment_requires_confirmation(db, customer, admin):
    payment = make_payment(db, customer, 2000, admin, method="CASH", reference="RCPT-1")
    assert payment.status == PaymentStatus.AWAITING_CONFIRMATION.value
    assert Decimal(payment.amount) == Decimal("2000.00")

    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("5000.00")
    assert customer.total_paid == Decimal("0.00")

    payment_service.confirm(db, payment.id, actor=admin, send_confirmation=False)

    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("3000.00")
    assert customer.total_paid == Decimal("2000.00")
    txn = db.query(BalanceTransaction).filter(BalanceTransaction.payment_id == payment.id).one()
    assert txn.transaction_type == TransactionType.PAYMENT.value
    assert txn.effect == TransactionEffect.DECREASE.value
    assert txn.balance_after == Decimal("3000.00")


def test_confirmation_sends_message_but_never_financial_rollback(db, customer, admin):
    payment = make_payment(db, customer, 1000, admin)
    result = payment_service.confirm(db, payment.id, actor=admin)
    assert result["message_status"] == MessageStatus.SENT.value
    messages = db.query(WhatsAppMessage).filter(WhatsAppMessage.customer_id == customer.id).all()
    assert len(messages) == 1
    assert messages[0].message_type == "CONFIRMATION"


def test_double_confirmation_rejected(db, customer, admin):
    payment = make_payment(db, customer, 1000, admin)
    payment_service.confirm(db, payment.id, actor=admin, send_confirmation=False)
    with pytest.raises(ConflictError):
        payment_service.confirm(db, payment.id, actor=admin, send_confirmation=False)


def test_reject_does_not_change_balance(db, customer, admin):
    payment = make_payment(db, customer, 1000, admin)
    rejected = payment_service.reject(db, payment.id, "Amount was entered incorrectly", actor=admin)
    assert rejected.status == PaymentStatus.REJECTED.value
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("5000.00")
    assert db.query(BalanceTransaction).count() == 1  # opening balance only


def test_overpayment_cannot_make_balance_negative(db, customer, admin):
    payment = make_payment(db, customer, 6000, admin)
    with pytest.raises(ConflictError, match="negative"):
        payment_service.confirm(db, payment.id, actor=admin, send_confirmation=False)
    db.refresh(payment)
    assert payment.status == PaymentStatus.AWAITING_CONFIRMATION.value
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("5000.00")
    assert db.query(Payment).filter(Payment.status == PaymentStatus.CONFIRMED.value).count() == 0


def test_reversal_restores_balance_with_ledger_entry(db, customer, admin):
    payment = make_payment(db, customer, 2000, admin)
    payment_service.confirm(db, payment.id, actor=admin, send_confirmation=False)
    reversed_payment = payment_service.reverse(db, payment.id, "Bank returned the transaction", actor=admin)
    assert reversed_payment.status == PaymentStatus.REVERSED.value
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


def test_webhook_creates_pending_payment_only(client, db, customer, admin):
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

    payment_service.confirm(db, payment.id, actor=admin, send_confirmation=False)
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


def test_payment_link_is_reused_and_replaced(db, customer, admin):
    first = payment_service.ensure_payment_link(db, customer, actor=admin)
    reused = payment_service.ensure_payment_link(db, customer, actor=admin)
    assert reused.id == first.id

    replaced = payment_service.ensure_payment_link(db, customer, force_new=True, actor=admin)
    assert replaced.id != first.id
    old = db.get(PaymentLink, first.id)
    assert old.status == PaymentLinkStatus.CANCELLED.value
    assert db.query(PaymentLink).count() == 2


def test_link_requires_balance_and_credentials(db, customer, admin):
    payment = make_payment(db, customer, 5000, admin)
    payment_service.confirm(db, payment.id, actor=admin, send_confirmation=False)
    with pytest.raises(ValidationError, match="zero-balance"):
        payment_service.ensure_payment_link(db, customer, actor=admin)

    settings_service.set_value(db, "upi_vpa", "")
    new_customer = customer_service.create(
        db,
        CustomerCreate(
            name="Second Customer", whatsapp_number="9876543211", opening_balance=1000
        ),
        actor=admin,
    )
    with pytest.raises(Exception, match="not configured"):
        payment_service.ensure_payment_link(db, new_customer, actor=admin)


def test_expired_links_are_flagged_by_reconcile(client, db, customer, admin):
    link = payment_service.ensure_payment_link(db, customer, actor=admin)
    row = db.get(PaymentLink, link.id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    result = client.post("/api/scheduler/run?job=reconcile")
    assert result.status_code == 200
    assert result.json()["expired"] == 1
    db.refresh(row)
    assert row.status == PaymentLinkStatus.EXPIRED.value
