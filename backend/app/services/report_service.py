"""Dashboard aggregates and collection/reminder reporting."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    MessageType,
    Payment,
    PaymentMethod,
    PaymentStatus,
    WhatsAppMessage,
)
from app.services import balance_service, customer_service, payment_service


def _dec(value: Any) -> Decimal:
    return balance_service.decimals(value or 0)


def dashboard(db: Session) -> dict[str, Any]:
    total_outstanding = _dec(db.query(func.coalesce(func.sum(Customer.outstanding_balance), 0)).scalar())
    total_collected = _dec(
        db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.status == PaymentStatus.CONFIRMED.value
        ).scalar()
    )
    pending_amount = _dec(
        db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.status == PaymentStatus.AWAITING_CONFIRMATION.value
        ).scalar()
    )
    messages = db.query(WhatsAppMessage)
    reminders_sent = messages.filter(
        WhatsAppMessage.message_type.in_([MessageType.REMINDER.value, MessageType.PARTIAL.value])
    ).count()
    delivered = messages.filter(
        WhatsAppMessage.status.in_(["DELIVERED", "READ"])
    ).count()
    failed = messages.filter(WhatsAppMessage.status == "FAILED").count()

    recent_payments = (
        db.query(Payment).order_by(Payment.created_at.desc()).limit(6).all()
    )
    awaiting = (
        db.query(Payment)
        .filter(Payment.status == PaymentStatus.AWAITING_CONFIRMATION.value)
        .order_by(Payment.created_at.desc())
        .limit(6)
        .all()
    )
    top_customers = (
        db.query(Customer)
        .filter(Customer.outstanding_balance > 0)
        .order_by(Customer.outstanding_balance.desc())
        .limit(6)
        .all()
    )
    recent_messages = (
        db.query(WhatsAppMessage)
        .order_by(WhatsAppMessage.created_at.desc())
        .limit(6)
        .all()
    )
    failed_reminders = (
        db.query(WhatsAppMessage)
        .filter(WhatsAppMessage.status == "FAILED")
        .order_by(WhatsAppMessage.created_at.desc())
        .limit(6)
        .all()
    )
    customer_names = {c.id: c.name for c in db.query(Customer).all()}
    return {
        "stats": {
            "total_outstanding": str(total_outstanding),
            "total_collected": str(total_collected),
            "pending_amount": str(pending_amount),
            "pending_count": payment_service.pending_count(db),
            "total_customers": db.query(func.count(Customer.id)).scalar() or 0,
            "customers_with_outstanding": db.query(func.count(Customer.id))
            .filter(Customer.outstanding_balance > 0)
            .scalar()
            or 0,
            "reminders_sent": reminders_sent,
            "successful_deliveries": delivered,
            "failed_messages": failed,
        },
        "charts": {
            "daily_collections": collections_by_period(db, "day", 14),
            "weekly_collections": collections_by_period(db, "week", 8),
            "monthly_collections": collections_by_period(db, "month", 12),
            "outstanding_distribution": outstanding_distribution(db),
            "payment_methods": payment_methods(db),
        },
        "recent_payments": [_payment(p, customer_names) for p in recent_payments],
        "awaiting_verification": [_payment(p, customer_names) for p in awaiting],
        "top_customers": [
            {"id": str(c.id), "name": c.name, "outstanding_balance": str(c.outstanding_balance)}
            for c in top_customers
        ],
        "recent_messages": [
            {
                "id": str(m.id),
                "customer_name": customer_names.get(m.customer_id, ""),
                "message_type": m.message_type,
                "status": m.status,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "error": m.error,
            }
            for m in recent_messages
        ],
        "failed_reminders": [
            {
                "id": str(m.id),
                "customer_name": customer_names.get(m.customer_id, ""),
                "status": m.status,
                "error": m.error,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in failed_reminders
        ],
    }


def _payment(payment: Payment, names: dict) -> dict[str, Any]:
    return {
        "id": str(payment.id),
        "customer_name": names.get(payment.customer_id, ""),
        "amount": str(payment.amount),
        "method": payment.method,
        "status": payment.status,
        "payment_date": payment.payment_date.isoformat() if payment.payment_date else None,
        "gateway_payment_id": payment.gateway_payment_id,
    }


def collections_by_period(db: Session, period: str, count: int) -> list[dict[str, Any]]:
    today = datetime.now(timezone.utc).date()
    buckets: list[str] = []
    if period == "day":
        buckets = [(today - timedelta(days=i)).isoformat() for i in range(count)][::-1]
    elif period == "week":
        buckets = [
            f"W{(today - timedelta(weeks=i)).isocalendar().week}"
            for i in range(count)
        ][::-1]
    else:
        buckets = [
            (today.replace(day=1) - pd.DateOffset(months=i)).strftime("%Y-%m")
            for i in range(count)
        ][::-1]
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    counts: dict[str, int] = defaultdict(int)
    payments = (
        db.query(Payment)
        .filter(Payment.status == PaymentStatus.CONFIRMED.value)
        .all()
    )
    for payment in payments:
        date = payment.payment_date
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        if period == "day":
            key = date.date().isoformat()
        elif period == "week":
            iso = date.isocalendar()
            key = f"W{iso.week}"
        else:
            key = date.strftime("%Y-%m")
        if key in totals:
            totals[key] += _dec(payment.amount)
            counts[key] += 1
    return [
        {"label": key, "amount": str(totals[key]), "count": counts[key]} for key in buckets
    ]


def outstanding_distribution(db: Session) -> list[dict[str, Any]]:
    ranges = [
        ("0", 0.01, 1000),
        ("₹1,000–₹5,000", 1000.01, 5000),
        ("₹5,000–₹20,000", 5000.01, 20000),
        ("Above ₹20,000", 20000.01, Decimal("999999999")),
    ]
    result = []
    for label, low, high in ranges:
        count = (
            db.query(func.count(Customer.id))
            .filter(Customer.outstanding_balance >= low, Customer.outstanding_balance <= high)
            .scalar()
            or 0
        )
        amount = (
            db.query(func.coalesce(func.sum(Customer.outstanding_balance), 0))
            .filter(Customer.outstanding_balance >= low, Customer.outstanding_balance <= high)
            .scalar()
            or 0
        )
        result.append({"label": label, "customers": count, "amount": str(_dec(amount))})
    return result


def payment_methods(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(Payment.method, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == PaymentStatus.CONFIRMED.value)
        .group_by(Payment.method)
        .all()
    )
    return [
        {"method": method, "count": count, "amount": str(_dec(amount))}
        for method, count, amount in rows
    ]


def collections_report(
    db: Session, from_date: datetime | None = None, to_date: datetime | None = None
) -> pd.DataFrame:
    query = db.query(Payment, Customer.name, Customer.whatsapp_number).join(
        Customer, Payment.customer_id == Customer.id
    )
    if from_date:
        query = query.filter(Payment.payment_date >= from_date)
    if to_date:
        query = query.filter(Payment.payment_date <= to_date)
    rows = []
    for payment, name, phone in query.order_by(Payment.payment_date.desc()).all():
        rows.append(
            {
                "Date": payment.payment_date.strftime("%Y-%m-%d") if payment.payment_date else "",
                "Customer Name": name,
                "WhatsApp Number": phone,
                "Amount": payment.amount,
                "Method": payment.method,
                "Gateway Transaction ID": payment.gateway_payment_id or "",
                "Reference": payment.reference or "",
                "Status": payment.status,
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "Date",
            "Customer Name",
            "WhatsApp Number",
            "Amount",
            "Method",
            "Gateway Transaction ID",
            "Reference",
            "Status",
        ],
    )


def outstanding_report(db: Session, customer_id: UUID | None = None) -> pd.DataFrame:
    query = db.query(Customer)
    if customer_id:
        query = query.filter(Customer.id == customer_id)
    rows = [
        {
            "Customer Name": c.name,
            "WhatsApp Number": c.whatsapp_number,
            "Opening Balance": c.opening_balance,
            "Outstanding Balance": c.outstanding_balance,
            "Total Paid": c.total_paid,
            "Last Payment Date": c.last_payment_at.strftime("%Y-%m-%d") if c.last_payment_at else "",
            "Last Reminder Date": c.last_reminder_at.strftime("%Y-%m-%d") if c.last_reminder_at else "",
            "Status": c.status,
            "Reminder Excluded": "YES" if c.exclude_from_reminders else "NO",
            "Consent": c.consent_status,
        }
        for c in query.order_by(Customer.outstanding_balance.desc()).all()
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "Customer Name",
            "WhatsApp Number",
            "Opening Balance",
            "Outstanding Balance",
            "Total Paid",
            "Last Payment Date",
            "Last Reminder Date",
            "Status",
            "Reminder Excluded",
            "Consent",
        ],
    )


def whatsapp_report(
    db: Session, from_date: datetime | None = None, to_date: datetime | None = None
) -> pd.DataFrame:
    query = db.query(WhatsAppMessage, Customer.name).outerjoin(
        Customer, WhatsAppMessage.customer_id == Customer.id
    )
    if from_date:
        query = query.filter(WhatsAppMessage.created_at >= from_date)
    if to_date:
        query = query.filter(WhatsAppMessage.created_at <= to_date)
    rows = [
        {
            "Date": m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "",
            "Customer Name": name or "",
            "Phone": m.phone_number,
            "Type": m.message_type,
            "Template": m.template_name or "",
            "Status": m.status,
            "Provider Message ID": m.provider_message_id or "",
            "Error": m.error or "",
        }
        for m, name in query.order_by(WhatsAppMessage.created_at.desc()).all()
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "Date",
            "Customer Name",
            "Phone",
            "Type",
            "Template",
            "Status",
            "Provider Message ID",
            "Error",
        ],
    )


def payments_report(
    db: Session, status: str, from_date: datetime | None = None, to_date: datetime | None = None
) -> pd.DataFrame:
    query = db.query(Payment, Customer.name).join(Customer, Payment.customer_id == Customer.id)
    query = query.filter(Payment.status == status)
    if from_date:
        query = query.filter(Payment.created_at >= from_date)
    if to_date:
        query = query.filter(Payment.created_at <= to_date)
    rows = [
        {
            "Customer Name": name,
            "Amount": p.amount,
            "Method": p.method,
            "Status": p.status,
            "Gateway ID": p.gateway_payment_id or "",
            "Reference": p.reference or "",
            "Notes": p.notes or "",
            "Date": p.created_at.strftime("%Y-%m-%d") if p.created_at else "",
        }
        for p, name in query.order_by(Payment.created_at.desc()).all()
    ]
    return pd.DataFrame(
        rows,
        columns=["Customer Name", "Amount", "Method", "Status", "Gateway ID", "Reference", "Notes", "Date"],
    )
