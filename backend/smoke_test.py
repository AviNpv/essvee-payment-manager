import os
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.db.init_db import init_db, seed

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
r = c.post("/api/payment-links/generate", headers=h, json={"customer_id": cid})
link = r.json()
print("upi link:", r.status_code, link.get("provider"), link.get("url"))
r = c.get(urlparse(link["url"]).path)
print("pay page:", r.status_code, "upi://pay?" in r.text, "Enter the amount" in r.text)

r = c.post("/api/payments/manual", headers=h, json={"customer_id": cid, "amount": 2000, "method": "UPI", "reference": "TESTUTR123"})
print("manual payment:", r.status_code, r.json().get("status"))
pid = r.json()["id"]
r = c.post(f"/api/payments/{pid}/confirm", headers=h)
print("confirm:", r.status_code, r.json().get("message_status"))
r = c.get("/api/customers/" + cid, headers=h)
print("balance after:", r.json()["outstanding_balance"], "total paid:", r.json()["total_paid"])
r = c.get("/api/reports/dashboard", headers=h)
print("dashboard:", r.status_code, r.json()["stats"]["total_collected"])
r = c.get("/api/excel/template", headers=h)
print("template:", r.status_code, len(r.content))
r = c.get("/api/settings", headers=h)
print("settings:", r.status_code, r.json()["configured"])
