from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import client_info, get_current_user
from app.core.config import settings
from app.core.errors import AppError
from app.core.rate_limit import limit
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import User
from app.schemas import ChangePasswordRequest, LoginRequest, TokenResponse, UserOut
from app.services import audit_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    _rate=Depends(limit("login", 10, 60)),
):
    user = db.query(User).filter(User.email == payload.email.strip().lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise AppError("Invalid email or password", code="INVALID_CREDENTIALS", status_code=401)
    if not user.is_active:
        raise AppError("Account is disabled", code="ACCOUNT_DISABLED", status_code=403)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    audit_service.log(
        db,
        action="auth.login",
        entity_type="USER",
        entity_id=str(user.id),
        actor_id=user.id,
        actor_email=user.email,
        ip_address=client_info(request)["ip_address"],
        user_agent=client_info(request)["user_agent"],
        commit=True,
    )
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role),
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserOut.model_validate(user, from_attributes=True),
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user, from_attributes=True)


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise AppError("Current password is incorrect", code="INVALID_PASSWORD", status_code=400)
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    audit_service.log(
        db,
        action="auth.change_password",
        entity_type="USER",
        entity_id=str(user.id),
        actor_id=user.id,
        actor_email=user.email,
        commit=True,
    )
    return {"message": "Password updated successfully"}
