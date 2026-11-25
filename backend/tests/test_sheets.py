import json
from decimal import Decimal

import pytest

from app.models import (
    BalanceTransaction,
    ConsentStatus,
    CustomerStatus,
    ImportHistory,
    MessageStatus,
    TransactionEffect,
    WhatsAppMessage,
)
from app.schemas import CustomerCreate
from app.services import customer_service, reminder_service, settings_service, sheets_service


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
