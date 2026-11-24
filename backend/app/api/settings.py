from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ConfigurationError, ProviderError
from app.db.session import get_db
from app.models import User
from app.schemas import (
    PaymentSettings,
    ReminderSettings,
    SettingsOut,
    TestConnectionOut,
)
from app.services import razorpay_service, settings_service, upi_service, whatsapp_service

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("", response_model=SettingsOut)
def get_settings(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    return settings_service.all_settings(db)


@router.put("/reminder", response_model=ReminderSettings)
def update_reminder(
    payload: ReminderSettings,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = settings_service.update_reminder(db, payload, actor=user)
    return ReminderSettings.model_validate(row, from_attributes=True)


@router.put("/payment", response_model=PaymentSettings)
def update_payment(
    payload: PaymentSettings,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = settings_service.update_payment_settings(db, payload, actor=user)
    return PaymentSettings.model_validate(row, from_attributes=True)


@router.put("/{group}")
def update_group(
    group: str,
    values: dict[str, Any],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return settings_service.update_group(db, group, values, actor=user)


@router.post("/clear/{key}")
def clear_credential(
    key: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings_service.clear_credential(db, key, actor=user)
    return {"message": f"Cleared {key}"}


@router.post("/test/{provider}", response_model=TestConnectionOut)
def test_connection(
    provider: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    try:
        if provider == "razorpay":
            result = razorpay_service.test_connection(db)
        elif provider == "whatsapp":
            result = whatsapp_service.send_test(db)
        elif provider == "upi":
            result = upi_service.test_connection(db)
        else:
            raise ConfigurationError(f"Unknown provider: {provider}")
    except (ConfigurationError, ProviderError) as exc:
        return TestConnectionOut(success=False, message=exc.message)
    return TestConnectionOut(success=True, message=f"{provider.title()} connection successful", detail=result)
