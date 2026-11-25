"""Backend-only configuration store. Credentials are Fernet-encrypted at rest."""

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings as env
from app.core.security import decrypt_secret, encrypt_secret, mask_secret
from app.db.init_db import DEFAULT_SETTINGS
from app.models import AppSetting, ReminderSetting
from app.schemas import (
    CompanySettings,
    GoogleSettings,
    PaymentSettings,
    RazorpaySettings,
    ReminderSettings,
    SettingsOut,
    UPISettings,
    WhatsAppSettings,
)
from app.services import audit_service

SECRET_KEYS = {
    "whatsapp_access_token",
    "whatsapp_phone_number_id",
    "whatsapp_waba_id",
    "whatsapp_webhook_verify_token",
    "whatsapp_app_secret",
    "razorpay_key_secret",
    "razorpay_webhook_secret",
    "google_service_account_json",
}

GROUPS: dict[str, list[str]] = {
    "company": [
        "company_name",
        "company_logo",
        "contact_number",
        "business_email",
        "address",
        "currency",
        "timezone",
    ],
    "whatsapp": [
        "whatsapp_access_token",
        "whatsapp_phone_number_id",
        "whatsapp_waba_id",
        "whatsapp_webhook_verify_token",
        "whatsapp_app_secret",
        "whatsapp_api_version",
        "reminder_template",
        "confirmation_template",
        "partial_template",
    ],
    "razorpay": [
        "razorpay_key_id",
        "razorpay_key_secret",
        "razorpay_webhook_secret",
        "razorpay_mode",
        "razorpay_callback_url",
    ],
    "upi": [
        "payment_provider",
        "upi_vpa",
        "upi_payee_name",
        "public_base_url",
        "upi_amount_mode",
    ],
    "google": [
        "google_service_account_json",
        "google_spreadsheet_id",
    ],
}


def get(db: Session, key: str, default: str = "") -> str:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if not row:
        return default
    try:
        return decrypt_secret(row.value_encrypted)
    except ValueError:
        return default


def set_value(db: Session, key: str, value: Any, *, actor: Any = None, commit: bool = True) -> None:
    value = "" if value is None else str(value)
    secret = key in SECRET_KEYS or DEFAULT_SETTINGS.get(key, (False, "", ""))[0]
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value_encrypted = encrypt_secret(value)
        row.is_secret = secret
    else:
        db.add(
            AppSetting(
                key=key,
                value_encrypted=encrypt_secret(value),
                is_secret=secret,
                description=DEFAULT_SETTINGS.get(key, (secret, "", ""))[1],
            )
        )
    if commit:
        db.commit()


def get_all_masked(db: Session) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in db.query(AppSetting).all():
        try:
            value = decrypt_secret(row.value_encrypted)
        except ValueError:
            value = ""
        result[row.key] = mask_secret(value) if row.is_secret else value
    return result


def update_group(
    db: Session, group: str, values: dict[str, Any], *, actor: Any = None
) -> dict[str, str]:
    if group not in GROUPS:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"Unknown settings group: {group}")
    before: dict[str, str] = {}
    changed_keys: list[str] = []
    for key in GROUPS[group]:
        if key not in values:
            continue
        new_value = values[key]
        if new_value is None:
            continue
        new_value = str(new_value)
        # Masked values returned by the API mean "unchanged".
        if "•" in new_value:
            continue
        before[key] = "••••" if key in SECRET_KEYS else get(db, key)
        set_value(db, key, new_value, commit=False)
        changed_keys.append(key)
    db.commit()
    if actor and changed_keys:
        audit_service.log(
            db,
            action=f"settings.update.{group}",
            entity_type="SETTINGS",
            entity_id=group,
            before=before,
            after={key: "••••" if key in SECRET_KEYS else get(db, key) for key in changed_keys},
            actor_id=getattr(actor, "id", None),
            actor_email=getattr(actor, "email", None),
            commit=True,
        )
    return {"updated": ", ".join(changed_keys)}


def clear_credential(db: Session, key: str, *, actor: Any = None) -> None:
    if key not in SECRET_KEYS:
        from app.core.errors import ValidationError

        raise ValidationError(f"{key} is not a clearable credential")
    set_value(db, key, "", commit=False)
    db.commit()
    if actor:
        audit_service.log(
            db,
            action="settings.clear_credential",
            entity_type="SETTINGS",
            entity_id=key,
            after={key: ""},
            actor_id=getattr(actor, "id", None),
            actor_email=getattr(actor, "email", None),
            commit=True,
        )


def get_reminder(db: Session) -> ReminderSetting:
    row = db.query(ReminderSetting).first()
    if not row:
        row = ReminderSetting()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_reminder(db: Session, data: ReminderSettings, *, actor: Any = None) -> ReminderSetting:
    row = get_reminder(db)
    before = {k: str(getattr(row, k)) for k in data.model_dump()}
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    row.updated_by = getattr(actor, "id", None)
    db.commit()
    db.refresh(row)
    audit_service.log(
        db,
        action="settings.update.reminder",
        entity_type="REMINDER_SETTINGS",
        entity_id=str(row.id),
        before=before,
        after=data.model_dump(),
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return row


def update_payment_settings(db: Session, data: PaymentSettings, *, actor: Any = None) -> ReminderSetting:
    row = get_reminder(db)
    before = {k: str(getattr(row, k)) for k in data.model_dump()}
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    audit_service.log(
        db,
        action="settings.update.payment",
        entity_type="PAYMENT_SETTINGS",
        entity_id=str(row.id),
        before=before,
        after=data.model_dump(),
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return row


def razorpay_credentials(db: Session) -> dict[str, str]:
    return {
        "key_id": get(db, "razorpay_key_id") or env.razorpay_key_id,
        "key_secret": get(db, "razorpay_key_secret") or env.razorpay_key_secret,
        "webhook_secret": get(db, "razorpay_webhook_secret") or env.razorpay_webhook_secret,
        "mode": get(db, "razorpay_mode") or env.razorpay_mode,
    }


def upi_credentials(db: Session) -> dict[str, str]:
    return {
        "payment_provider": (get(db, "payment_provider") or env.payment_provider).upper(),
        "vpa": get(db, "upi_vpa") or env.upi_vpa,
        "payee_name": get(db, "upi_payee_name") or env.upi_payee_name,
        "public_base_url": get(db, "public_base_url") or env.public_base_url,
        "amount_mode": (get(db, "upi_amount_mode") or env.upi_amount_mode).upper(),
    }


def whatsapp_credentials(db: Session) -> dict[str, str]:
    return {
        "access_token": get(db, "whatsapp_access_token") or env.whatsapp_access_token,
        "phone_number_id": get(db, "whatsapp_phone_number_id") or env.whatsapp_phone_number_id,
        "waba_id": get(db, "whatsapp_waba_id") or env.whatsapp_waba_id,
        "webhook_verify_token": get(db, "whatsapp_webhook_verify_token")
        or env.whatsapp_webhook_verify_token,
        "app_secret": get(db, "whatsapp_app_secret") or env.whatsapp_app_secret,
        "api_version": get(db, "whatsapp_api_version") or env.whatsapp_api_version,
    }


def google_credentials(db: Session) -> dict[str, str]:
    """The JSON key is a secret; the spreadsheet ID is not. Env values are fallbacks only."""
    return {
        "service_account_json": get(db, "google_service_account_json") or env.google_service_account_json,
        "spreadsheet_id": get(db, "google_spreadsheet_id") or env.google_spreadsheet_id,
    }


def all_settings(db: Session) -> SettingsOut:
    values = get_all_masked(db)
    reminder = get_reminder(db)
    return SettingsOut(
        company=CompanySettings(**{k: values.get(k, "") for k in GROUPS["company"]}),
        whatsapp=WhatsAppSettings(
            **{k: values.get(k, "") for k in GROUPS["whatsapp"]}
        ),
        razorpay=RazorpaySettings(
            **{k: values.get(k, "") for k in GROUPS["razorpay"]}
        ),
        upi=UPISettings(**{k: values.get(k, "") for k in GROUPS["upi"]}),
        google=GoogleSettings(**{k: values.get(k, "") for k in GROUPS["google"]}),
        reminder=ReminderSettings.model_validate(reminder, from_attributes=True),
        payment=PaymentSettings.model_validate(reminder, from_attributes=True),
        configured={
            "razorpay": bool(razorpay_credentials(db)["key_id"] and razorpay_credentials(db)["key_secret"]),
            "razorpay_webhook": bool(razorpay_credentials(db)["webhook_secret"]),
            "upi": bool(upi_credentials(db)["vpa"] and upi_credentials(db)["public_base_url"]),
            "whatsapp": bool(
                whatsapp_credentials(db)["access_token"] and whatsapp_credentials(db)["phone_number_id"]
            ),
            "whatsapp_webhook": bool(
                whatsapp_credentials(db)["webhook_verify_token"]
                and whatsapp_credentials(db)["app_secret"]
            ),
            "google_sheets": bool(google_credentials(db)["service_account_json"]),
            "google_spreadsheet": bool(google_credentials(db)["spreadsheet_id"]),
        },
    )


def company_name(db: Session) -> str:
    return get(db, "company_name") or env.company_name
