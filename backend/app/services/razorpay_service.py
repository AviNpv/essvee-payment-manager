"""Official Razorpay Payment Links API client (no mock responses)."""

import hashlib
import hmac
from decimal import Decimal
from typing import Any
from uuid import UUID

import requests

from app.core.config import settings as env
from app.core.errors import ConfigurationError, ProviderError, ValidationError
from app.models import Customer, PaymentLink, PaymentLinkStatus, ReminderSetting
from app.services import settings_service

API_BASE = "https://api.razorpay.com/v1"
TIMEOUT = (5, 20)


def credentials(db) -> dict[str, str]:
    return settings_service.razorpay_credentials(db)


def is_configured(db) -> bool:
    creds = credentials(db)
    return bool(creds["key_id"] and creds["key_secret"])


def _session(db) -> tuple[requests.Session, dict[str, str]]:
    creds = credentials(db)
    if not creds["key_id"] or not creds["key_secret"]:
        raise ConfigurationError(
            "Razorpay credentials are not configured. Add the Key ID and Key Secret in Settings → Razorpay."
        )
    session = requests.Session()
    session.auth = (creds["key_id"], creds["key_secret"])
    session.headers.update({"Content-Type": "application/json"})
    return session, creds


def _request(db, method: str, path: str, **kwargs) -> dict[str, Any]:
    session, _ = _session(db)
    try:
        response = session.request(method, f"{API_BASE}{path}", timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise ProviderError(f"Razorpay request failed: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        # Never echo credentials or full response bodies that may contain PII.
        raise ProviderError(
            f"Razorpay API returned HTTP {response.status_code}: {_safe_error(response)}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError("Razorpay returned an invalid JSON response") from exc


def _safe_error(response: requests.Response) -> str:
    try:
        body = response.json()
        error = body.get("error", {}) if isinstance(body, dict) else {}
        return str(error.get("description") or error.get("code") or "unknown error")[:300]
    except ValueError:
        return "unknown error"


def paise(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1")))


def create_payment_link(
    db,
    customer: Customer,
    amount: Decimal,
    *,
    reminder: ReminderSetting,
    note: str | None = None,
) -> dict[str, Any]:
    if amount <= 0:
        raise ValidationError("Payment link amount must be greater than zero")
    minimum = reminder.minimum_payment_amount or Decimal("10")
    if amount < minimum:
        raise ValidationError(f"Minimum payment amount is ₹{minimum}")
    payload: dict[str, Any] = {
        "amount": paise(amount),
        "currency": "INR",
        "description": f"Outstanding payment - {settings_service.company_name(db)}",
        "customer": {
            "name": customer.name[:100],
            "contact": customer.whatsapp_number.lstrip("+"),
        },
        "notes": {
            "customer_id": str(customer.id),
            "system": "essvee-payment-manager",
        },
        "reminder_enable": False,
    }
    if customer.email:
        payload["customer"]["email"] = customer.email[:150]
    if note:
        payload["notes"]["note"] = str(note)[:200]
    if reminder.link_expiry_days:
        from datetime import datetime, timedelta, timezone

        payload["expire_by"] = int(
            (datetime.now(timezone.utc) + timedelta(days=reminder.link_expiry_days)).timestamp()
        )
    if reminder.allow_partial_payments:
        payload["accept_partial"] = True
        payload["min_partial_amount"] = paise(min(minimum, amount))
    callback = settings_service.get(db, "razorpay_callback_url", "") or getattr(
        env, "razorpay_callback_url", ""
    )
    if callback:
        payload["callback_url"] = callback
    return _request(db, "POST", "/payment_links", json=payload)


def cancel_payment_link(db, razorpay_link_id: str) -> dict[str, Any]:
    return _request(db, "POST", f"/payment_links/{razorpay_link_id}/cancel")


def fetch_payment(db, razorpay_payment_id: str) -> dict[str, Any]:
    return _request(db, "GET", f"/payments/{razorpay_payment_id}")


def fetch_link(db, razorpay_link_id: str) -> dict[str, Any]:
    return _request(db, "GET", f"/payment_links/{razorpay_link_id}")


def verify_webhook_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def test_connection(db) -> dict[str, Any]:
    if not is_configured(db):
        raise ConfigurationError("Razorpay credentials are not configured")
    data = _request(db, "GET", "/payment_links?count=1")
    creds = credentials(db)
    return {
        "status": "ok",
        "mode": creds["mode"],
        "key_id_masked": creds["key_id"][:6] + "••••" if creds["key_id"] else "",
        "links_count": len(data.get("items", [])),
    }
