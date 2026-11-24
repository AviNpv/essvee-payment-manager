"""Customer management, phone normalisation and validation."""

import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import (
    BalanceTransaction,
    ConsentStatus,
    Customer,
    CustomerStatus,
    Payment,
    WhatsAppMessage,
)
from app.schemas import CustomerCreate, CustomerHistoryOut, CustomerUpdate, MessageOut, Page, PaymentOut, TransactionOut
from app.services import audit_service, balance_service

PHONE_RE = re.compile(r"^[1-9]\d{9,14}$")


def normalize_phone(raw: str | None) -> str:
    if not raw:
        raise ValidationError("WhatsApp number is required")
    value = str(raw).strip()
    value = re.sub(r"[\s()\-]", "", value)
    if value.startswith("00"):
        value = "+" + value[2:]
    if value.startswith("+"):
        digits = value[1:]
    elif value.startswith("0") and len(value) >= 11:
        digits = value.lstrip("0")
    else:
        digits = value
    if digits.startswith("91") and len(digits) == 12:
        pass
    elif len(digits) == 10 and digits[0] in "6789":
        digits = "91" + digits
    elif len(digits) == 12 and digits.startswith("91"):
        pass
    else:
        # International numbers are accepted in E.164 form.
        pass
    if not PHONE_RE.fullmatch(digits):
        raise ValidationError(f"Invalid WhatsApp number: {raw}")
    return "+" + digits


def is_valid_phone(raw: str) -> bool:
    try:
        normalize_phone(raw)
        return True
    except ValidationError:
        return False


def get(db: Session, customer_id: UUID) -> Customer:
    customer = db.get(Customer, customer_id)
    if not customer:
        raise NotFoundError("Customer not found")
    return customer


def get_by_phone(db: Session, phone: str) -> Customer | None:
    return db.query(Customer).filter(Customer.whatsapp_number == normalize_phone(phone)).first()


def create(db: Session, data: CustomerCreate, actor: Any = None) -> Customer:
    phone = normalize_phone(data.whatsapp_number)
    if get_by_phone(db, phone):
        raise ConflictError(f"A customer with WhatsApp number {phone} already exists")
    status = (data.status or CustomerStatus.ACTIVE.value).upper()
    consent = (data.consent_status or ConsentStatus.OPT_IN.value).upper()
    if status not in {s.value for s in CustomerStatus}:
        raise ValidationError("Invalid customer status")
    if consent not in {c.value for c in ConsentStatus}:
        raise ValidationError("Invalid consent status")
    customer = Customer(
        name=data.name.strip(),
        whatsapp_number=phone,
        email=data.email,
        status=status,
        consent_status=consent,
        opening_balance=balance_service.decimals(data.opening_balance),
        outstanding_balance=balance_service.decimals(data.opening_balance),
        exclude_from_reminders=data.exclude_from_reminders,
        notes=data.notes,
        created_by=getattr(actor, "id", None),
    )
    db.add(customer)
    db.flush()
    balance_service.opening_transaction(db, customer, getattr(actor, "id", None))
    db.commit()
    db.refresh(customer)
    audit_service.log(
        db,
        action="customer.create",
        entity_type="CUSTOMER",
        entity_id=str(customer.id),
        after={"name": customer.name, "phone": phone, "opening_balance": str(customer.opening_balance)},
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return customer


def update(db: Session, customer_id: UUID, data: CustomerUpdate, actor: Any = None) -> Customer:
    customer = get(db, customer_id)
    before = {
        "name": customer.name,
        "whatsapp_number": customer.whatsapp_number,
        "email": customer.email,
        "status": customer.status,
        "consent_status": customer.consent_status,
        "exclude_from_reminders": customer.exclude_from_reminders,
        "notes": customer.notes,
    }
    payload = data.model_dump(exclude_unset=True)
    if "whatsapp_number" in payload and payload["whatsapp_number"]:
        phone = normalize_phone(payload["whatsapp_number"])
        duplicate = db.query(Customer).filter(
            Customer.whatsapp_number == phone, Customer.id != customer.id
        ).first()
        if duplicate:
            raise ConflictError(f"A customer with WhatsApp number {phone} already exists")
        customer.whatsapp_number = phone
    if "name" in payload and payload["name"]:
        customer.name = " ".join(payload["name"].split())
    if "email" in payload:
        customer.email = payload["email"].lower() if payload["email"] else None
    if "status" in payload:
        status = str(payload["status"]).upper()
        if status not in {s.value for s in CustomerStatus}:
            raise ValidationError("Invalid customer status")
        customer.status = status
    if "consent_status" in payload:
        consent = str(payload["consent_status"]).upper()
        if consent not in {c.value for c in ConsentStatus}:
            raise ValidationError("Invalid consent status")
        customer.consent_status = consent
    if "exclude_from_reminders" in payload and payload["exclude_from_reminders"] is not None:
        customer.exclude_from_reminders = payload["exclude_from_reminders"]
    if "notes" in payload:
        customer.notes = payload["notes"]
    db.commit()
    db.refresh(customer)
    audit_service.log(
        db,
        action="customer.update",
        entity_type="CUSTOMER",
        entity_id=str(customer.id),
        before=before,
        after={
            "name": customer.name,
            "whatsapp_number": customer.whatsapp_number,
            "status": customer.status,
            "consent_status": customer.consent_status,
            "exclude_from_reminders": customer.exclude_from_reminders,
        },
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return customer


def archive(db: Session, customer_id: UUID, actor: Any = None) -> Customer:
    customer = get(db, customer_id)
    customer.status = CustomerStatus.ARCHIVED.value
    customer.exclude_from_reminders = True
    db.commit()
    db.refresh(customer)
    audit_service.log(
        db,
        action="customer.archive",
        entity_type="CUSTOMER",
        entity_id=str(customer.id),
        after={"status": customer.status},
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return customer


def delete(db: Session, customer_id: UUID, actor: Any = None) -> None:
    """Hard delete is only permitted when the customer has no financial history."""
    customer = get(db, customer_id)
    if customer.payments or customer.transactions:
        raise ConflictError(
            "Customer has financial history. Archive the customer instead of deleting it."
        )
    db.delete(customer)
    db.commit()
    audit_service.log(
        db,
        action="customer.delete",
        entity_type="CUSTOMER",
        entity_id=str(customer_id),
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )


def list_customers(
    db: Session,
    *,
    search: str | None = None,
    status: str | None = None,
    outstanding: str | None = None,  # all | pending | zero
    consent: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> Page[Any]:
    query = db.query(Customer)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(or_(Customer.name.ilike(term), Customer.whatsapp_number.ilike(term)))
    if status:
        query = query.filter(Customer.status == status.upper())
    if consent:
        query = query.filter(Customer.consent_status == consent.upper())
    if outstanding == "pending":
        query = query.filter(Customer.outstanding_balance > 0)
    elif outstanding == "zero":
        query = query.filter(Customer.outstanding_balance <= 0)
    total = query.count()
    items = (
        query.order_by(Customer.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page(
        items=[_customer_out(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, (total + page_size - 1) // page_size),
    )


def _customer_out(customer: Customer) -> Any:
    from app.schemas import CustomerOut

    return CustomerOut.model_validate(customer, from_attributes=True)


def history(db: Session, customer_id: UUID, limit: int = 100) -> CustomerHistoryOut:
    customer = get(db, customer_id)
    txns = (
        db.query(BalanceTransaction)
        .filter(BalanceTransaction.customer_id == customer.id)
        .order_by(BalanceTransaction.created_at.desc())
        .limit(limit)
        .all()
    )
    payments = (
        db.query(Payment)
        .filter(Payment.customer_id == customer.id)
        .order_by(Payment.created_at.desc())
        .limit(limit)
        .all()
    )
    messages = (
        db.query(WhatsAppMessage)
        .filter(WhatsAppMessage.customer_id == customer.id)
        .order_by(WhatsAppMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    return CustomerHistoryOut(
        transactions=[TransactionOut.model_validate(t, from_attributes=True) for t in txns],
        payments=[
            PaymentOut.model_validate(p, from_attributes=True).model_copy(
                update={"customer_name": customer.name, "customer_phone": customer.whatsapp_number}
            )
            for p in payments
        ],
        messages=[
            MessageOut.model_validate(m, from_attributes=True).model_copy(
                update={"customer_name": customer.name}
            )
            for m in messages
        ],
    )


def toggle_reminder_exclusion(db: Session, customer_id: UUID, actor: Any = None) -> Customer:
    customer = get(db, customer_id)
    customer.exclude_from_reminders = not customer.exclude_from_reminders
    db.commit()
    db.refresh(customer)
    audit_service.log(
        db,
        action="customer.toggle_reminder_exclusion",
        entity_type="CUSTOMER",
        entity_id=str(customer.id),
        after={"exclude_from_reminders": customer.exclude_from_reminders},
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return customer


def stats(db: Session) -> dict[str, Any]:
    total = db.query(func.count(Customer.id)).scalar() or 0
    outstanding = db.query(func.coalesce(func.sum(Customer.outstanding_balance), 0)).scalar() or 0
    pending_count = (
        db.query(func.count(Customer.id)).filter(Customer.outstanding_balance > 0).scalar() or 0
    )
    return {
        "total_customers": total,
        "total_outstanding": balance_service.decimals(outstanding),
        "customers_with_outstanding": pending_count,
    }
