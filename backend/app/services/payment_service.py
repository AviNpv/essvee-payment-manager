"""Payment links, webhook reconciliation and administrator-controlled verification."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.errors import (
    ConfigurationError,
    ConflictError,
    NotFoundError,
    ProviderError,
    ValidationError,
)
from app.models import (
    BalanceTransaction,
    Customer,
    Payment,
    PaymentLink,
    PaymentLinkStatus,
    PaymentMethod,
    PaymentStatus,
    TransactionEffect,
    TransactionType,
)
from app.schemas import ManualPaymentCreate, Page, PaymentLinkOut, PaymentOut
from app.services import (
    audit_service,
    balance_service,
    customer_service,
    razorpay_service,
    settings_service,
    upi_service,
    whatsapp_service,
)


def _link_out(link: PaymentLink, customer: Customer | None = None) -> PaymentLinkOut:
    return PaymentLinkOut.model_validate(link, from_attributes=True).model_copy(
        update={
            "customer_name": customer.name if customer else None,
            "provider": link_provider(link),
        }
    )


def _payment_out(payment: Payment) -> PaymentOut:
    customer = payment.customer
    return PaymentOut.model_validate(payment, from_attributes=True).model_copy(
        update={
            "customer_name": customer.name if customer else None,
            "customer_phone": customer.whatsapp_number if customer else None,
        }
    )


def link_provider(link: PaymentLink) -> str:
    return "UPI" if link.razorpay_link_id.startswith("upi:") else "RAZORPAY"


def link_by_public_token(db: Session, token: str) -> PaymentLink | None:
    return (
        db.query(PaymentLink)
        .filter(PaymentLink.razorpay_link_id == f"upi:{token}")
        .first()
    )


def recent_links(db: Session, customer_id: UUID | None = None, limit: int = 100) -> list[PaymentLinkOut]:
    query = db.query(PaymentLink, Customer).join(Customer, PaymentLink.customer_id == Customer.id)
    if customer_id:
        query = query.filter(PaymentLink.customer_id == customer_id)
    rows = query.order_by(PaymentLink.created_at.desc()).limit(limit).all()
    return [_link_out(link, customer) for link, customer in rows]


def get_link(db: Session, link_id: UUID) -> PaymentLink:
    link = db.get(PaymentLink, link_id)
    if not link:
        raise NotFoundError("Payment link not found")
    return link


def _cancel_existing_link(db: Session, link: PaymentLink) -> None:
    if link_provider(link) == "UPI":
        link.status = PaymentLinkStatus.CANCELLED.value
        return
    try:
        razorpay_service.cancel_payment_link(db, link.razorpay_link_id)
    except Exception:
        # Provider may be unreachable; still mark the local link inactive.
        pass
    link.status = PaymentLinkStatus.CANCELLED.value


def ensure_payment_link(
    db,
    customer: Customer,
    *,
    amount: Decimal | None = None,
    force_new: bool = False,
    note: str | None = None,
    actor: Any = None,
) -> PaymentLink:
    """Reuse a valid link; otherwise create exactly one link for the active provider."""
    balance = balance_service.decimals(customer.outstanding_balance)
    if balance <= 0:
        raise ValidationError("Cannot generate a payment link for a zero-balance customer")
    target = balance_service.decimals(amount) if amount is not None else balance
    if target <= 0 or target > balance:
        raise ValidationError("Link amount must be positive and not exceed the outstanding balance")
    reminder = settings_service.get_reminder(db)
    now = datetime.now(timezone.utc)
    provider = settings_service.upi_credentials(db)["payment_provider"]
    if provider not in ("UPI", "RAZORPAY"):
        raise ConfigurationError("Payment provider must be UPI or RAZORPAY")

    if not force_new:
        candidates = (
            db.query(PaymentLink)
            .filter(
                PaymentLink.customer_id == customer.id,
                PaymentLink.status == PaymentLinkStatus.ACTIVE.value,
                PaymentLink.amount == target,
                or_(PaymentLink.expires_at.is_(None), PaymentLink.expires_at > now),
            )
            .order_by(PaymentLink.created_at.desc())
            .all()
        )
        for candidate in candidates:
            if link_provider(candidate) == provider:
                return candidate

    # Cancel obsolete active links before issuing a new one.
    obsolete = (
        db.query(PaymentLink)
        .filter(
            PaymentLink.customer_id == customer.id,
            PaymentLink.status == PaymentLinkStatus.ACTIVE.value,
        )
        .all()
    )
    for old in obsolete:
        _cancel_existing_link(db, old)
    db.flush()

    if provider == "UPI":
        if not upi_service.is_configured(db):
            raise ConflictError(
                "UPI is not configured. Add a UPI ID/VPA and public base URL in Settings → UPI before generating payment links."
            )
        result = upi_service.create_payment_link(
            db, customer, target, reminder=reminder, note=note
        )
        extra_data = {
            "provider": "UPI",
            "upi_uri": result["upi_uri"],
            "amount_mode": result["amount_mode"],
            "note": result["note"],
        }
        audit_after = {
            "customer_id": str(customer.id),
            "amount": str(target),
            "provider": "UPI",
            "upi_token": result["token"],
        }
    else:
        if not razorpay_service.is_configured(db):
            raise ConflictError(
                "Razorpay is not configured. Add credentials in Settings → Razorpay before generating payment links."
            )
        result = razorpay_service.create_payment_link(
            db, customer, target, reminder=reminder, note=note
        )
        extra_data = {"provider": "RAZORPAY", "mode": razorpay_service.credentials(db)["mode"]}
        audit_after = {
            "customer_id": str(customer.id),
            "amount": str(target),
            "provider": "RAZORPAY",
            "razorpay_id": result.get("id"),
        }

    provider_id = result.get("id")
    short_url = result.get("short_url") or result.get("url")
    if not provider_id or not short_url:
        raise ProviderError(f"{provider.title()} did not return a payment link ID and URL")
    expires_at = None
    if result.get("expire_by"):
        expires_at = datetime.fromtimestamp(int(result["expire_by"]), tz=timezone.utc)
    link = PaymentLink(
        customer_id=customer.id,
        razorpay_link_id=str(provider_id),
        url=str(short_url),
        amount=target,
        status=PaymentLinkStatus.ACTIVE.value,
        expires_at=expires_at,
        extra_data=extra_data,
    )
    db.add(link)
    db.flush()
    audit_service.log(
        db,
        action="payment_link.create",
        entity_type="PAYMENT_LINK",
        entity_id=str(link.id),
        after=audit_after,
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=False,
    )
    db.commit()
    db.refresh(link)
    return link


def cancel_link(db: Session, link_id: UUID, actor: Any = None) -> PaymentLink:
    link = get_link(db, link_id)
    if link.status != PaymentLinkStatus.ACTIVE.value:
        raise ValidationError("Only active payment links can be cancelled")
    if link_provider(link) == "UPI":
        link.status = PaymentLinkStatus.CANCELLED.value
    else:
        try:
            razorpay_service.cancel_payment_link(db, link.razorpay_link_id)
        except ProviderError as exc:
            raise ProviderError(f"Could not cancel link at Razorpay: {exc}") from exc
        link.status = PaymentLinkStatus.CANCELLED.value
    db.commit()
    db.refresh(link)
    audit_service.log(
        db,
        action="payment_link.cancel",
        entity_type="PAYMENT_LINK",
        entity_id=str(link.id),
        after={"status": link.status, "provider": link_provider(link)},
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return link


def create_manual(db: Session, data: ManualPaymentCreate, actor: Any = None) -> Payment:
    customer = customer_service.get(db, data.customer_id)
    if data.method not in {m.value for m in PaymentMethod}:
        raise ValidationError("Invalid payment method")
    if customer.outstanding_balance <= 0:
        raise ValidationError("Customer has no outstanding balance")
    payment = Payment(
        customer_id=customer.id,
        amount=balance_service.decimals(data.amount),
        method=data.method,
        status=PaymentStatus.AWAITING_CONFIRMATION.value,
        reference=data.reference,
        notes=data.notes,
        payment_date=data.payment_date or datetime.now(timezone.utc),
        extra_data={"entered_by": str(getattr(actor, "id", ""))},
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    audit_service.log(
        db,
        action="payment.manual_entry",
        entity_type="PAYMENT",
        entity_id=str(payment.id),
        after={"customer_id": str(customer.id), "amount": str(payment.amount), "method": payment.method},
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return payment


def create_from_webhook(db: Session, payload: dict[str, Any], event_id: str) -> dict[str, Any]:
    """Create a pending payment from a verified Razorpay webhook (idempotent)."""
    body = payload.get("payload", {}) if isinstance(payload, dict) else {}
    payment_entity = (body.get("payment") or {}).get("entity") or {}
    link_entity = (body.get("payment_link") or {}).get("entity") or {}
    event_type = str(payload.get("event", ""))
    gateway_id = payment_entity.get("id")
    link_razorpay_id = (
        (payment_entity.get("link_id"))
        or link_entity.get("id")
        or (payment_entity.get("notes") or {}).get("payment_link_id")
    )
    link = None
    if link_razorpay_id:
        link = (
            db.query(PaymentLink)
            .filter(PaymentLink.razorpay_link_id == str(link_razorpay_id))
            .first()
        )
    notes = payment_entity.get("notes") or link_entity.get("notes") or {}
    customer = None
    if link:
        customer = db.get(Customer, link.customer_id)
    if not customer and notes.get("customer_id"):
        customer = db.get(Customer, UUID(str(notes["customer_id"])))
    if not customer:
        return {"status": "skipped", "reason": "customer_not_found", "gateway_payment_id": gateway_id}

    if gateway_id:
        existing = (
            db.query(Payment)
            .filter(Payment.gateway_payment_id == str(gateway_id))
            .first()
        )
        if existing:
            return {"status": "duplicate", "payment_id": str(existing.id)}

    raw_amount = payment_entity.get("amount") or link_entity.get("amount")
    amount = (
        balance_service.decimals(Decimal(str(raw_amount)) / 100)
        if raw_amount is not None
        else balance_service.decimals(link.amount if link else 0)
    )
    if amount <= 0:
        return {"status": "skipped", "reason": "zero_amount", "gateway_payment_id": gateway_id}

    failed = event_type.endswith(".failed") or payment_entity.get("status") == "failed"
    status = PaymentStatus.FAILED.value if failed else PaymentStatus.AWAITING_CONFIRMATION.value
    payment = Payment(
        customer_id=customer.id,
        amount=amount,
        method=PaymentMethod.RAZORPAY.value,
        status=status,
        gateway_payment_id=str(gateway_id) if gateway_id else None,
        gateway_order_id=payment_entity.get("order_id"),
        payment_link_id=link.id if link else None,
        reference=link.razorpay_link_id if link else None,
        notes=str(notes.get("note") or "")[:500] or None,
        payment_date=datetime.fromtimestamp(
            int(payment_entity.get("created_at") or datetime.now(timezone.utc).timestamp()),
            tz=timezone.utc,
        ) if payment_entity.get("created_at") else datetime.now(timezone.utc),
        failure_reason=str((payment_entity.get("error_description") or ""))[:500] or None,
        extra_data={"event_type": event_type, "webhook_event_id": event_id, "verified": True},
    )
    db.add(payment)
    db.flush()
    if link and status == PaymentStatus.AWAITING_CONFIRMATION.value:
        link.attempts += 1
    db.commit()
    db.refresh(payment)
    audit_service.log(
        db,
        action="payment.webhook_received",
        entity_type="PAYMENT",
        entity_id=str(payment.id),
        after={"status": status, "amount": str(amount), "event": event_type},
        commit=True,
    )
    return {"status": "created", "payment_id": str(payment.id), "amount": str(amount)}


def get_payment(db: Session, payment_id: UUID) -> Payment:
    payment = db.get(Payment, payment_id)
    if not payment:
        raise NotFoundError("Payment not found")
    return payment


def confirm(
    db: Session, payment_id: UUID, actor: Any = None, *, send_confirmation: bool = True
) -> dict[str, Any]:
    payment = get_payment(db, payment_id)
    if payment.status != PaymentStatus.AWAITING_CONFIRMATION.value:
        raise ConflictError("Only payments awaiting confirmation can be confirmed")
    customer = customer_service.get(db, payment.customer_id)
    amount = balance_service.decimals(payment.amount)
    txn = balance_service.add_transaction(
        db,
        customer,
        amount=amount,
        transaction_type=TransactionType.PAYMENT.value,
        effect=TransactionEffect.DECREASE.value,
        description=f"Confirmed payment ({payment.method})",
        reference=payment.reference or payment.gateway_payment_id,
        payment_id=payment.id,
        actor_id=getattr(actor, "id", None),
        allow_negative=False,
    )
    payment.status = PaymentStatus.CONFIRMED.value
    payment.verified_by = getattr(actor, "id", None)
    payment.verified_at = datetime.now(timezone.utc)
    if payment.payment_link_id:
        link = db.get(PaymentLink, payment.payment_link_id)
        if link:
            link.status = (
                PaymentLinkStatus.PARTIALLY_PAID.value
                if customer.outstanding_balance > 0
                else PaymentLinkStatus.PAID.value
            )
    db.commit()
    db.refresh(payment)
    db.refresh(customer)
    audit_service.log(
        db,
        action="payment.confirm",
        entity_type="PAYMENT",
        entity_id=str(payment.id),
        after={
            "status": payment.status,
            "amount": str(amount),
            "balance_after": str(customer.outstanding_balance),
            "ledger_id": str(txn.id),
        },
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    message_status = "skipped"
    if send_confirmation:
        try:
            message = whatsapp_service.send_confirmation(
                db, customer, amount, customer.outstanding_balance, payment.id
            )
            message_status = message.status
        except (ValidationError, ProviderError, ConfigurationError) as exc:
            # Financial state remains correct; the failed notification is visible
            # in message history and the dashboard instead of being silently ignored.
            message_status = f"failed: {exc}"
    return {
        "payment": _payment_out(payment),
        "balance_after": customer.outstanding_balance,
        "message_status": message_status,
    }


def reject(db: Session, payment_id: UUID, reason: str, actor: Any = None) -> Payment:
    payment = get_payment(db, payment_id)
    if payment.status != PaymentStatus.AWAITING_CONFIRMATION.value:
        raise ConflictError("Only pending payments can be rejected")
    payment.status = PaymentStatus.REJECTED.value
    payment.rejection_reason = reason
    payment.verified_by = getattr(actor, "id", None)
    payment.verified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(payment)
    audit_service.log(
        db,
        action="payment.reject",
        entity_type="PAYMENT",
        entity_id=str(payment.id),
        after={"status": payment.status, "reason": reason},
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return payment


def reverse(db: Session, payment_id: UUID, reason: str, actor: Any = None) -> Payment:
    payment = get_payment(db, payment_id)
    if payment.status != PaymentStatus.CONFIRMED.value:
        raise ConflictError("Only confirmed payments can be reversed")
    customer = customer_service.get(db, payment.customer_id)
    txn = balance_service.add_transaction(
        db,
        customer,
        amount=payment.amount,
        transaction_type=TransactionType.REVERSAL.value,
        effect=TransactionEffect.INCREASE.value,
        description=f"Reversal of confirmed payment: {reason}",
        reference=str(payment.id),
        payment_id=payment.id,
        actor_id=getattr(actor, "id", None),
    )
    payment.status = PaymentStatus.REVERSED.value
    payment.rejection_reason = f"Reversed: {reason}"
    db.commit()
    db.refresh(payment)
    audit_service.log(
        db,
        action="payment.reverse",
        entity_type="PAYMENT",
        entity_id=str(payment.id),
        after={"status": payment.status, "reason": reason, "ledger_id": str(txn.id)},
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return payment


def list_payments(
    db: Session,
    *,
    status: str | None = None,
    method: str | None = None,
    customer_id: UUID | None = None,
    search: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = 1,
    page_size: int = 25,
) -> Page[PaymentOut]:
    query = db.query(Payment).join(Customer, Payment.customer_id == Customer.id)
    if status:
        query = query.filter(Payment.status == status.upper())
    if method:
        query = query.filter(Payment.method == method.upper())
    if customer_id:
        query = query.filter(Payment.customer_id == customer_id)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(Customer.name.ilike(term), Customer.whatsapp_number.ilike(term), Payment.gateway_payment_id.ilike(term))
        )
    if from_date:
        query = query.filter(Payment.payment_date >= from_date)
    if to_date:
        query = query.filter(Payment.payment_date <= to_date)
    total = query.count()
    rows = (
        query.order_by(Payment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(
        items=[_payment_out(p) for p in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, (total + page_size - 1) // page_size),
    )


def pending_count(db: Session) -> int:
    return (
        db.query(func.count(Payment.id))
        .filter(Payment.status == PaymentStatus.AWAITING_CONFIRMATION.value)
        .scalar()
        or 0
    )
