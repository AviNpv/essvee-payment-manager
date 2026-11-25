from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas import (
    SheetPreviewOut,
    SheetPreviewRequest,
    SheetSendOut,
    SheetSendRequest,
)
from app.services import sheets_service

router = APIRouter(prefix="/sheets", tags=["Google Sheets"])


@router.get("/status")
def status(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    return sheets_service.status(db)


@router.post("/preview", response_model=SheetPreviewOut)
def preview(
    payload: SheetPreviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return sheets_service.preview(db, payload, actor=user)


@router.post("/send", response_model=SheetSendOut)
def send(
    payload: SheetSendRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return sheets_service.confirm_send(db, payload, actor=user)
