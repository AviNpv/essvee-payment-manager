import os
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.db.init_db import init_db, seed
from app.schemas import ManualPaymentCreate
from app.services import customer_service, payment_service

init_db()
with SessionLocal() as db:
    seed(db)

c = TestClient(app)
print("health:", c.get("/health").status_code, c.get("/health").json())
r = c.post("/api/auth/login", json={"email": "admin@essvee.in", "password": "ChangeMe123!"})
print("login:", r.status_code, list(r.json().keys()))
token = r.json()["access_token"]
h = {"Authorization": f"Bearer {token}"}
r = c.post("/api/customers", headers=h, json={
    "name": "Rahul Test", "whatsapp_number": "9876543210", "opening_balance": 5000
})
print("customer:", r.status_code, r.json().get("whatsapp_number"), r.json().get("outstanding_balance"))
cid = r.json()["id"]
r = c.get("/api/customers", headers=h)
print("list:", r.status_code, r.json()["total"])

# UPI app chooser: Pay Now → HTTPS redirect → upi://pay (customer enters amount)
r = c.put("/api/settings/upi", headers=h, json={
    "payment_provider": "UPI",
    "upi_vpa": "essvee@testbank",
    "upi_payee_name": "ESSVEE Associates",
    "public_base_url": "http://localhost:8000",
    "upi_amount_mode": "CUSTOMER_ENTERS",
})
print("upi settings:", r.status_code)
r = c.post("/api/settings/test/upi", headers=h)
print("upi check:", r.status_code, r.json().get("success"))
r = c.put("/api/settings/google", headers=h, json={
    "google_spreadsheet_id": os.getenv("GOOGLE_SPREADSHEET_ID", "")
})
print("google settings:", r.status_code, r.json().get("configured", {}).get("google_spreadsheet"))
r = c.get("/api/sheets/status", headers=h)
print("sheets status:", r.status_code, r.json().get("tabs"))

# Payment confirmation stays internal; the Sheets Customer Details workflow uses
# the same create/confirm service path after an explicit administrator approval.
with SessionLocal() as db:
    customer = customer_service.get_by_phone(db, "9876543210")
    link = payment_service.ensure_payment_link(db, customer, actor=None)
    payload = payment_service._link_out(link, customer)
    print("upi link:", payload.provider, payload.url)
    r = c.get(urlparse(payload.url).path)
    print("pay page:", r.status_code, "upi://pay?" in r.text, "Enter the amount" in r.text)

    payment = payment_service.create_manual(
        db,
        ManualPaymentCreate(
            customer_id=customer.id,
            amount=2000,
            method="UPI",
            reference="TESTUTR123",
        ),
        actor=None,
    )
    print("manual payment:", payment.status)
    payment_service.confirm(db, payment.id, actor=None, send_confirmation=False)
    db.refresh(customer)
    print("balance after:", customer.outstanding_balance, "total paid:", customer.total_paid)

r = c.get("/api/reports/dashboard", headers=h)
print("dashboard:", r.status_code, r.json()["stats"]["total_collected"])
r = c.get("/api/sheets/history", headers=h)
print("sheet history:", r.status_code, len(r.json()))
r = c.get("/api/settings", headers=h)
print("settings:", r.status_code, r.json()["configured"])
