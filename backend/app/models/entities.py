"""SQLAlchemy ORM models for ESSVEE Payment Manager.

All money columns use Numeric(16, 2) mapped to Python Decimal. Financial state is
derived from an append-only balance ledger; the customer's cached balance is
updated inside the same database transaction as the ledger entry.
"""

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    Uuid,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Store UTC without offsets and always return timezone-aware UTC values.

    PostgreSQL and SQLite differ in how they return ``UTCDateTime``
    columns. Normalising at the type level keeps every comparison in the
    application consistent and avoids naive/aware datetime errors.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class UserRole(StrEnum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"


class CustomerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class ConsentStatus(StrEnum):
    OPT_IN = "OPT_IN"
    OPT_OUT = "OPT_OUT"
    UNKNOWN = "UNKNOWN"


class TransactionType(StrEnum):
    OPENING = "OPENING"
    CHARGE = "CHARGE"
    PAYMENT = "PAYMENT"
    ADJUSTMENT = "ADJUSTMENT"
    REVERSAL = "REVERSAL"


class TransactionEffect(StrEnum):
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"


class PaymentStatus(StrEnum):
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class PaymentMethod(StrEnum):
    RAZORPAY = "RAZORPAY"
    CASH = "CASH"
    BANK_TRANSFER = "BANK_TRANSFER"
    UPI = "UPI"
    OTHER = "OTHER"


class PaymentLinkStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAID = "PAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class MessageDirection(StrEnum):
    OUTBOUND = "OUTBOUND"
    INBOUND = "INBOUND"


class MessageStatus(StrEnum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"


class MessageType(StrEnum):
    REMINDER = "REMINDER"
    CONFIRMATION = "CONFIRMATION"
    PARTIAL = "PARTIAL"
    TEST = "TEST"
    INBOUND = "INBOUND"


class ImportMode(StrEnum):
    UPDATE_BALANCE = "UPDATE_BALANCE"
    ADJUSTMENT = "ADJUSTMENT"


class ImportStatus(StrEnum):
    PREVIEWED = "PREVIEWED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150), default="Administrator")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default=UserRole.ADMIN.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Customer(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_outstanding", "outstanding_balance"),
        Index("ix_customers_status_outstanding", "status", "outstanding_balance"),
    )

    name: Mapped[str] = mapped_column(String(200))
    whatsapp_number: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=CustomerStatus.ACTIVE.value, index=True)
    consent_status: Mapped[str] = mapped_column(
        String(20), default=ConsentStatus.OPT_IN.value
    )
    opening_balance: Mapped[Any] = mapped_column(Numeric(16, 2), default=0)
    outstanding_balance: Mapped[Any] = mapped_column(Numeric(16, 2), default=0, index=True)
    total_paid: Mapped[Any] = mapped_column(Numeric(16, 2), default=0)
    total_charges: Mapped[Any] = mapped_column(Numeric(16, 2), default=0)
    total_adjustments: Mapped[Any] = mapped_column(Numeric(16, 2), default=0)
    exclude_from_reminders: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_payment_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    transactions: Mapped[list["BalanceTransaction"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", passive_deletes=True
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", passive_deletes=True
    )


class BalanceTransaction(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "balance_transactions"
    __table_args__ = (Index("ix_ledger_customer_created", "customer_id", "created_at"),)

    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Any] = mapped_column(Numeric(16, 2))
    transaction_type: Mapped[str] = mapped_column(String(30))
    effect: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(String(300))
    reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("payments.id", ondelete="SET NULL"), nullable=True
    )
    balance_after: Mapped[Any] = mapped_column(Numeric(16, 2), default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="transactions")


class Payment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("gateway_payment_id", name="uq_payments_gateway_id"),
        Index("ix_payments_status_date", "status", "payment_date"),
        Index("ix_payments_customer", "customer_id", "created_at"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Any] = mapped_column(Numeric(16, 2))
    method: Mapped[str] = mapped_column(String(30), default=PaymentMethod.RAZORPAY.value)
    status: Mapped[str] = mapped_column(
        String(30), default=PaymentStatus.AWAITING_CONFIRMATION.value, index=True
    )
    gateway_payment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gateway_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payment_link_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("payment_links.id", ondelete="SET NULL"), nullable=True
    )
    reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_date: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="payments")
    link: Mapped["PaymentLink | None"] = relationship(back_populates="payments")


class PaymentLink(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "payment_links"
    __table_args__ = (
        UniqueConstraint("razorpay_link_id", name="uq_links_razorpay_id"),
        Index("ix_links_customer_status", "customer_id", "status"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    razorpay_link_id: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    amount: Mapped[Any] = mapped_column(Numeric(16, 2))
    status: Mapped[str] = mapped_column(String(30), default=PaymentLinkStatus.ACTIVE.value)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    customer: Mapped[Customer] = relationship()
    payments: Mapped[list[Payment]] = relationship(back_populates="link")


class WhatsAppMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_wa_idempotency"),
        UniqueConstraint("provider_message_id", name="uq_wa_provider_id"),
        Index("ix_wa_customer_created", "customer_id", "created_at"),
        Index("ix_wa_status", "status"),
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    direction: Mapped[str] = mapped_column(String(10), default=MessageDirection.OUTBOUND.value)
    message_type: Mapped[str] = mapped_column(String(30), default=MessageType.REMINDER.value)
    template_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=MessageStatus.QUEUED.value)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    phone_number: Mapped[str] = mapped_column(String(20))
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    button_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    customer: Mapped[Customer | None] = relationship()


class WebhookEvent(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_event"),)

    provider: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSON)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ReminderSetting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "reminder_settings"

    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    frequency_days: Mapped[int] = mapped_column(Integer, default=7)
    first_reminder_after_days: Mapped[int] = mapped_column(Integer, default=1)
    max_reminders: Mapped[int] = mapped_column(Integer, default=4)
    daily_limit: Mapped[int] = mapped_column(Integer, default=200)
    quiet_start: Mapped[str] = mapped_column(String(5), default="21:00")
    quiet_end: Mapped[str] = mapped_column(String(5), default="09:00")
    reminder_template: Mapped[str] = mapped_column(String(100), default="payment_reminder")
    confirmation_template: Mapped[str] = mapped_column(String(100), default="payment_confirmation")
    partial_template: Mapped[str] = mapped_column(String(100), default="partial_payment_reminder")
    retry_failed_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    max_retry_attempts: Mapped[int] = mapped_column(Integer, default=3)
    link_expiry_days: Mapped[int] = mapped_column(Integer, default=7)
    allow_partial_payments: Mapped[bool] = mapped_column(Boolean, default=False)
    minimum_payment_amount: Mapped[Any] = mapped_column(Numeric(16, 2), default=10)
    manual_payment_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AppSetting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value_encrypted: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportHistory(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "import_history"

    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))
    mode: Mapped[str] = mapped_column(String(30), default=ImportMode.UPDATE_BALANCE.value)
    status: Mapped[str] = mapped_column(String(20), default=ImportStatus.PREVIEWED.value)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rows: Mapped[list["ImportRow"]] = relationship(
        back_populates="import_batch", cascade="all, delete-orphan", passive_deletes=True
    )


class ImportRow(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "import_rows"
    __table_args__ = (Index("ix_import_rows_batch", "import_id", "row_number"),)

    import_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("import_history.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    normalized_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amount: Mapped[Any | None] = mapped_column(Numeric(16, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="VALID")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    current_balance: Mapped[Any | None] = mapped_column(Numeric(16, 2), nullable=True)
    difference: Mapped[Any | None] = mapped_column(Numeric(16, 2), nullable=True)

    import_batch: Mapped[ImportHistory] = relationship(back_populates="rows")


class AuditLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)


class SchedulerRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "scheduler_runs"

    job_name: Mapped[str] = mapped_column(String(100), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    processed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
