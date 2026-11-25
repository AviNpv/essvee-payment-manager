from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Email addresses are validated with a simple local check to avoid an extra
# runtime dependency; Pydantic's EmailStr is intentionally not used here.

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class UserOut(ORMModel):
    id: UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
class CustomerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    whatsapp_number: str
    email: str | None = Field(default=None, max_length=255)
    opening_balance: Decimal = Field(default=Decimal("0"), ge=Decimal("-100000000"))
    status: str = "ACTIVE"
    consent_status: str = "OPT_IN"
    exclude_from_reminders: bool = False
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str | None) -> str | None:
        if v and "@" not in v:
            raise ValueError("Invalid email address")
        return v.strip().lower() if v else None


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    whatsapp_number: str | None = None
    email: str | None = None
    status: str | None = None
    consent_status: str | None = None
    exclude_from_reminders: bool | None = None
    notes: str | None = None


class CustomerOut(ORMModel):
    id: UUID
    name: str
    whatsapp_number: str
    email: str | None = None
    status: str
    consent_status: str
    opening_balance: Decimal
    outstanding_balance: Decimal
    total_paid: Decimal
    total_charges: Decimal
    total_adjustments: Decimal
    exclude_from_reminders: bool
    reminder_count: int
    last_reminder_at: datetime | None = None
    last_payment_at: datetime | None = None
    notes: str | None = None
    created_at: datetime


class CustomerHistoryOut(ORMModel):
    transactions: list["TransactionOut"]
    payments: list["PaymentOut"]
    messages: list["MessageOut"]


class BalanceAdjustRequest(BaseModel):
    amount: Decimal
    transaction_type: str = "ADJUSTMENT"  # CHARGE or ADJUSTMENT
    description: str = Field(min_length=3, max_length=300)
    allow_negative: bool = False


class TransactionOut(ORMModel):
    id: UUID
    amount: Decimal
    transaction_type: str
    effect: str
    description: str
    reference: str | None = None
    balance_after: Decimal
    created_at: datetime


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class ManualPaymentCreate(BaseModel):
    customer_id: UUID
    amount: Decimal = Field(gt=0)
    method: str = "CASH"
    reference: str | None = None
    notes: str | None = None
    payment_date: datetime | None = None


class PaymentOut(ORMModel):
    id: UUID
    customer_id: UUID
    amount: Decimal
    method: str
    status: str
    gateway_payment_id: str | None = None
    gateway_order_id: str | None = None
    payment_link_id: UUID | None = None
    reference: str | None = None
    notes: str | None = None
    payment_date: datetime
    verified_at: datetime | None = None
    rejection_reason: str | None = None
    failure_reason: str | None = None
    created_at: datetime
    customer_name: str | None = None
    customer_phone: str | None = None


class PaymentRejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PaymentReverseRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PaymentLinkOut(ORMModel):
    id: UUID
    customer_id: UUID
    razorpay_link_id: str
    url: str
    amount: Decimal
    status: str
    expires_at: datetime | None = None
    created_at: datetime
    customer_name: str | None = None
    provider: str = "RAZORPAY"


class PaymentLinkGenerateRequest(BaseModel):
    customer_id: UUID
    amount: Decimal | None = Field(default=None, gt=0)
    force_new: bool = False
    note: str | None = None


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
class PreviewRowOut(BaseModel):
    row_number: int
    name: str
    phone: str
    normalized_phone: str | None = None
    amount: Decimal | None = None
    status: str
    error: str | None = None
    current_balance: Decimal | None = None
    difference: Decimal | None = None
    customer_id: UUID | None = None


class ExcelPreviewOut(BaseModel):
    import_id: UUID
    filename: str
    row_count: int
    valid_count: int
    invalid_count: int
    rows: list[PreviewRowOut]
    mode: str
    requires_difference_confirmation: bool = False


class ExcelImportRequest(BaseModel):
    import_id: UUID
    confirm_differences: bool = False


class ExcelImportOut(BaseModel):
    import_id: UUID
    status: str
    imported_count: int
    updated_count: int
    invalid_count: int
    total_amount: Decimal
    message: str


class ImportOut(ORMModel):
    id: UUID
    filename: str
    file_type: str
    mode: str
    status: str
    row_count: int
    valid_count: int
    invalid_count: int
    imported_count: int
    updated_count: int
    summary: dict | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# WhatsApp / reminders
# ---------------------------------------------------------------------------
class SendReminderRequest(BaseModel):
    customer_id: UUID
    message_type: str = "REMINDER"
    note: str | None = None


class BulkPreviewRequest(BaseModel):
    frequency_days: int | None = None
    max_reminders: int | None = None
    daily_limit: int | None = None
    respect_quiet_hours: bool = True


class BulkPreviewOut(BaseModel):
    eligible_count: int
    estimated_messages: int
    total_outstanding: Decimal
    excluded_count: int
    reason_breakdown: dict[str, int]
    customers: list[dict[str, Any]]
    confirmation_token: str
    quiet_hours: bool = False


class BulkSendRequest(BaseModel):
    confirmation_token: str
    customer_ids: list[UUID] | None = None


class MessageOut(ORMModel):
    id: UUID
    customer_id: UUID | None = None
    provider_message_id: str | None = None
    direction: str
    message_type: str
    template_name: str | None = None
    status: str
    phone_number: str
    body_preview: str | None = None
    button_url: str | None = None
    error: str | None = None
    retry_count: int
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    customer_name: str | None = None


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------
class SheetPreviewRequest(BaseModel):
    tab: str = Field(min_length=1, max_length=40)
    spreadsheet_id: str | None = Field(default=None, max_length=500)


class SheetPreviewRowOut(BaseModel):
    row_number: int
    name: str
    phone: str | None = None
    amount: Decimal | None = None
    current_balance: Decimal | None = None
    difference: Decimal | None = None
    balance_after: Decimal | None = None
    status: str
    reason: str
    customer_id: UUID | None = None
    pending_payment_id: UUID | None = None
    message_reason: str | None = None
    error: str | None = None


class SheetPreviewOut(BaseModel):
    import_id: UUID
    tab: str
    spreadsheet_id: str
    kind: str = "reminders"
    row_count: int
    matched_count: int
    unmatched_count: int
    opted_out_count: int
    eligible_count: int
    invalid_count: int
    reason_breakdown: dict[str, int]
    message_reason_breakdown: dict[str, int] = {}
    reminder_count: int = 0
    rows: list[SheetPreviewRowOut]
    confirmation_token: str
    quiet_hours: bool = False
    directory_created: int = 0
    directory_updated: int = 0
    warnings: list[str] = []


class SheetSendRequest(BaseModel):
    import_id: UUID
    confirmation_token: str


class SheetSendOut(BaseModel):
    import_id: UUID
    status: str
    updated_count: int
    payments_confirmed: int = 0
    adjustments: int = 0
    sent_count: int
    failed_count: int
    skipped_count: int
    messages_skipped_count: int = 0
    total_amount: Decimal
    message: str


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class CompanySettings(BaseModel):
    company_name: str
    company_logo: str = ""
    contact_number: str = ""
    business_email: str = ""
    address: str = ""
    currency: str = "INR"
    timezone: str = "Asia/Kolkata"


class WhatsAppSettings(BaseModel):
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_waba_id: str = ""
    whatsapp_webhook_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v21.0"
    reminder_template: str = "payment_reminder"
    confirmation_template: str = "payment_confirmation"
    partial_template: str = "partial_payment_reminder"


class RazorpaySettings(BaseModel):
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_mode: str = "test"
    razorpay_callback_url: str = ""


class UPISettings(BaseModel):
    payment_provider: str = "UPI"
    upi_vpa: str = ""
    upi_payee_name: str = ""
    public_base_url: str = "http://localhost:8000"
    upi_amount_mode: str = "CUSTOMER_ENTERS"


class GoogleSettings(BaseModel):
    google_service_account_json: str = ""
    google_spreadsheet_id: str = ""


class ReminderSettings(BaseModel):
    automation_enabled: bool = False
    frequency_days: int = Field(default=7, ge=1, le=90)
    first_reminder_after_days: int = Field(default=1, ge=0, le=90)
    max_reminders: int = Field(default=4, ge=1, le=50)
    daily_limit: int = Field(default=200, ge=1, le=5000)
    quiet_start: str = "21:00"
    quiet_end: str = "09:00"
    reminder_template: str = "payment_reminder"
    confirmation_template: str = "payment_confirmation"
    partial_template: str = "partial_payment_reminder"
    retry_failed_messages: bool = True
    max_retry_attempts: int = Field(default=3, ge=0, le=10)


class PaymentSettings(BaseModel):
    link_expiry_days: int = Field(default=7, ge=1, le=90)
    allow_partial_payments: bool = False
    minimum_payment_amount: Decimal = Field(default=Decimal("10.00"), ge=0)
    manual_payment_approval: bool = True


class SettingsOut(BaseModel):
    company: CompanySettings
    whatsapp: WhatsAppSettings
    razorpay: RazorpaySettings
    upi: UPISettings
    google: GoogleSettings
    reminder: ReminderSettings
    payment: PaymentSettings
    configured: dict[str, bool]


class TestConnectionOut(BaseModel):
    success: bool
    message: str
    detail: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Reports / audit
# ---------------------------------------------------------------------------
class DateRange(BaseModel):
    from_date: datetime | None = None
    to_date: datetime | None = None


class AuditLogOut(ORMModel):
    id: UUID
    action: str
    entity_type: str
    entity_id: str | None = None
    actor_email: str | None = None
    before: dict | None = None
    after: dict | None = None
    created_at: datetime


class ExportRequest(BaseModel):
    report: str
    format: str = "xlsx"  # xlsx or csv
    from_date: datetime | None = None
    to_date: datetime | None = None
    customer_id: UUID | None = None


TokenResponse.model_rebuild()
CustomerHistoryOut.model_rebuild()
