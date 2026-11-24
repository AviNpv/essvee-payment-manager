import os
import time
from uuid import uuid4

# Environment must be configured before application modules are imported.
os.environ["DATABASE_URL"] = "sqlite:///./pytest_essvee.db"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["SECRET_KEY"] = "pytest-secret-key-do-not-use-in-production"
os.environ["ADMIN_EMAIL"] = "admin@essvee.in"
os.environ["ADMIN_PASSWORD"] = "ChangeMe123!"

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.db.init_db import seed
from app.db.session import SessionLocal, engine
from app.main import app
from app.models import Base, Customer, User
from app.schemas import CustomerCreate
from app.services import customer_service, settings_service


class FakeResponse:
    """A minimal requests.Response stand-in for provider HTTP calls."""

    def __init__(self, data: dict, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self) -> dict:
        return self._data


@pytest.fixture(autouse=True)
def mock_providers(monkeypatch):
    """No test ever reaches Meta or Razorpay; provider replies are deterministic."""

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        assert "graph.facebook.com" in url
        return FakeResponse({"messages": [{"id": f"wamid.test.{uuid4().hex[:12]}"}]})

    def fake_get(url, headers=None, timeout=None):
        assert "graph.facebook.com" in url
        return FakeResponse({"verified_name": "ESSVEE Test", "display_phone_number": "15550000000"})

    def fake_session_request(self, method, url, **kwargs):
        assert "api.razorpay.com" in url
        method = method.upper()
        if method == "POST" and url.endswith("/payment_links"):
            return FakeResponse(
                {
                    "id": f"link_test_{uuid4().hex[:12]}",
                    "short_url": f"https://rzp.io/i/{uuid4().hex[:8]}",
                    "expire_by": int(time.time()) + 7 * 86400,
                }
            )
        if method == "POST" and url.endswith("/cancel"):
            return FakeResponse({"status": "cancelled"})
        if method == "GET" and "/payment_links/" in url:
            return FakeResponse({"id": url.rsplit("/", 1)[-1], "status": "active"})
        return FakeResponse({"items": [], "count": 0})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests.Session, "request", fake_session_request)


@pytest.fixture()
def db() -> Session:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    seed(session)
    yield session
    session.close()


@pytest.fixture()
def admin(db: Session) -> User:
    return db.query(User).first()


@pytest.fixture()
def client(db: Session, admin: User):
    access = create_access_token(str(admin.id), admin.role)
    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {access}"})
        yield test_client


@pytest.fixture()
def customer(db: Session) -> Customer:
    return customer_service.create(
        db,
        CustomerCreate(name="Rahul Test", whatsapp_number="9876543210", opening_balance=5000),
        actor=db.query(User).first(),
    )


@pytest.fixture(autouse=True)
def configure_test_secrets(db: Session):
    settings_service.set_value(db, "razorpay_webhook_secret", "test_webhook_secret")
    settings_service.set_value(db, "whatsapp_access_token", "test_token")
    settings_service.set_value(db, "whatsapp_phone_number_id", "1234567890")
    settings_service.set_value(db, "razorpay_key_id", "rzp_test_123")
    settings_service.set_value(db, "razorpay_key_secret", "rzp_secret_123")
    settings_service.set_value(db, "payment_provider", "UPI")
    settings_service.set_value(db, "upi_vpa", "essvee@testbank")
    settings_service.set_value(db, "upi_payee_name", "ESSVEE Test")
    settings_service.set_value(db, "public_base_url", "http://localhost:8000")
    settings_service.set_value(db, "upi_amount_mode", "CUSTOMER_ENTERS")
