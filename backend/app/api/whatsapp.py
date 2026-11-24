from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import MessageStatus, User, WhatsAppMessage
from app.schemas import (
    BulkPreviewOut,
    BulkPreviewRequest,
    BulkSendRequest,
    MessageOut,
    Page,
    SendReminderRequest,
    TestConnectionOut,
)
from app.services import (
    customer_service,
    payment_service,
    reminder_service,
    settings_service,
    whatsapp_service,
)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


@router.post("/reminders/send", response_model=MessageOut)
def send_reminder(
    payload: SendReminderRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    customer = customer_service.get(db, payload.customer_id)
    before = whatsapp_service.existing_message_ids(db, customer.id)
    link = payment_service.ensure_payment_link(db, customer)
    message = whatsapp_service.send_reminder(
        db, customer, link.url, idempotency_suffix=f"manual:{link.id}"
    )
    if message.status != MessageStatus.FAILED.value and message.id not in before:
        customer.reminder_count += 1
        from datetime import datetime, timezone

        customer.last_reminder_at = datetime.now(timezone.utc)
        db.commit()
    return MessageOut.model_validate(message, from_attributes=True).model_copy(
        update={"customer_name": customer.name}
    )


@router.post("/reminders/preview", response_model=BulkPreviewOut)
def preview_bulk(
    payload: BulkPreviewRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return BulkPreviewOut(**reminder_service.preview(db, payload))


@router.post("/reminders/bulk")
def send_bulk(
    payload: BulkSendRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return reminder_service.send_bulk(db, payload.confirmation_token, payload.customer_ids)


@router.get("/messages", response_model=Page[MessageOut])
def list_messages(
    status: str | None = None,
    message_type: str | None = None,
    customer_id: UUID | None = None,
    search: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from sqlalchemy import or_

    query = db.query(WhatsAppMessage)
    if status:
        query = query.filter(WhatsAppMessage.status == status.upper())
    if message_type:
        query = query.filter(WhatsAppMessage.message_type == message_type.upper())
    if customer_id:
        query = query.filter(WhatsAppMessage.customer_id == customer_id)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                WhatsAppMessage.phone_number.ilike(term),
                WhatsAppMessage.body_preview.ilike(term),
            )
        )
    if from_date:
        query = query.filter(WhatsAppMessage.created_at >= from_date)
    if to_date:
        query = query.filter(WhatsAppMessage.created_at <= to_date)
    total = query.count()
    rows = (
        query.order_by(WhatsAppMessage.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    from app.models import Customer

    names = {c.id: c.name for c in db.query(Customer).all()}
    items = []
    for m in rows:
        name = names.get(m.customer_id)
        items.append(
            MessageOut.model_validate(m, from_attributes=True).model_copy(
                update={"customer_name": name}
            )
        )
    return Page(items=items, total=total, page=page, page_size=page_size, pages=max(1, (total + page_size - 1) // page_size))


@router.post("/messages/{message_id}/retry", response_model=MessageOut)
def retry_message(
    message_id: UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return MessageOut.model_validate(
        whatsapp_service.retry_failed(db, message_id), from_attributes=True
    )


@router.get("/templates")
def templates(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return whatsapp_service.template_definitions()


@router.post("/test", response_model=TestConnectionOut)
def test_whatsapp(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    result = whatsapp_service.send_test(db)
    return TestConnectionOut(success=True, message="WhatsApp Cloud API connection successful", detail=result)
