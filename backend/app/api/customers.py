from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import client_info, get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas import (
    BalanceAdjustRequest,
    CustomerCreate,
    CustomerHistoryOut,
    CustomerOut,
    CustomerUpdate,
    Page,
    TransactionOut,
)
from app.services import balance_service, customer_service
from app.services import audit_service

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.get("", response_model=Page[CustomerOut])
def list_customers(
    search: str | None = None,
    status: str | None = None,
    outstanding: str | None = Query(default=None, pattern="^(all|pending|zero)$"),
    consent: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return customer_service.list_customers(
        db, search=search, status=status, outstanding=outstanding, consent=consent,
        page=page, page_size=page_size,
    )


@router.post("", response_model=CustomerOut, status_code=201)
def create_customer(
    payload: CustomerCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return customer_service._customer_out(customer_service.create(db, payload, actor=user))


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return customer_service._customer_out(customer_service.get(db, customer_id))


@router.put("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return customer_service._customer_out(customer_service.update(db, customer_id, payload, actor=user))


@router.delete("/{customer_id}", status_code=204)
def delete_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    customer_service.delete(db, customer_id, actor=user)


@router.post("/{customer_id}/archive", response_model=CustomerOut)
def archive_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return customer_service._customer_out(customer_service.archive(db, customer_id, actor=user))


@router.post("/{customer_id}/toggle-exclusion", response_model=CustomerOut)
def toggle_exclusion(
    customer_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return customer_service._customer_out(
        customer_service.toggle_reminder_exclusion(db, customer_id, actor=user)
    )


@router.get("/{customer_id}/history", response_model=CustomerHistoryOut)
def customer_history(
    customer_id: UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return customer_service.history(db, customer_id)


@router.post("/{customer_id}/adjustments", response_model=TransactionOut, status_code=201)
def adjust_balance(
    customer_id: UUID,
    payload: BalanceAdjustRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.transaction_type not in ("CHARGE", "ADJUSTMENT"):
        from app.core.errors import ValidationError

        raise ValidationError("Only CHARGE and ADJUSTMENT entries are allowed here")
    customer = customer_service.get(db, customer_id)
    info = client_info(request)
    txn = balance_service.add_transaction(
        db,
        customer,
        amount=payload.amount,
        transaction_type=payload.transaction_type,
        effect="INCREASE" if payload.transaction_type == "CHARGE" else "DECREASE",
        description=payload.description,
        actor_id=user.id,
        allow_negative=payload.allow_negative,
    )
    balance_service.log_balance_change(
        db,
        customer,
        user,
        "customer.balance_adjustment",
        {"amount": str(payload.amount), "balance": str(customer.outstanding_balance)},
    )
    return TransactionOut.model_validate(txn, from_attributes=True)
