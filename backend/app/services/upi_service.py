"""UPI deep-link payment links.

The WhatsApp URL button points to an HTTPS redirect on this application. That
redirect opens ``upi://pay?...``, which makes a phone show its installed UPI
apps. Payments are never auto-confirmed; the administrator still records and
approves the real amount in Payments.
"""

import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlparse

from app.core.errors import ConfigurationError, ValidationError
from app.models import Customer, PaymentLink, ReminderSetting
from app.services import settings_service

AMOUNT_MODES = {"CUSTOMER_ENTERS", "FIXED_OUTSTANDING"}


def credentials(db) -> dict[str, str]:
    return settings_service.upi_credentials(db)


def is_configured(db) -> bool:
    creds = credentials(db)
    return bool(creds["vpa"] and creds["public_base_url"])


def _base_url(db) -> str:
    base = (credentials(db)["public_base_url"] or "").strip().rstrip("/")
    if not base:
        raise ConfigurationError(
            "Public base URL is not configured. Add it in Settings → UPI before sending payment links."
        )
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigurationError("Public base URL must be a full http(s) URL.")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ConfigurationError(
            "Public base URL must use HTTPS so Meta accepts the WhatsApp Pay Now button."
        )
    return base


def build_uri(
    db,
    customer: Customer,
    amount: Decimal,
    token: str,
    *,
    note: str | None = None,
) -> str:
    creds = credentials(db)
    vpa = (creds["vpa"] or "").strip()
    if not vpa or "@" not in vpa:
        raise ConfigurationError(
            "UPI ID (VPA) is not configured. Add a valid VPA such as name@bank in Settings → UPI."
        )
    mode = creds["amount_mode"] or "CUSTOMER_ENTERS"
    if mode not in AMOUNT_MODES:
        raise ConfigurationError("UPI amount mode must be CUSTOMER_ENTERS or FIXED_OUTSTANDING.")

    company = settings_service.company_name(db)
    payee = (creds["payee_name"] or company or "Payee").strip()[:50] or "Payee"
    value = Decimal(str(amount))
    params: dict[str, str] = {
        "pa": vpa,
        "pn": payee,
        "cu": "INR",
        "tr": token[:64],
    }
    if mode == "FIXED_OUTSTANDING":
        params["am"] = f"{value:.2f}"
    message = note or f"{company} payment · Outstanding ₹{value:,.2f}"
    params["tn"] = message[:100]
    return "upi://pay?" + urlencode(params)


def build_uri_for_link(db, link: PaymentLink) -> str:
    """Rebuild the deep link so VPA changes apply to existing active links."""
    if not link.razorpay_link_id.startswith("upi:"):
        raise ValidationError("This is not a UPI payment link")
    token = link.razorpay_link_id.split(":", 1)[1]
    customer = db.get(Customer, link.customer_id)
    if not customer:
        raise ValidationError("Customer for this payment link no longer exists")
    note = (link.extra_data or {}).get("note")
    return build_uri(db, customer, link.amount, token, note=note)


def create_payment_link(
    db,
    customer: Customer,
    amount: Decimal,
    *,
    reminder: ReminderSetting,
    note: str | None = None,
) -> dict[str, Any]:
    """Create the redirect URL and matching UPI URI; caller persists the row."""
    if not is_configured(db):
        raise ConfigurationError(
            "UPI is not configured. Add a UPI ID/VPA and public base URL in Settings → UPI."
        )
    token = secrets.token_urlsafe(18)
    expires_at = None
    if reminder.link_expiry_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=reminder.link_expiry_days)
    return {
        "id": f"upi:{token}",
        "short_url": f"{_base_url(db)}/pay/{token}",
        "expire_by": int(expires_at.timestamp()) if expires_at else None,
        "upi_uri": build_uri(db, customer, amount, token, note=note),
        "token": token,
        "amount_mode": credentials(db)["amount_mode"] or "CUSTOMER_ENTERS",
        "note": note,
    }


def test_connection(db) -> dict[str, Any]:
    if not is_configured(db):
        raise ConfigurationError("UPI is not configured")
    creds = credentials(db)
    return {
        "status": "ok",
        "vpa": creds["vpa"],
        "payee_name": creds["payee_name"] or settings_service.company_name(db),
        "public_base_url": _base_url(db),
        "amount_mode": creds["amount_mode"] or "CUSTOMER_ENTERS",
    }
