import json
from decimal import Decimal

import pytest

from app.models import (
    BalanceTransaction,
    ConsentStatus,
    CustomerStatus,
    ImportHistory,
    MessageStatus,
    MessageType,
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
    }
    class FakeCalls(list):
        pass

    calls = FakeCalls()

    def fake_fetch(db, spreadsheet_id, tab):
        calls.append((spreadsheet_id, tab))
        rows = [
            (index, dict(zip(headers[tab], [str(cell) for cell in row])))
            for index, row in enumerate(values[tab], start=2)
        ]
        return headers[tab], rows

    monkeypatch.setattr(sheets_service, "_fetch_values", fake_fetch)
    calls.values = values
    return calls


def _seed_customers(db, admin, *, asha_balance=0):
    customer_service.create(
        db,
        CustomerCreate(
            name="Asha",
            whatsapp_number="9876543210",
            opening_balance=asha_balance,
        ),
        actor=admin,
    )


def _preview_tuesday(client):
    response = client.post("/api/sheets/preview", json={"tab": "Tuesday"})
    assert response.status_code == 200, response.text
    return response.json()


def _payload(preview):
    return {
        "import_id": preview["import_id"],
        "confirmation_token": preview["confirmation_token"],
    }


def test_preview_applies_balances_to_all_matched_rows_and_counts_messages(client, db, fake_sheets, admin):
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

    data = _preview_tuesday(client)

    assert data["kind"] == "balances"
    assert data["eligible_count"] == 3
    assert data["reminder_count"] == 1
    assert data["matched_count"] == 3
    assert data["unmatched_count"] == 1
    assert data["opted_out_count"] == 1
    assert data["reason_breakdown"]["invalid_amount"] == 1
    assert data["message_reason_breakdown"]["no_consent"] == 1
    assert data["message_reason_breakdown"]["inactive"] == 1
    assert data["directory_created"] == 1
    assert data["confirmation_token"]
    assert fake_sheets == [("test_spreadsheet_id", "Tuesday"), ("test_spreadsheet_id", "Customers")]
    assert [row["status"] for row in data["rows"]] == ["VALID", "VALID", "VALID", "unmatched", "invalid_amount"]
    assert data["rows"][1]["message_reason"] == "no_consent"
    assert data["rows"][2]["message_reason"] == "inactive"

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("0.00")
    assert db.query(ImportHistory).filter(ImportHistory.file_type == "SHEET").count() == 1


def test_confirm_records_inferred_payment_sends_reminder_and_blocks_replay(client, db, fake_sheets, admin):
    fake_sheets.values["Tuesday"] = [["Asha", "2500"]]
    _seed_customers(db, admin, asha_balance=2600)
    preview = _preview_tuesday(client)

    response = client.post("/api/sheets/send", json=_payload(preview))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated_count"] == 1
    assert result["payments_confirmed"] == 1
    assert result["adjustments"] == 0
    assert result["sent_count"] == 1
    assert Decimal(result["total_amount"]) == Decimal("2500.00")

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("2500.00")
    assert asha.total_paid == Decimal("100.00")

    payment = db.query(Payment).one()
    assert payment.status == PaymentStatus.CONFIRMED.value
    assert payment.method == "OTHER"
    assert payment.reference.startswith("SHEET:")
    txn = db.query(BalanceTransaction).filter(BalanceTransaction.payment_id == payment.id).one()
    assert txn.transaction_type == TransactionType.PAYMENT.value
    assert txn.effect == TransactionEffect.DECREASE.value
    assert txn.balance_after == Decimal("2500.00")

    message = (
        db.query(WhatsAppMessage)
        .filter(
            WhatsAppMessage.customer_id == asha.id,
            WhatsAppMessage.message_type == MessageType.REMINDER.value,
        )
        .one()
    )
    assert message.message_type == MessageType.REMINDER.value
    assert message.status == MessageStatus.SENT.value
    assert message.button_url.startswith("http://localhost:8000/pay/")

    replay = client.post("/api/sheets/send", json=_payload(preview))
    assert replay.status_code == 409


def test_confirm_records_increase_as_audited_adjustment(client, db, fake_sheets, admin):
    fake_sheets.values["Tuesday"] = [["Asha", "2500"]]
    _seed_customers(db, admin, asha_balance=100)
    preview = _preview_tuesday(client)
    assert Decimal(str(preview["rows"][0]["difference"])) == Decimal("2400.00")

    response = client.post("/api/sheets/send", json=_payload(preview))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["adjustments"] == 1
    assert result["payments_confirmed"] == 0
    assert db.query(Payment).count() == 0

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("2500.00")
    txn = (
        db.query(BalanceTransaction)
        .filter(
            BalanceTransaction.customer_id == asha.id,
            BalanceTransaction.transaction_type == TransactionType.ADJUSTMENT.value,
        )
        .one()
    )
    assert txn.transaction_type == TransactionType.ADJUSTMENT.value
    assert txn.effect == TransactionEffect.INCREASE.value


def test_zero_balance_updates_without_reminder(client, db, fake_sheets, admin):
    fake_sheets.values["Tuesday"] = [["Asha", "0"]]
    _seed_customers(db, admin, asha_balance=100)
    preview = _preview_tuesday(client)
    assert preview["reminder_count"] == 0
    assert preview["rows"][0]["message_reason"] == "zero_balance"

    response = client.post("/api/sheets/send", json=_payload(preview))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated_count"] == 1
    assert result["payments_confirmed"] == 1
    assert result["sent_count"] == 0
    assert result["messages_skipped_count"] == 1

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("0.00")
    assert asha.total_paid == Decimal("100.00")
    assert db.query(WhatsAppMessage).filter(WhatsAppMessage.message_type == MessageType.REMINDER.value).count() == 0


def test_matching_pending_payment_is_confirmed_once(client, db, fake_sheets, admin):
    fake_sheets.values["Tuesday"] = [["Asha", "900"]]
    _seed_customers(db, admin, asha_balance=1000)
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

    preview = _preview_tuesday(client)
    assert preview["rows"][0]["pending_payment_id"] == str(pending.id)

    response = client.post("/api/sheets/send", json=_payload(preview))
    assert response.status_code == 200, response.text
    assert response.json()["payments_confirmed"] == 1
    assert db.query(Payment).count() == 1
    db.refresh(pending)
    assert pending.status == PaymentStatus.CONFIRMED.value
    db.refresh(asha)
    assert asha.outstanding_balance == Decimal("900.00")


def test_mismatched_pending_payment_blocks_row_and_can_be_rejected(client, db, fake_sheets, admin):
    fake_sheets.values["Tuesday"] = [["Asha", "900"]]
    _seed_customers(db, admin, asha_balance=1000)
    asha = customer_service.get_by_phone(db, "9876543210")
    pending = payment_service.create_manual(
        db,
        ManualPaymentCreate(customer_id=asha.id, amount=50, method="RAZORPAY"),
        actor=admin,
    )

    preview = _preview_tuesday(client)
    assert preview["eligible_count"] == 0
    assert preview["rows"][0]["reason"] == "awaiting_confirmation"
    assert preview["rows"][0]["pending_payment_id"] == str(pending.id)
    assert client.post("/api/sheets/send", json=_payload(preview)).status_code == 422

    rejected = client.post(
        f"/api/sheets/pending-payments/{pending.id}/reject",
        json={"reason": "Duplicate gateway event"},
    )
    assert rejected.status_code == 200, rejected.text
    db.refresh(pending)
    assert pending.status == PaymentStatus.REJECTED.value
    db.refresh(asha)
    assert asha.outstanding_balance == Decimal("1000.00")

    replay = client.post(
        f"/api/sheets/pending-payments/{pending.id}/reject",
        json={"reason": "Trying again"},
    )
    assert replay.status_code == 409

    fresh_preview = _preview_tuesday(client)
    response = client.post("/api/sheets/send", json=_payload(fresh_preview))
    assert response.status_code == 200, response.text
    db.refresh(asha)
    assert asha.outstanding_balance == Decimal("900.00")
    assert db.query(Payment).count() == 2


def test_invalid_token_and_quiet_hours_block_messages_not_balances(client, db, fake_sheets, admin, monkeypatch):
    fake_sheets.values["Tuesday"] = [["Asha", "2500"]]
    _seed_customers(db, admin, asha_balance=100)
    preview = _preview_tuesday(client)

    payload = _payload(preview)
    payload["confirmation_token"] = preview["confirmation_token"] + "tampered"
    assert client.post("/api/sheets/send", json=payload).status_code == 422

    monkeypatch.setattr(reminder_service, "is_quiet_hours", lambda db: True)
    response = client.post("/api/sheets/send", json=_payload(preview))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated_count"] == 1
    assert result["sent_count"] == 0
    assert result["messages_skipped_count"] == 1

    asha = customer_service.get_by_phone(db, "9876543210")
    assert asha.outstanding_balance == Decimal("2500.00")
    assert db.query(WhatsAppMessage).filter(WhatsAppMessage.message_type == MessageType.REMINDER.value).count() == 0


def test_stale_preview_is_skipped(client, db, fake_sheets, admin):
    fake_sheets.values["Tuesday"] = [["Asha", "2500"]]
    _seed_customers(db, admin, asha_balance=100)
    preview = _preview_tuesday(client)

    asha = customer_service.get_by_phone(db, "9876543210")
    asha.outstanding_balance = Decimal("5000")
    db.commit()

    response = client.post("/api/sheets/send", json=_payload(preview))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated_count"] == 0
    assert result["skipped_count"] == 1
    assert result["sent_count"] == 0

    db.refresh(asha)
    assert asha.outstanding_balance == Decimal("5000.00")
    assert db.query(Payment).count() == 0


def test_status_exposes_only_day_tabs_and_customer_details_is_removed(client):
    status = client.get("/api/sheets/status")
    assert status.status_code == 200, status.text
    assert status.json()["tabs"] == ["Tuesday", "Thursday", "Saturday"]
    assert "payment_tab" not in status.json()

    preview = client.post("/api/sheets/preview", json={"tab": "Customer Details"})
    assert preview.status_code == 422
    assert "Tuesday" in preview.text

    removed = client.get("/api/sheets/confirm-payments")
    assert removed.status_code == 404
