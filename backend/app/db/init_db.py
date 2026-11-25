"""Database bootstrap: creates tables, seed rows and the initial administrator.

Production deployments should use Alembic (`alembic upgrade head`); this helper is
also safe to run against an already-migrated database because it only inserts rows
that do not exist.
"""

from decimal import Decimal
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import encrypt_secret, hash_password
from app.models import (
    AppSetting,
    AuditLog,
    Customer,
    ImportHistory,
    ImportRow,
    Payment,
    PaymentLink,
    ReminderSetting,
    SchedulerRun,
    User,
    WhatsAppMessage,
    WebhookEvent,
    BalanceTransaction,
)
from app.models import Base
from app.db.session import engine

# key -> (is_secret, description, default)
DEFAULT_SETTINGS: dict[str, tuple[bool, str, str]] = {
    "company_name": (False, "Company display name", settings.company_name),
    "company_logo": (False, "Logo URL (optional)", ""),
    "contact_number": (False, "Public contact number", ""),
    "business_email": (False, "Public business email", ""),
    "address": (False, "Business address", ""),
    "currency": (False, "Display currency", "INR"),
    "timezone": (False, "Business timezone", "Asia/Kolkata"),
    "whatsapp_access_token": (True, "Meta Cloud API access token", settings.whatsapp_access_token),
    "whatsapp_phone_number_id": (True, "WhatsApp Phone Number ID", settings.whatsapp_phone_number_id),
    "whatsapp_waba_id": (True, "WhatsApp Business Account ID", settings.whatsapp_waba_id),
    "whatsapp_webhook_verify_token": (True, "Webhook verification token", settings.whatsapp_webhook_verify_token),
    "whatsapp_app_secret": (True, "Meta application secret", settings.whatsapp_app_secret),
    "whatsapp_api_version": (False, "Graph API version", settings.whatsapp_api_version),
    "reminder_template": (False, "Approved reminder template name", "payment_reminder"),
    "confirmation_template": (False, "Approved confirmation template name", "payment_confirmation"),
    "partial_template": (False, "Approved partial-payment template name", "partial_payment_reminder"),
    "razorpay_key_id": (False, "Razorpay Key ID", settings.razorpay_key_id),
    "razorpay_key_secret": (True, "Razorpay Key Secret", settings.razorpay_key_secret),
    "razorpay_webhook_secret": (True, "Razorpay Webhook Secret", settings.razorpay_webhook_secret),
    "razorpay_mode": (False, "test or live", settings.razorpay_mode),
    "razorpay_callback_url": (False, "Optional Razorpay payment link callback URL", settings.razorpay_callback_url),
    "payment_provider": (False, "UPI or RAZORPAY", settings.payment_provider),
    "upi_vpa": (False, "UPI ID / VPA that receives payments", settings.upi_vpa),
    "upi_payee_name": (False, "Payee name shown in UPI apps", settings.upi_payee_name),
    "public_base_url": (False, "Public HTTPS base URL used in WhatsApp buttons", settings.public_base_url),
    "upi_amount_mode": (False, "CUSTOMER_ENTERS or FIXED_OUTSTANDING", settings.upi_amount_mode),
    "google_service_account_json": (True, "Google service account JSON (store as a Render secret)", settings.google_service_account_json),
    "google_spreadsheet_id": (False, "Google Spreadsheet ID or share URL", settings.google_spreadsheet_id),
}


def create_tables() -> None:
    # Import models module so all tables are registered on Base.metadata.
    Base.metadata.create_all(bind=engine)


def seed(db: Session) -> None:
    if db.query(User).count() == 0:
        db.add(
            User(
                email=settings.admin_email.lower(),
                full_name="ESSVEE Administrator",
                password_hash=hash_password(settings.admin_password),
                role="ADMIN",
                is_active=True,
            )
        )
    if db.query(ReminderSetting).count() == 0:
        db.add(
            ReminderSetting(
                automation_enabled=False,
                frequency_days=7,
                first_reminder_after_days=1,
                max_reminders=4,
                daily_limit=200,
                quiet_start="21:00",
                quiet_end="09:00",
                reminder_template="payment_reminder",
                confirmation_template="payment_confirmation",
                partial_template="partial_payment_reminder",
                minimum_payment_amount=Decimal("10.00"),
                link_expiry_days=7,
            )
        )
    existing = {row.key for row in db.query(AppSetting.key).all()}
    for key, (is_secret, description, default) in DEFAULT_SETTINGS.items():
        if key not in existing:
            db.add(
                AppSetting(
                    key=key,
                    value_encrypted=encrypt_secret(str(default)),
                    is_secret=is_secret,
                    description=description,
                )
            )
    db.commit()


def init_db() -> None:
    create_tables()
    with Session(engine) as db:
        seed(db)


__all__ = [
    "AppSetting",
    "AuditLog",
    "BalanceTransaction",
    "Customer",
    "ImportHistory",
    "ImportRow",
    "Payment",
    "PaymentLink",
    "ReminderSetting",
    "SchedulerRun",
    "User",
    "WhatsAppMessage",
    "WebhookEvent",
    "create_tables",
    "init_db",
    "seed",
]
