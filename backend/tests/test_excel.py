import io
from decimal import Decimal

import pandas as pd

from app.models import Customer
from app.services import excel_service


def upload(client, rows, mode="UPDATE_BALANCE"):
    frame = pd.DataFrame(rows)
    stream = io.BytesIO()
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Customers")
    response = client.post(
        "/api/excel/preview",
        files={"file": ("customers.xlsx", stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"mode": mode},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_valid_import(client, db):
    preview = upload(client, [
        {"Customer Name": "Rahul", "WhatsApp Number": "919876543210", "Outstanding Amount": 2500},
        {"Customer Name": "Anil", "WhatsApp Number": "919876543211", "Outstanding Amount": 5000},
    ])
    assert preview["valid_count"] == 2
    result = client.post("/api/excel/import", json={"import_id": preview["import_id"]})
    assert result.status_code == 200
    assert result.json()["imported_count"] == 2
    assert db.query(Customer).count() == 2


def test_invalid_rows_reported(client, db):
    preview = upload(client, [
        {"Customer Name": "Good", "WhatsApp Number": "9876543210", "Outstanding Amount": 100},
        {"Customer Name": "", "WhatsApp Number": "9876543211", "Outstanding Amount": 100},
        {"Customer Name": "Bad Phone", "WhatsApp Number": "12345", "Outstanding Amount": 100},
        {"Customer Name": "Bad Amount", "WhatsApp Number": "9876543212", "Outstanding Amount": "abc"},
    ])
    assert preview["valid_count"] == 1
    assert preview["invalid_count"] == 3
    assert any("Invalid amount" in (row["error"] or "") for row in preview["rows"])


def test_duplicate_within_file(client):
    preview = upload(client, [
        {"Customer Name": "First", "WhatsApp Number": "9876543210", "Outstanding Amount": 100},
        {"Customer Name": "Second", "WhatsApp Number": "+919876543210", "Outstanding Amount": 200},
    ])
    assert preview["valid_count"] == 1
    assert any("Duplicate" in (row["error"] or "") for row in preview["rows"])


def test_balance_reconciliation_requires_confirmation(client, db, customer):
    preview = upload(client, [
        {"Customer Name": "Rahul Test", "WhatsApp Number": "9876543210", "Outstanding Amount": 3200}
    ])
    assert preview["requires_difference_confirmation"] is True
    assert Decimal(str(preview["rows"][0]["difference"])) == Decimal("-1800.00")
    denied = client.post("/api/excel/import", json={"import_id": preview["import_id"]})
    assert denied.status_code == 422
    confirmed = client.post(
        "/api/excel/import",
        json={"import_id": preview["import_id"], "confirm_differences": True},
    )
    assert confirmed.status_code == 200
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("3200.00")
    assert db.query(Customer).count() == 1
    assert customer.total_paid == Decimal("0.00")


def test_adjustment_mode_reduces_balance(client, db, customer):
    preview = upload(client, [
        {"Customer Name": "Rahul Test", "WhatsApp Number": "9876543210", "Outstanding Amount": 1500}
    ], mode="ADJUSTMENT")
    result = client.post("/api/excel/import", json={"import_id": preview["import_id"]})
    assert result.status_code == 200
    db.refresh(customer)
    assert customer.outstanding_balance == Decimal("3500.00")


def test_template_and_export(client):
    template = client.get("/api/excel/template")
    assert template.status_code == 200
    assert template.content[:2] == b"PK"
    exported = client.get("/api/excel/export?format=csv")
    assert exported.status_code == 200
    assert b"WhatsApp Number" in exported.content


def test_audit_log_records_import(client):
    preview = upload(client, [
        {"Customer Name": "Audit", "WhatsApp Number": "9876543215", "Outstanding Amount": 100}
    ])
    client.post("/api/excel/import", json={"import_id": preview["import_id"]})
    logs = client.get("/api/audit-logs").json()
    assert any(entry["action"] == "excel.import" for entry in logs)
