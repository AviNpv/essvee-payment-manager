"""Verified incoming webhooks: Meta WhatsApp and Razorpay."""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import WebhookEvent
from app.services import payment_service, settings_service, whatsapp_service

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.get("/whatsapp")
def verify_whatsapp(
    request: Request, db: Session = Depends(get_db)
):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")
    expected = settings_service.whatsapp_credentials(db)["webhook_verify_token"]
    if mode == "subscribe" and expected and token == expected:
        return PlainTextResponse(content=challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    secret = settings_service.whatsapp_credentials(db)["app_secret"]
    if secret:
        digest = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    event_id = str(payload.get("id") or hashlib.sha256(raw).hexdigest())
    existing = (
        db.query(WebhookEvent)
        .filter(WebhookEvent.provider == "META", WebhookEvent.event_id == event_id)
        .first()
    )
    if existing and existing.processed:
        return {"status": "duplicate"}
    event = existing or WebhookEvent(
        provider="META",
        event_id=event_id,
        event_type="whatsapp",
        payload=payload,
        signature_valid=True,
    )
    db.add(event)
    db.commit()
    changed = 0
    for entry in payload.get("entry", []) or []:
        for value in entry.get("changes", []) or []:
            inner = value.get("value", {}) or {}
            changed += whatsapp_service.process_statuses(db, inner)
            changed += whatsapp_service.process_inbound(db, inner)
    event.processed = True
    event.processed_at = datetime.now(timezone.utc)
    event.processing_error = None if changed else "no_matching_records"
    db.commit()
    return {"status": "ok", "processed": changed}


@router.post("/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    secret = settings_service.razorpay_credentials(db)["webhook_secret"]
    valid = hmac.compare_digest(
        hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest(), signature or ""
    ) if secret else False
    if not valid:
        # Store rejected attempts for security auditing without processing them.
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        db.add(
            WebhookEvent(
                provider="RAZORPAY",
                event_id=str(body.get("id") or hashlib.sha256(raw).hexdigest()),
                event_type=str(body.get("event", "unknown")),
                payload=body if isinstance(body, dict) else {},
                signature_valid=False,
                processed=True,
                processing_error="invalid_signature",
                processed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    event_id = str(payload.get("id") or hashlib.sha256(raw).hexdigest())
    existing = (
        db.query(WebhookEvent)
        .filter(WebhookEvent.provider == "RAZORPAY", WebhookEvent.event_id == event_id)
        .first()
    )
    if existing and existing.processed:
        return {"status": "duplicate"}
    event = existing or WebhookEvent(
        provider="RAZORPAY",
        event_id=event_id,
        event_type=str(payload.get("event", "unknown")),
        payload=payload,
        signature_valid=True,
    )
    db.add(event)
    db.commit()
    try:
        result = payment_service.create_from_webhook(db, payload, event_id)
        event.processed = True
        event.processed_at = datetime.now(timezone.utc)
        event.processing_error = None if result.get("status") != "skipped" else result.get("reason")
        db.commit()
        return {"status": "ok", **result}
    except Exception as exc:
        event.processed = False
        event.processing_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        db.commit()
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc
