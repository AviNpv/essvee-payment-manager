from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas import (
    ManualPaymentCreate,
    Page,
    PaymentLinkGenerateRequest,
    PaymentLinkOut,
    PaymentOut,
    PaymentRejectRequest,
    PaymentReverseRequest,
)
from app.services import payment_service

router = APIRouter(tags=["Payments"])


@router.get("/payments", response_model=Page[PaymentOut])
def list_payments(
    status: str | None = None,
    method: str | None = None,
    customer_id: UUID | None = None,
    search: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return payment_service.list_payments(
        db, status=status, method=method, customer_id=customer_id, search=search,
        from_date=from_date, to_date=to_date, page=page, page_size=page_size,
    )


@router.post("/payments/manual", response_model=PaymentOut, status_code=201)
def manual_payment(
    payload: ManualPaymentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return payment_service._payment_out(payment_service.create_manual(db, payload, actor=user))


@router.post("/payments/{payment_id}/confirm")
def confirm_payment(
    payment_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return payment_service.confirm(db, payment_id, actor=user)


@router.post("/payments/{payment_id}/reject", response_model=PaymentOut)
def reject_payment(
    payment_id: UUID,
    payload: PaymentRejectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return payment_service._payment_out(payment_service.reject(db, payment_id, payload.reason, actor=user))


@router.post("/payments/{payment_id}/reverse", response_model=PaymentOut)
def reverse_payment(
    payment_id: UUID,
    payload: PaymentReverseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return payment_service._payment_out(payment_service.reverse(db, payment_id, payload.reason, actor=user))


@router.get("/payment-links", response_model=list[PaymentLinkOut])
def list_links(
    customer_id: UUID | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return payment_service.recent_links(db, customer_id=customer_id)


@router.post("/payment-links/generate", response_model=PaymentLinkOut, status_code=201)
def generate_link(
    payload: PaymentLinkGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.customer_service import get as get_customer

    customer = get_customer(db, payload.customer_id)
    link = payment_service.ensure_payment_link(
        db,
        customer,
        amount=payload.amount,
        force_new=payload.force_new,
        note=payload.note,
        actor=user,
    )
    return payment_service._link_out(link, customer)


@router.post("/payment-links/{link_id}/cancel", response_model=PaymentLinkOut)
def cancel_link(
    link_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return payment_service._link_out(payment_service.cancel_link(db, link_id, actor=user))
