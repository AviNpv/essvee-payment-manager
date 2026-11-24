"""Public UPI redirect used as the WhatsApp Pay Now button destination."""

import html
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Customer, PaymentLinkStatus
from app.services import payment_service, settings_service, upi_service

router = APIRouter(tags=["Pay"])


def _page(title: str, body: str, *, auto_uri: str | None = None) -> str:
    script = ""
    if auto_uri:
        script = f"<script>window.location.replace({json.dumps(auto_uri)});</script>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a;margin:0;display:grid;place-items:center;min-height:100vh;padding:24px}}
.card{{background:white;border:1px solid #e2e8f0;border-radius:16px;padding:28px;max-width:420px;text-align:center;box-shadow:0 10px 30px rgba(15,23,42,.08)}}
a.button{{display:inline-block;margin-top:16px;padding:12px 20px;border-radius:10px;background:#2563eb;color:white;text-decoration:none;font-weight:700}}
code{{display:block;margin-top:14px;padding:10px;background:#f1f5f9;border-radius:8px;word-break:break-all}}</style>
</head><body><div class="card">{body}{script}</div></body></html>"""


@router.get("/pay/{token}", response_class=HTMLResponse, include_in_schema=False)
def upi_pay_redirect(token: str, db: Session = Depends(get_db)):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token):
        raise HTTPException(status_code=404, detail="Payment link not found")
    link = payment_service.link_by_public_token(db, token)
    if not link or payment_service.link_provider(link) != "UPI":
        raise HTTPException(status_code=404, detail="Payment link not found")

    if link.status == PaymentLinkStatus.ACTIVE.value and link.expires_at and link.expires_at <= datetime.now(timezone.utc):
        link.status = PaymentLinkStatus.EXPIRED.value
        db.commit()
    if link.status != PaymentLinkStatus.ACTIVE.value:
        return _page(
            "Link unavailable",
            "<h1>Link unavailable</h1><p>This payment link is no longer active. "
            "Please request a fresh Pay Now message.</p>",
        )

    try:
        uri = upi_service.build_uri_for_link(db, link)
    except Exception:
        return _page(
            "Payment configuration error",
            "<h1>Payment configuration error</h1><p>The UPI details for this link are "
            "not available. Please contact the business.</p>",
        )

    customer = db.get(Customer, link.customer_id)
    name = html.escape(customer.name if customer else "Customer")
    outstanding = Decimal(str(link.amount))
    mode = settings_service.upi_credentials(db)["amount_mode"]
    if mode == "CUSTOMER_ENTERS":
        amount_note = (
            f"Enter the amount you wish to pay in your UPI app. "
            f"Outstanding shown for reference: ₹{outstanding:,.2f}."
        )
    else:
        amount_note = f"Pay ₹{outstanding:,.2f} in your UPI app."
    parsed = urlparse(uri)
    body = (
        f"<h1>Open your UPI app</h1><p>{name}, choose your UPI app to continue.</p>"
        f"<p>{html.escape(amount_note)}</p>"
        f'<a class="button" href="{html.escape(uri, quote=True)}">Open UPI apps</a>'
        f"<code>{html.escape(uri)}</code>"
        f"<p style='font-size:12px;color:#64748b'>Your bank/UPI app will open. "
        f"After paying, share the UTR with the business for confirmation.</p>"
    )
    return _page("Open UPI app", body, auto_uri=uri)
