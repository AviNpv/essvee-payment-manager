"""Reminder eligibility, bulk dispatch and database-backed scheduler jobs."""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings as env
from app.core.errors import ValidationError
from app.core.security import create_access_token, decode_access_token
from app.models import (
    ConsentStatus,
    Customer,
    CustomerStatus,
    MessageStatus,
    Payment,
    PaymentLink,
    PaymentLinkStatus,
    PaymentStatus,
    SchedulerRun,
    WhatsAppMessage,
)
from app.services import (
    customer_service,
    payment_service,
    razorpay_service,
    settings_service,
    whatsapp_service,
)

IST = timezone(timedelta(hours=5, minutes=30))
TOKEN_ISSUER = "essvee-bulk-confirmation"


def now_ist() -> datetime:
    return datetime.now(IST)


def is_quiet_hours(db: Session) -> bool:
    config = settings_service.get_reminder(db)
    current = now_ist().strftime("%H:%M")
    start, end = config.quiet_start, config.quiet_end
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def _eligibility_reason(db: Session, customer: Customer, config: Any, frequency_days: int) -> tuple[bool, str]:
    if customer.outstanding_balance <= 0:
        return False, "zero_balance"
    if customer.status != CustomerStatus.ACTIVE.value:
        return False, "inactive"
    if customer.consent_status != ConsentStatus.OPT_IN.value:
        return False, "no_consent"
    if customer.exclude_from_reminders:
        return False, "excluded"
    if not customer_service.is_valid_phone(customer.whatsapp_number):
        return False, "invalid_phone"
    pending = (
        db.query(func.count(Payment.id))
        .filter(
            Payment.customer_id == customer.id,
            Payment.status == PaymentStatus.AWAITING_CONFIRMATION.value,
        )
        .scalar()
        or 0
    )
    if pending:
        return False, "awaiting_confirmation"
    if customer.reminder_count >= config.max_reminders:
        return False, "max_reminders_reached"
    if customer.last_reminder_at:
        if customer.last_reminder_at.tzinfo is None:
            last = customer.last_reminder_at.replace(tzinfo=timezone.utc)
        else:
            last = customer.last_reminder_at
        if datetime.now(timezone.utc) - last < timedelta(days=frequency_days):
            return False, "within_interval"
    else:
        created = customer.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created < timedelta(days=config.first_reminder_after_days):
            return False, "too_early"
    return True, "eligible"


def eligible_customers(
    db: Session,
    *,
    frequency_days: int | None = None,
    max_reminders: int | None = None,
    daily_limit: int | None = None,
    respect_quiet_hours: bool = True,
) -> dict[str, Any]:
    config = settings_service.get_reminder(db)
    freq = frequency_days if frequency_days is not None else config.frequency_days
    if freq < 1:
        raise ValidationError("Reminder frequency must be at least 1 day")
    limit = daily_limit if daily_limit is not None else config.daily_limit
    max_reminders_value = max_reminders if max_reminders is not None else config.max_reminders
    breakdown: dict[str, int] = {}
    eligible: list[Customer] = []
    for customer in db.query(Customer).all():
        ok, reason = _eligibility_reason(db, customer, config, freq)
        if max_reminders_value is not None and customer.reminder_count >= max_reminders_value:
            ok, reason = False, "max_reminders_reached"
        breakdown[reason] = breakdown.get(reason, 0) + 1
        if ok:
            eligible.append(customer)
    remaining_quota = max(0, limit - whatsapp_service._daily_usage(db))
    eligible = eligible[:remaining_quota]
    return {
        "customers": eligible,
        "reason_breakdown": breakdown,
        "remaining_quota": remaining_quota,
        "quiet_hours": is_quiet_hours(db) if respect_quiet_hours else False,
    }


def issue_confirmation_token(customer_ids: list[UUID]) -> str:
    payload = {
        "iss": TOKEN_ISSUER,
        "ids": [str(i) for i in customer_ids],
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    return create_access_token("bulk", expires_delta=timedelta(minutes=10)) + "." + base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode("utf-8")


def validate_confirmation_token(token: str) -> list[UUID]:
    try:
        jwt_part, payload_part = token.rsplit(".", 1)
        decode_access_token(jwt_part)
        payload = json.loads(base64.urlsafe_b64decode(payload_part.encode("utf-8")))
        if payload.get("iss") != TOKEN_ISSUER or payload.get("exp", 0) < time.time():
            raise ValueError("expired")
        return [UUID(value) for value in payload["ids"]]
    except Exception as exc:
        raise ValidationError("Bulk-send confirmation token is invalid or has expired") from exc


def preview(db: Session, data: Any) -> dict[str, Any]:
    result = eligible_customers(
        db,
        frequency_days=data.frequency_days,
        max_reminders=data.max_reminders,
        daily_limit=data.daily_limit,
        respect_quiet_hours=data.respect_quiet_hours,
    )
    customers = result["customers"]
    token = issue_confirmation_token([c.id for c in customers])
    return {
        "eligible_count": len(customers),
        "estimated_messages": len(customers),
        "total_outstanding": sum(
            (Decimal(str(c.outstanding_balance)) for c in customers), Decimal("0")
        ),
        "excluded_count": sum(
            v for k, v in result["reason_breakdown"].items() if k != "eligible"
        ),
        "reason_breakdown": result["reason_breakdown"],
        "customers": [
            {
                "id": str(c.id),
                "name": c.name,
                "whatsapp_number": c.whatsapp_number,
                "outstanding_balance": str(c.outstanding_balance),
                "reminder_count": c.reminder_count,
            }
            for c in customers[:50]
        ],
        "confirmation_token": token,
        "quiet_hours": result["quiet_hours"],
    }


def send_bulk(db: Session, token: str, customer_ids: list[UUID] | None = None) -> dict[str, Any]:
    allowed = set(validate_confirmation_token(token))
    if customer_ids:
        requested = set(customer_ids)
        if not requested.issubset(allowed):
            raise ValidationError("Some selected customers were not part of the preview")
        targets = [cid for cid in allowed if cid in requested]
    else:
        targets = list(allowed)
    if is_quiet_hours(db):
        raise ValidationError(
            "Quiet hours are active (21:00–09:00 IST). Bulk reminders are paused."
        )
    sent, failed, skipped = 0, 0, 0
    run = SchedulerRun(job_name="manual_bulk_reminder", status="RUNNING")
    db.add(run)
    db.flush()
    for customer_id in targets:
        customer = db.get(Customer, customer_id)
        if not customer:
            skipped += 1
            continue
        ok, _ = _eligibility_reason(db, customer, settings_service.get_reminder(db), settings_service.get_reminder(db).frequency_days)
        if not ok:
            skipped += 1
            continue
        before_ids = whatsapp_service.existing_message_ids(db, customer.id)
        try:
            link = payment_service.ensure_payment_link(db, customer, actor=None)
            message = whatsapp_service.send_reminder(
                db, customer, link.url, idempotency_suffix=str(link.id)
            )
            if message.status == MessageStatus.FAILED.value:
                failed += 1
            else:
                sent += 1
                if message.id not in before_ids:
                    customer.reminder_count += 1
                    customer.last_reminder_at = datetime.now(timezone.utc)
        except Exception:
            failed += 1
    db.commit()
    run.status = "COMPLETED"
    run.processed = sent + failed + skipped
    run.failed = failed
    run.finished_at = datetime.now(timezone.utc)
    run.detail = {"sent": sent, "failed": failed, "skipped": skipped}
    db.commit()
    return {"sent": sent, "failed": failed, "skipped": skipped}


def run_reminder_job(db: Session) -> dict[str, Any]:
    config = settings_service.get_reminder(db)
    run = SchedulerRun(job_name="automated_reminders", status="RUNNING")
    db.add(run)
    db.flush()
    if not config.automation_enabled:
        run.status = "SKIPPED"
        run.finished_at = datetime.now(timezone.utc)
        run.detail = {"reason": "automation_disabled"}
        db.commit()
        return run.detail
    if is_quiet_hours(db):
        run.status = "SKIPPED"
        run.finished_at = datetime.now(timezone.utc)
        run.detail = {"reason": "quiet_hours"}
        db.commit()
        return run.detail
    result = eligible_customers(db)
    sent, failed = 0, 0
    for customer in result["customers"]:
        before_ids = whatsapp_service.existing_message_ids(db, customer.id)
        try:
            link = payment_service.ensure_payment_link(db, customer)
            message = whatsapp_service.send_reminder(
                db, customer, link.url, idempotency_suffix=str(link.id)
            )
            if message.status == MessageStatus.FAILED.value:
                failed += 1
            else:
                sent += 1
                if message.id not in before_ids:
                    customer.reminder_count += 1
                    customer.last_reminder_at = datetime.now(timezone.utc)
        except Exception:
            failed += 1
    db.commit()
    run.status = "COMPLETED"
    run.processed = sent + failed
    run.failed = failed
    run.finished_at = datetime.now(timezone.utc)
    run.detail = {
        "sent": sent,
        "failed": failed,
        "remaining_quota": result["remaining_quota"],
    }
    db.commit()
    return run.detail


def run_retry_job(db: Session) -> dict[str, Any]:
    config = settings_service.get_reminder(db)
    run = SchedulerRun(job_name="failed_message_retry", status="RUNNING")
    db.add(run)
    db.flush()
    if not config.retry_failed_messages:
        run.status = "SKIPPED"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return {"skipped": True}
    failed = (
        db.query(WhatsAppMessage)
        .filter(
            WhatsAppMessage.status == MessageStatus.FAILED.value,
            WhatsAppMessage.direction == "OUTBOUND",
            WhatsAppMessage.retry_count < config.max_retry_attempts,
        )
        .limit(50)
        .all()
    )
    ok = 0
    for message in failed:
        try:
            updated = whatsapp_service.retry_failed(db, message.id)
            if updated.status != MessageStatus.FAILED.value:
                ok += 1
        except Exception:
            pass
    run.status = "COMPLETED"
    run.processed = len(failed)
    run.failed = len(failed) - ok
    run.finished_at = datetime.now(timezone.utc)
    run.detail = {"attempted": len(failed), "recovered": ok}
    db.commit()
    return run.detail


def run_link_reconcile_job(db: Session) -> dict[str, Any]:
    run = SchedulerRun(job_name="payment_link_reconcile", status="RUNNING")
    db.add(run)
    db.flush()
    checked = expired = created = 0
    now = datetime.now(timezone.utc)
    for link in (
        db.query(PaymentLink)
        .filter(PaymentLink.status == PaymentLinkStatus.ACTIVE.value)
        .all()
    ):
        if link.expires_at and link.expires_at <= now:
            link.status = PaymentLinkStatus.EXPIRED.value
            expired += 1
            continue
        if payment_service.link_provider(link) != "RAZORPAY":
            continue
        if not razorpay_service.is_configured(db):
            continue
        checked += 1
        try:
            remote = razorpay_service.fetch_link(db, link.razorpay_link_id)
        except Exception:
            continue
        remote_status = str(remote.get("status", "")).lower()
        if remote_status in ("paid", "partially_paid"):
            result = payment_service.create_from_webhook(
                db,
                {
                    "event": f"payment_link.{remote_status}",
                    "payload": {"payment_link": {"entity": remote}},
                },
                f"reconcile:{link.razorpay_link_id}:{remote_status}",
            )
            if result.get("status") == "created":
                created += 1
    db.commit()
    run.status = "COMPLETED"
    run.processed = checked
    run.finished_at = datetime.now(timezone.utc)
    run.detail = {"checked": checked, "expired": expired, "pending_created": created}
    db.commit()
    return run.detail
