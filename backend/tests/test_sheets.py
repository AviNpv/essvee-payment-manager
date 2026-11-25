import json
from decimal import Decimal

import pytest

from app.models import (
    BalanceTransaction,
    ConsentStatus,
    CustomerStatus,
    ImportHistory,
    MessageStatus,
    Payment,
    PaymentStatus,
    TransactionEffect,
    TransactionType,
    WhatsAppMessage,
)
from app.schemas import CustomerCreate, ManualPaymentCreate
from app.services import (
    customer_service,
    payment_service,
    reminder_service,
    settings_service,
    sheets_service,
)


@pytest.fixture(autouse=True)
def no_quiet_hours(monkeypatch):
    monkeypatch.setattr(reminder_service, "is_quiet_hours", lambda db: False)


@pytest.fixture(autouse=True)
def google_config(db):
    settings_service.set_value(
        db,
        "google_service_account_json",
        json.dumps(
            {
                "type": "service_account",
                "client_email": "sheet-reader@example.iam.gserviceaccount.com",
                "private_key": "not-used-in-tests",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
    )
    settings_service.set_value(db, "google_spreadsheet_id", "test_spreadsheet_id")


@pytest.fixture
def fake_sheets(monkeypatch):
    headers = {
        "Customers": ["Name", "Mobile"],
        "Tuesday": ["Name", "Amount"],
        "Thursday": ["Name", "Amount"],
        "Saturday": ["Name", "Amount"],
        "Customer Details": ["Name", "Paid Amount"],
    }
    values = {
        "Customers": [
            ["Asha", "9876543210"],
            ["Ravi", "9876543211"],
            ["Meena", "9876543212"],
        ],
        "Tuesday": [
            ["Asha", "2500"],
            ["Ravi", "1200"],
            ["Meena", "500"],
            ["Unknown", "900"],
            ["Asha", "-10"],
        ],
        "Thursday": [],
        "Saturday": [],
        "Customer Details": [
            ["Asha", "100"],
            ["Ravi", "2000"],
            ["Meena", "100"],
            ["Unknown", "50"],
            ["Asha", "-10"],
        ],
    }
    calls = []

    def fake_fetch(db, spreadsheet_id, tab):
        calls.append((spreadsheet_id, tab))
        rows = [
            (index, dict(zip(headers[tab], [str(cell) for cell in row])))
            for index, row in enumerate(values[tab], start=2)
        ]
        return headers[tab], rows

    monkeypatch.setattr(sheets_service, "_fetch_values", fake_fetch)
    return calls


def test_preview_classifies_rows_and_syncs_directory(client, db, fake_sheets, admin):
    customer_service.create(
        db,
        CustomerCreate(
            name="Ravi",
            whatsapp_number="9876543211",
            consent_status=ConsentStatus.OPT_OUT.value,
        ),
        actor=admin,
    )
    customer_service.create(
        db,
        CustomerCreate(
            name="Meena",
            whatsapp_number="9876543212",
            status=CustomerStatus.INACTIVE.value,
        ),
        actor=admin,
    )

    response = client.post("/api/sheets/preview", json={"tab": "Tuesday"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["eligible_count"] == 1, [(r["name"], r["reason"]) for r in data["rows"]]
    assert data["unmatched_count"] == 1
    assert data["opted_out_count"] == 1
    assert data["reason_breakdown"]["inactive"] == 1
    assert data["reason_breakdown"]["invalid_amount"] == 1
    assert data["directory_created"] == 1
    assert data["confirmation_token"]
    assert fake_sheets == [("test_spreadsheet_id", "Tuesday"), ("test_spreadsheet_id", "Customers")]

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("0.00")
    assert db.query(ImportHistory).filter(ImportHistory.file_type == "SHEET").count() == 1


def test_confirm_sets_balance_sends_and_blocks_replay(client, db, fake_sheets, admin):
    customer_service.create(
        db,
        CustomerCreate(
            name="Ravi",
            whatsapp_number="9876543211",
            consent_status=ConsentStatus.OPT_OUT.value,
        ),
        actor=admin,
    )
    customer_service.create(
        db,
        CustomerCreate(
            name="Meena",
            whatsapp_number="9876543212",
            status=CustomerStatus.INACTIVE.value,
        ),
        actor=admin,
    )
    preview = client.post("/api/sheets/preview", json={"tab": "Tuesday"}).json()
    payload = {
        "import_id": preview["import_id"],
        "confirmation_token": preview["confirmation_token"],
    }
    response = client.post("/api/sheets/send", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated_count"] == 1
    assert result["sent_count"] == 1
    assert Decimal(result["total_amount"]) == Decimal("2500.00")

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("2500.00")
    transaction = db.query(BalanceTransaction).filter(BalanceTransaction.customer_id == asha.id).one()
    assert transaction.transaction_type == "ADJUSTMENT"
    assert transaction.effect == TransactionEffect.INCREASE.value

    message = db.query(WhatsAppMessage).filter(WhatsAppMessage.customer_id == asha.id).one()
    assert message.status == MessageStatus.SENT.value
    assert message.button_url.startswith("http://localhost:8000/pay/")

    replay = client.post("/api/sheets/send", json=payload)
    assert replay.status_code == 409


def test_invalid_token_and_quiet_hours_are_rejected(client, db, fake_sheets, monkeypatch):
    preview = client.post("/api/sheets/preview", json={"tab": "Tuesday"}).json()
    payload = {
        "import_id": preview["import_id"],
        "confirmation_token": preview["confirmation_token"] + "tampered",
    }
    assert client.post("/api/sheets/send", json=payload).status_code == 422

    monkeypatch.setattr(reminder_service, "is_quiet_hours", lambda db: True)
    valid = {"import_id": preview["import_id"], "confirmation_token": preview["confirmation_token"]}
    response = client.post("/api/sheets/send", json=valid)
    assert response.status_code == 422
    assert "Quiet hours" in response.text

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("0.00")


def _seed_payment_customers(db, admin):
    for name, phone, balance in (
        ("Asha", "9876543210", 1000),
        ("Ravi", "9876543211", 500),
        ("Meena", "9876543212", 0),
    ):
        customer_service.create(
            db,
            CustomerCreate(name=name, whatsapp_number=phone, opening_balance=balance),
            actor=admin,
        )


def test_customer_details_preview_classifies_payments_without_changing_balances(client, db, fake_sheets, admin):
    _seed_payment_customers(db, admin)

    response = client.post("/api/sheets/preview", json={"tab": "Customer Details"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["kind"] == "payments"
    assert data["eligible_count"] == 1
    assert data["unmatched_count"] == 1
    assert data["reason_breakdown"]["exceeds_balance"] == 1
    assert data["reason_breakdown"]["no_outstanding"] == 1
    assert data["reason_breakdown"]["invalid_amount"] == 1
    assert data["confirmation_token"]
    assert fake_sheets[0] == ("test_spreadsheet_id", "Customer Details")

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("1000.00")
    assert db.query(Payment).count() == 0
    assert db.query(BalanceTransaction).filter(BalanceTransaction.customer_id == asha.id).count() == 1


def test_confirm_payments_reduces_balance_records_ledger_and_blocks_replay(client, db, fake_sheets, admin):
    _seed_payment_customers(db, admin)
    preview = client.post("/api/sheets/preview", json={"tab": "Customer Details"}).json()
    payload = {
        "import_id": preview["import_id"],
        "confirmation_token": preview["confirmation_token"],
    }

    wrong_endpoint = client.post("/api/sheets/send", json=payload)
    assert wrong_endpoint.status_code == 422

    response = client.post("/api/sheets/confirm-payments", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["confirmed_count"] == 1
    assert result["skipped_count"] == 0
    assert Decimal(result["total_amount"]) == Decimal("100.00")

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("900.00")
    assert asha.total_paid == Decimal("100.00")
    payment = db.query(Payment).one()
    assert payment.status == PaymentStatus.CONFIRMED.value
    assert payment.method == "OTHER"
    assert payment.reference.startswith("SHEET:")
    txn = (
        db.query(BalanceTransaction)
        .filter(BalanceTransaction.payment_id == payment.id)
        .one()
    )
    assert txn.transaction_type == TransactionType.PAYMENT.value
    assert txn.effect == TransactionEffect.DECREASE.value
    assert txn.balance_after == Decimal("900.00")

    replay = client.post("/api/sheets/confirm-payments", json=payload)
    assert replay.status_code == 409
    assert db.query(Payment).count() == 1


def test_customer_details_confirms_matching_webhook_payment(client, db, fake_sheets, admin):
    _seed_payment_customers(db, admin)
    asha = customer_service.get_by_phone(db, "9876543210")
    pending = payment_service.create_manual(
        db,
        ManualPaymentCreate(
            customer_id=asha.id,
            amount=100,
            method="RAZORPAY",
            reference="pay_webhook_1",
        ),
        actor=admin,
    )

    preview = client.post("/api/sheets/preview", json={"tab": "Customer Details"}).json()
    assert preview["eligible_count"] == 1
    assert preview["rows"][0]["pending_payment_id"] == str(pending.id)

    response = client.post(
        "/api/sheets/confirm-payments",
        json={
            "import_id": preview["import_id"],
            "confirmation_token": preview["confirmation_token"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["confirmed_count"] == 1
    assert db.query(Payment).count() == 1
    db.refresh(pending)
    assert pending.status == PaymentStatus.CONFIRMED.value
    db.refresh(asha)
    assert asha.outstanding_balance == Decimal("900.00")


def test_customer_details_flags_mismatched_pending_payment(client, db, fake_sheets, admin):
    _seed_payment_customers(db, admin)
    asha = customer_service.get_by_phone(db, "9876543210")
    pending = payment_service.create_manual(
        db,
        ManualPaymentCreate(customer_id=asha.id, amount=50, method="RAZORPAY"),
        actor=admin,
    )
    preview = client.post("/api/sheets/preview", json={"tab": "Customer Details"}).json()
    row = preview["rows"][0]
    assert row["reason"] == "awaiting_confirmation"
    assert row["pending_payment_id"] == str(pending.id)
    assert preview["eligible_count"] == 0


def test_verified_payment_can_be_rejected_from_sheets(client, db, admin):
    _seed_payment_customers(db, admin)
    asha = customer_service.get_by_phone(db, "9876543210")
    pending = payment_service.create_manual(
        db,
        ManualPaymentCreate(customer_id=asha.id, amount=100, method="RAZORPAY"),
        actor=admin,
    )
    response = client.post(
        f"/api/sheets/pending-payments/{pending.id}/reject",
        json={"reason": "Duplicate gateway event"},
    )
    assert response.status_code == 200, response.text
    db.refresh(pending)
    assert pending.status == PaymentStatus.REJECTED.value
    assert "Duplicate" in pending.rejection_reason
    db.refresh(asha)
    assert asha.outstanding_balance == Decimal("1000.00")

    replay = client.post(
        f"/api/sheets/pending-payments/{pending.id}/reject",
        json={"reason": "Trying again"},
    )
    assert replay.status_code == 409
