"""Append-only balance ledger and authoritative outstanding balance calculation."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ValidationError
from app.models import BalanceTransaction, Customer, TransactionEffect, TransactionType
from app.services import audit_service


def decimals(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def customer_balance(db: Session, customer: Customer) -> Decimal:
    """Recalculate from ledger entries (never from unverified payments)."""
    total = decimals(customer.opening_balance)
    for txn in db.query(BalanceTransaction).filter(BalanceTransaction.customer_id == customer.id).all():
        if txn.transaction_type == TransactionType.OPENING.value:
            continue
        amount = decimals(txn.amount)
        total = total + amount if txn.effect == TransactionEffect.INCREASE.value else total - amount
    return max(total, Decimal("0.00")) if total < 0 else total


def recompute(db: Session, customer: Customer) -> Decimal:
    balance = customer_balance(db, customer)
    customer.outstanding_balance = balance
    customer.total_charges = sum(
        (decimals(t.amount) for t in customer.transactions if t.transaction_type == TransactionType.CHARGE.value),
        Decimal("0.00"),
    )
    customer.total_adjustments = sum(
        (
            decimals(t.amount)
            for t in customer.transactions
            if t.transaction_type == TransactionType.ADJUSTMENT.value
        ),
        Decimal("0.00"),
    )
    db.flush()
    return balance


def add_transaction(
    db: Session,
    customer: Customer,
    *,
    amount: Decimal | str | int,
    transaction_type: str,
    effect: str,
    description: str,
    reference: str | None = None,
    payment_id: UUID | None = None,
    actor_id: UUID | None = None,
    allow_negative: bool = False,
    extra_data: dict | None = None,
    occurred_at: datetime | None = None,
) -> BalanceTransaction:
    amount = decimals(amount)
    if amount <= 0:
        raise ValidationError("Transaction amount must be greater than zero")
    if effect not in (TransactionEffect.INCREASE.value, TransactionEffect.DECREASE.value):
        raise ValidationError("Invalid transaction effect")
    if transaction_type not in {t.value for t in TransactionType}:
        raise ValidationError("Invalid transaction type")

    current = decimals(customer.outstanding_balance)
    new_balance = current + amount if effect == TransactionEffect.INCREASE.value else current - amount
    if new_balance < 0 and not allow_negative:
        raise ConflictError(
            f"Adjustment would make the outstanding balance negative (₹{new_balance}). "
            "Enable explicit administrator approval to allow negative balances."
        )

    txn = BalanceTransaction(
        customer_id=customer.id,
        amount=amount,
        transaction_type=transaction_type,
        effect=effect,
        description=description,
        reference=reference,
        payment_id=payment_id,
        balance_after=new_balance,
        created_by=actor_id,
        extra_data=extra_data,
    )
    if occurred_at is not None:
        txn.created_at = occurred_at
    db.add(txn)
    db.flush()
    customer.outstanding_balance = new_balance
    if transaction_type == TransactionType.CHARGE.value:
        customer.total_charges = decimals(customer.total_charges) + amount
    elif transaction_type == TransactionType.ADJUSTMENT.value:
        customer.total_adjustments = decimals(customer.total_adjustments) + (
            amount if effect == TransactionEffect.INCREASE.value else -amount
        )
    elif transaction_type == TransactionType.PAYMENT.value:
        customer.total_paid = decimals(customer.total_paid) + amount
        customer.last_payment_at = occurred_at or datetime.now(timezone.utc)
    elif transaction_type == TransactionType.REVERSAL.value:
        customer.total_paid = max(decimals(customer.total_paid) - amount, Decimal("0.00"))
    db.flush()
    return txn


def opening_transaction(db: Session, customer: Customer, actor_id: UUID | None = None) -> None:
    amount = decimals(customer.opening_balance)
    if amount <= 0:
        return
    db.add(
        BalanceTransaction(
            customer_id=customer.id,
            amount=amount,
            transaction_type=TransactionType.OPENING.value,
            effect=TransactionEffect.INCREASE.value,
            description="Opening outstanding balance",
            balance_after=amount,
            created_by=actor_id,
        )
    )
    customer.outstanding_balance = amount
    db.flush()


def ledger(db: Session, customer_id: UUID, limit: int = 200) -> list[BalanceTransaction]:
    return (
        db.query(BalanceTransaction)
        .filter(BalanceTransaction.customer_id == customer_id)
        .order_by(BalanceTransaction.created_at.desc())
        .limit(limit)
        .all()
    )


def log_balance_change(
    db: Session, customer: Customer, actor: Any, action: str, after: dict[str, Any]
) -> None:
    audit_service.log(
        db,
        action=action,
        entity_type="CUSTOMER_BALANCE",
        entity_id=str(customer.id),
        before={"outstanding_balance": str(customer.outstanding_balance)},
        after=after,
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
