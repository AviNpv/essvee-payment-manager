"""Read-only Google Sheets import with preview-first, explicit-send reminders.

The spreadsheet is accessed with a service account using a short-lived OAuth2
JWT signed locally. No browser automation, no scraping and no tokens are ever
returned to the frontend. All money changes go through the append-only ledger
and all messages go through the approved WhatsApp reminder path.
"""

import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote
from uuid import UUID

import jwt
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings as env
from app.core.errors import (
    AppError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    ProviderError,
    ValidationError,
)
from app.models import (
    ConsentStatus,
    Customer,
    CustomerStatus,
    ImportHistory,
    ImportMode,
    ImportRow,
    ImportStatus,
    MessageStatus,
    Payment,
    PaymentStatus,
    TransactionEffect,
    TransactionType,
)
from app.schemas import (
    CustomerCreate,
    CustomerUpdate,
    ManualPaymentCreate,
    SheetPaymentConfirmOut,
    SheetPreviewOut,
    SheetPreviewRequest,
    SheetPreviewRowOut,
    SheetSendOut,
    SheetSendRequest,
)
from app.services import (
    audit_service,
    balance_service,
    customer_service,
    payment_service,
    reminder_service,
    settings_service,
    whatsapp_service,
)

DAYS = ("Tuesday", "Thursday", "Saturday")
CUSTOMERS_TAB = "Customers"
PAYMENTS_TAB = "Customer Details"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
TOKEN_ISSUER = "essvee-sheets-confirmation"
TOKEN_TTL_SECONDS = 600
HTTP_TIMEOUT = (5, 20)

NAME_ALIASES = ("name", "customername", "customer", "fullname", "party")
PHONE_ALIASES = (
    "phone",
    "mobile",
    "mobilenumber",
    "whatsapp",
    "whatsappnumber",
    "contact",
    "contactnumber",
)
AMOUNT_ALIASES = (
    "amount",
    "outstanding",
    "outstandingamount",
    "balance",
    "due",
    "dueamount",
)
PAID_ALIASES = (
    "paid amount",
    "paidamount",
    "amount paid",
    "amountpaid",
    "paid",
    "payment",
    "received",
    "received amount",
)

REASON_LABELS = {
    "invalid_name": "Invalid name",
    "invalid_amount": "Invalid amount",
    "unmatched": "No number found in Customers tab",
    "ambiguous_directory": "Duplicate or conflicting directory entry",
    "invalid_phone": "Invalid WhatsApp number",
    "no_consent": "Opted out / no WhatsApp consent",
    "inactive": "Customer is inactive",
    "excluded": "Excluded from reminders",
    "awaiting_confirmation": "Payment awaiting admin review",
    "max_reminders_reached": "Maximum reminders reached",
    "within_interval": "Within reminder interval",
    "too_early": "New customer inside first-reminder window",
    "zero_balance": "Zero outstanding balance",
    "no_outstanding": "No outstanding balance to reduce",
    "exceeds_balance": "Paid amount exceeds outstanding balance",
    "eligible": "Ready to send",
    "ready": "Ready to confirm",
}

_TOKEN_CACHE: dict[str, tuple[str, int]] = {}


# ---------------------------------------------------------------------------
# Configuration and low-level Google Sheets access
# ---------------------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _service_account_info(db: Session) -> dict[str, str]:
    raw = settings_service.google_credentials(db)["service_account_json"]
    if not raw:
        raise ConfigurationError(
            "Google Sheets is not configured. Store the service account JSON as "
            "GOOGLE_SERVICE_ACCOUNT_JSON in Render (or Settings → Google Sheets)."
        )
    try:
        info = json.loads(raw)
    except (TypeError, ValueError):
        raise ConfigurationError("Google service account JSON is not valid JSON")
    missing = [key for key in ("client_email", "private_key", "token_uri") if not info.get(key)]
    if missing:
        raise ConfigurationError(
            "Google service account JSON is missing required fields: " + ", ".join(missing)
        )
    return info


def _access_token(info: dict[str, str]) -> str:
    """Exchange the service account JWT for a short-lived Google access token."""
    now = int(time.time())
    cached = _TOKEN_CACHE.get(info["client_email"])
    if cached and cached[1] > now + 60:
        return cached[0]

    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": info["client_email"],
        "scope": SHEETS_SCOPE,
        "aud": info["token_uri"],
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    )
    try:
        private_key = serialization.load_pem_private_key(
            info["private_key"].encode("utf-8"), password=None
        )
        signature = private_key.sign(
            signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
        )
    except (TypeError, ValueError):
        raise ConfigurationError("Google service account private key is invalid")

    assertion = signing_input + "." + _b64url(signature)
    try:
        response = requests.post(
            info["token_uri"],
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ProviderError(f"Google OAuth request failed: {exc}")
    if response.status_code >= 400:
        raise ProviderError(f"Google OAuth request failed (HTTP {response.status_code})")
    token = response.json().get("access_token") if hasattr(response, "json") else None
    if not token:
        raise ProviderError("Google OAuth response did not contain an access token")
    _TOKEN_CACHE[info["client_email"]] = (token, now + 3600)
    return token


def _extract_spreadsheet_id(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    match = re.search(r"/spreadsheets/d/([^/]+)", raw)
    if match:
        return match.group(1)
    return raw.split("?")[0].strip("/")


def _spreadsheet_id(db: Session, explicit: str | None = None) -> str:
    sid = _extract_spreadsheet_id(explicit) or _extract_spreadsheet_id(
        settings_service.google_credentials(db)["spreadsheet_id"]
    )
    if not sid:
        raise ConfigurationError(
            "Spreadsheet ID is missing. Paste the spreadsheet URL in Settings → Google Sheets."
        )
    return sid


def _fetch_values(db: Session, spreadsheet_id: str, tab: str) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """Return (headers, [(row_number, row_dict)]) for a named worksheet."""
    info = _service_account_info(db)
    token = _access_token(info)
    sheet_range = f"'{tab}'!A:ZZ"
    url = (
        "https://sheets.googleapis.com/v4/spreadsheets/"
        + quote(spreadsheet_id, safe="")
        + "/values/"
        + quote(sheet_range, safe="!':")
    )
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ProviderError(f"Google Sheets request failed: {exc}")
    if response.status_code == 404:
        raise ConfigurationError("Spreadsheet not found. Check the ID and shared access.")
    if response.status_code == 403:
        raise ConfigurationError(
            f"Access denied. Share the spreadsheet with {info['client_email']} as Viewer."
        )
    if response.status_code >= 400:
        raise ProviderError(f"Google Sheets request failed (HTTP {response.status_code})")
    values = response.json().get("values") or []
    if not values:
        return [], []
    header = [str(cell).strip() for cell in values[0]]
    rows: list[tuple[int, dict[str, str]]] = []
    for index, raw_row in enumerate(values[1:], start=2):
        padded = list(raw_row) + [""] * (len(header) - len(raw_row))
        rows.append((index, dict(zip(header, [str(cell) for cell in padded]))))
    return header, rows


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def _header_key(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _find_column(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    keys = {_header_key(h): h for h in headers}
    for alias in aliases:
        if alias in keys:
            return keys[alias]
    for header in headers:
        key = _header_key(header)
        if any(alias in key or key in alias for alias in aliases):
            return header
    return None


def _clean_name(value: Any) -> str:
    return " ".join(str(value or "").split())


def _name_key(value: str) -> str:
    return _clean_name(value).casefold()


def _parse_amount(value: Any) -> tuple[Decimal | None, str | None]:
    text = str(value or "").replace(",", "").replace("₹", "").replace("INR", "").strip()
    if not text:
        return None, "Amount is required"
    try:
        amount = Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None, "Invalid amount"
    if amount <= 0:
        return None, "Amount must be greater than zero"
    return amount, None


def _sheet_reason(
    db: Session, customer: Customer, amount: Decimal, config: Any, frequency_days: int
) -> tuple[bool, str]:
    """Central eligibility rules with two deliberate sheet-flow exceptions.

    Zero balance and the first-reminder window are not blockers: this flow sets
    the balance from the sheet first, and the administrator explicitly approves
    this send. Every other guard (consent, status, exclusion, review, interval,
    maximum, valid number) is enforced exactly as in the reminder service.
    """
    if customer.status != CustomerStatus.ACTIVE.value:
        return False, "inactive"
    if customer.consent_status != ConsentStatus.OPT_IN.value:
        return False, "no_consent"
    if customer.exclude_from_reminders:
        return False, "excluded"
    if not customer_service.is_valid_phone(customer.whatsapp_number):
        return False, "invalid_phone"
    pending = (
        db.query(func.count(Payment.id))
        .filter(
            Payment.customer_id == customer.id,
            Payment.status == PaymentStatus.AWAITING_CONFIRMATION.value,
        )
        .scalar()
        or 0
    )
    if pending:
        return False, "awaiting_confirmation"
    if customer.reminder_count >= config.max_reminders:
        return False, "max_reminders_reached"
    if customer.last_reminder_at:
        last = customer.last_reminder_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last < timedelta(days=frequency_days):
            return False, "within_interval"
    return True, "eligible"


# ---------------------------------------------------------------------------
# Customers directory sync
# ---------------------------------------------------------------------------
def read_directory(db: Session, spreadsheet_id: str) -> tuple[dict[str, dict[str, str]], list[str]]:
    header, rows = _fetch_values(db, spreadsheet_id, CUSTOMERS_TAB)
    name_col = _find_column(header, NAME_ALIASES)
    phone_col = _find_column(header, PHONE_ALIASES)
    if not name_col or not phone_col:
        raise ValidationError(
            "The Customers tab needs a Name column and a Mobile / WhatsApp Number column"
        )
    directory: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    seen_phones: dict[str, str] = {}
    for row_number, raw in rows:
        name = _clean_name(raw.get(name_col))
        if not name or len(name) < 2:
            warnings.append(f"Customers row {row_number}: missing or invalid name")
            continue
        try:
            phone = customer_service.normalize_phone(raw.get(phone_col))
        except ValidationError:
            warnings.append(f"Customers row {row_number}: invalid mobile number")
            continue
        key = _name_key(name)
        if key in directory and directory[key]["phone"] != phone:
            warnings.append(f"Customers row {row_number}: conflicting number for {name}")
            continue
        if phone in seen_phones and seen_phones[phone] != key:
            warnings.append(f"Customers row {row_number}: number already listed for another name")
            continue
        directory[key] = {"name": name, "phone": phone, "row": str(row_number)}
        seen_phones[phone] = key
    return directory, warnings


def sync_directory(
    db: Session, directory: dict[str, dict[str, str]], actor: Any = None
) -> dict[str, int]:
    """Sync the predefined Customers name → mobile directory into the app DB.

    Only names and numbers are synced. Consent, status, exclusions and balances
    are never changed by this operation.
    """
    created = 0
    updated = 0
    for entry in directory.values():
        name, phone = entry["name"], entry["phone"]
        customer = customer_service.get_by_phone(db, phone)
        if customer:
            if customer.name.casefold() != name.casefold() and len(name) >= 2:
                customer_service.update(
                    db, customer.id, CustomerUpdate(name=name), actor=actor
                )
                updated += 1
            continue
        customer_service.create(
            db,
            CustomerCreate(name=name, whatsapp_number=phone, opening_balance=Decimal("0")),
            actor=actor,
        )
        created += 1
    audit_service.log(
        db,
        action="sheets.customers.sync",
        entity_type="SHEET_DIRECTORY",
        after={"created": created, "updated": updated, "entries": len(directory)},
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return {"created": created, "updated": updated, "entries": len(directory)}


# ---------------------------------------------------------------------------
# Confirmation token
# ---------------------------------------------------------------------------
def _issue_token(import_id: UUID, row_ids: list[UUID], tab: str) -> str:
    now = int(time.time())
    payload = {
        "iss": TOKEN_ISSUER,
        "import_id": str(import_id),
        "row_ids": [str(row_id) for row_id in row_ids],
        "tab": tab,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, env.secret_key, algorithm="HS256")


def _validate_token(token: str, import_id: UUID, row_ids: list[UUID], tab: str) -> None:
    try:
        payload = jwt.decode(
            token,
            env.secret_key,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        raise ValidationError("Confirmation token is invalid or has expired")
    expected = [str(row_id) for row_id in row_ids]
    if (
        payload.get("iss") != TOKEN_ISSUER
        or payload.get("import_id") != str(import_id)
        or payload.get("row_ids") != expected
        or payload.get("tab") != tab
    ):
        raise ValidationError("Confirmation token does not match this preview")


# ---------------------------------------------------------------------------
# Public service API
# ---------------------------------------------------------------------------
def status(db: Session) -> dict[str, Any]:
    credentials = settings_service.google_credentials(db)
    info: dict[str, str] = {}
    if credentials["service_account_json"]:
        try:
            info = json.loads(credentials["service_account_json"])
        except (TypeError, ValueError):
            info = {}
    return {
        "configured": bool(info.get("client_email")),
        "spreadsheet_configured": bool(_extract_spreadsheet_id(credentials["spreadsheet_id"])),
        "service_account_email": info.get("client_email") or None,
        "tabs": [*DAYS, PAYMENTS_TAB],
        "reminder_tabs": list(DAYS),
        "payment_tab": PAYMENTS_TAB,
        "customers_tab": CUSTOMERS_TAB,
    }


def history(db: Session, limit: int = 50) -> list[ImportHistory]:
    """All preview/confirm batches created from Google Sheets, newest first."""
    return (
        db.query(ImportHistory)
        .filter(ImportHistory.file_type == "SHEET")
        .order_by(ImportHistory.created_at.desc())
        .limit(limit)
        .all()
    )


def preview(
    db: Session, payload: SheetPreviewRequest, actor: Any = None
) -> SheetPreviewOut:
    tab = payload.tab.strip()
    if tab == PAYMENTS_TAB:
        return _preview_payments(db, payload, actor)
    if tab not in DAYS:
        raise ValidationError(
            "Choose one of the configured tabs: " + ", ".join([*DAYS, PAYMENTS_TAB])
        )
    return _preview_reminders(db, payload, actor, tab)


def _preview_reminders(
    db: Session, payload: SheetPreviewRequest, actor: Any, tab: str
) -> SheetPreviewOut:
    spreadsheet_id = _spreadsheet_id(db, payload.spreadsheet_id)
    header, data_rows = _fetch_values(db, spreadsheet_id, tab)
    name_col = _find_column(header, NAME_ALIASES)
    amount_col = _find_column(header, AMOUNT_ALIASES)
    if not name_col or not amount_col:
        raise ValidationError(
            f"The {tab} tab needs a Name column and an Amount column"
        )

    directory, warnings = read_directory(db, spreadsheet_id)
    sync_stats = sync_directory(db, directory, actor)

    config = settings_service.get_reminder(db)
    batch = ImportHistory(
        filename=f"Google Sheets: {tab}",
        file_type="SHEET",
        mode=ImportMode.UPDATE_BALANCE.value,
        status=ImportStatus.PREVIEWED.value,
        row_count=len(data_rows),
        created_by=getattr(actor, "id", None),
        summary={"tab": tab, "spreadsheet_id": spreadsheet_id},
    )
    db.add(batch)
    db.flush()

    rows: list[SheetPreviewRowOut] = []
    reason_counts: dict[str, int] = {}
    phone: str | None = None
    for row_number, raw in data_rows:
        name = _clean_name(raw.get(name_col))
        amount, amount_error = _parse_amount(raw.get(amount_col))
        customer: Customer | None = None
        reason = "eligible"
        phone = None

        if len(name) < 2:
            reason = "invalid_name"
        elif amount_error:
            reason = "invalid_amount"
        else:
            entry = directory.get(_name_key(name))
            if not entry:
                reason = "unmatched"
            else:
                phone = entry["phone"]
                customer = customer_service.get_by_phone(db, phone)
                if not customer:
                    reason = "unmatched"
                else:
                    ok, candidate = _sheet_reason(
                        db, customer, amount or Decimal("0"), config, config.frequency_days
                    )
                    if not ok:
                        customer = None if candidate in ("invalid_phone",) else customer
                        reason = candidate

        breakdown_key = "opted_out" if reason == "no_consent" else reason
        reason_counts[breakdown_key] = reason_counts.get(breakdown_key, 0) + 1
        current = customer.outstanding_balance if customer and reason == "eligible" else None
        difference = (
            (amount - current)
            if customer and reason == "eligible" and amount is not None and current is not None
            else None
        )
        rows.append(
            SheetPreviewRowOut(
                row_number=row_number,
                name=name or "(blank)",
                phone=phone,
                amount=amount,
                current_balance=current,
                difference=difference,
                status="VALID" if reason == "eligible" else reason[:20],
                reason=reason,
                customer_id=customer.id if customer and reason == "eligible" else None,
                error=None if reason == "eligible" else REASON_LABELS.get(reason, reason),
            )
        )
        db.add(
            ImportRow(
                import_id=batch.id,
                row_number=row_number,
                name=name or None,
                raw_phone=phone,
                normalized_phone=phone,
                amount=amount,
                status="VALID" if reason == "eligible" else reason[:20],
                error=None if reason == "eligible" else REASON_LABELS.get(reason, reason),
                matched_customer_id=customer.id if customer and reason == "eligible" else None,
                current_balance=current,
                difference=difference,
            )
        )

    valid_rows = [row for row in rows if row.status == "VALID"]
    eligible_count = len(valid_rows)
    matched_count = sum(1 for row in rows if row.customer_id is not None)
    invalid_count = len(rows) - eligible_count
    batch.valid_count = eligible_count
    batch.invalid_count = invalid_count
    batch.summary = {
        "tab": tab,
        "spreadsheet_id": spreadsheet_id,
        "matched": matched_count,
        "unmatched": reason_counts.get("unmatched", 0),
        "opted_out": reason_counts.get("opted_out", 0),
        "reason_breakdown": reason_counts,
        "directory": sync_stats,
        "directory_warnings": warnings,
    }
    batch.errors = warnings
    db.commit()

    eligible_row_ids = [
        row.id
        for row in db.query(ImportRow)
        .filter(ImportRow.import_id == batch.id, ImportRow.status == "VALID")
        .order_by(ImportRow.row_number)
        .all()
    ]
    return SheetPreviewOut(
        import_id=batch.id,
        tab=tab,
        spreadsheet_id=spreadsheet_id,
        kind="reminders",
        row_count=len(rows),
        matched_count=matched_count,
        unmatched_count=reason_counts.get("unmatched", 0),
        opted_out_count=reason_counts.get("opted_out", 0),
        eligible_count=eligible_count,
        invalid_count=invalid_count,
        reason_breakdown=reason_counts,
        rows=rows,
        confirmation_token=_issue_token(batch.id, eligible_row_ids, tab),
        quiet_hours=reminder_service.is_quiet_hours(db),
        directory_created=sync_stats["created"],
        directory_updated=sync_stats["updated"],
        warnings=warnings,
    )


def confirm_send(
    db: Session, payload: SheetSendRequest, actor: Any = None
) -> SheetSendOut:
    batch = (
        db.query(ImportHistory)
        .filter(ImportHistory.id == payload.import_id, ImportHistory.file_type == "SHEET")
        .first()
    )
    if not batch:
        raise NotFoundError("Sheet preview not found")
    if batch.status == ImportStatus.COMPLETED.value:
        raise ConflictError("This sheet preview has already been sent")
    if batch.status != ImportStatus.PREVIEWED.value:
        raise ConflictError("This sheet preview can no longer be sent")

    rows = (
        db.query(ImportRow)
        .filter(ImportRow.import_id == batch.id, ImportRow.status == "VALID")
        .order_by(ImportRow.row_number)
        .all()
    )
    if not rows:
        raise ValidationError("This preview has no eligible rows to send")
    kind = str((batch.summary or {}).get("kind") or "reminders")
    if kind != "reminders":
        raise ValidationError(
            "This preview is not a reminder preview; use Confirm Payments for Customer Details rows"
        )
    tab = str((batch.summary or {}).get("tab") or "")
    if tab not in DAYS:
        raise ValidationError("This preview is not a reminder preview")
    _validate_token(payload.confirmation_token, batch.id, [row.id for row in rows], tab)

    if reminder_service.is_quiet_hours(db):
        raise ValidationError("Quiet hours are active.Sending is blocked until they end.")
    whatsapp_service.check_rate_limit(db)

    config = settings_service.get_reminder(db)
    updated = sent = failed = skipped = 0
    total = Decimal("0.00")
    for row in rows:
        customer = db.get(Customer, row.matched_customer_id) if row.matched_customer_id else None
        amount = balance_service.decimals(row.amount)
        if not customer:
            row.status = "SKIPPED"
            row.error = "Customer no longer exists"
            skipped += 1
            continue

        ok, reason = _sheet_reason(db, customer, amount, config, config.frequency_days)
        if not ok:
            row.status = "SKIPPED"
            row.error = REASON_LABELS.get(reason, reason)
            skipped += 1
            continue

        current = balance_service.decimals(customer.outstanding_balance)
        delta = amount - current
        if delta != 0:
            effect = (
                TransactionEffect.INCREASE.value
                if delta > 0
                else TransactionEffect.DECREASE.value
            )
            balance_service.add_transaction(
                db,
                customer,
                amount=abs(delta),
                transaction_type=TransactionType.ADJUSTMENT.value,
                effect=effect,
                description=f"Google Sheets {tab} balance update ({effect.lower()})",
                reference=str(batch.id),
                actor_id=getattr(actor, "id", None),
            )
        updated += 1
        total += amount

        try:
            link = payment_service.ensure_payment_link(
                db,
                customer,
                note=f"Google Sheets {tab} row {row.row_number}",
                actor=actor,
            )
        except AppError as exc:
            row.status = "LINK_FAILED"
            row.error = str(exc)[:500]
            failed += 1
            continue

        before = whatsapp_service.existing_message_ids(db, customer.id)
        try:
            message = whatsapp_service.send_reminder(
                db,
                customer,
                link.url,
                idempotency_suffix=f"sheet:{batch.id}:{row.id}",
            )
        except ValidationError as exc:
            row.status = "SKIPPED"
            row.error = str(exc)[:500]
            skipped += 1
            if "Daily WhatsApp sending limit" in str(exc):
                break
            continue

        if message.status == MessageStatus.FAILED.value:
            row.status = "FAILED"
            row.error = message.error or "WhatsApp delivery failed"
            failed += 1
            continue
        row.status = "SENT"
        row.error = None
        sent += 1
        if message.id not in before:
            customer.reminder_count = (customer.reminder_count or 0) + 1
            customer.last_reminder_at = message.sent_at or datetime.now(timezone.utc)

    summary = dict(batch.summary or {})
    summary.update(
        {
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "updated_balances": updated,
            "total_amount": str(total),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    batch.status = ImportStatus.COMPLETED.value
    batch.imported_count = updated
    batch.updated_count = sent
    batch.invalid_count = failed + skipped
    batch.summary = summary
    db.commit()
    audit_service.log(
        db,
        action="sheets.reminders.send",
        entity_type="SHEET_IMPORT",
        entity_id=str(batch.id),
        after={
            "tab": tab,
            "updated_balances": updated,
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "total_amount": str(total),
        },
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return SheetSendOut(
        import_id=batch.id,
        status=batch.status,
        updated_count=updated,
        sent_count=sent,
        failed_count=failed,
        skipped_count=skipped,
        total_amount=total,
        message=(
            f"Balances updated for {updated} customer(s); "
            f"{sent} reminder(s) sent, {failed} failed, {skipped} skipped."
        ),
    )


def _preview_payments(
    db: Session, payload: SheetPreviewRequest, actor: Any
) -> SheetPreviewOut:
    """Preview payments pasted into the Customer Details tab.

    This never changes a balance. Every valid row is recorded as a pending
    payment preview and only becomes a confirmed payment after the administrator
    presses Confirm Payments.
    """
    spreadsheet_id = _spreadsheet_id(db, payload.spreadsheet_id)
    header, data_rows = _fetch_values(db, spreadsheet_id, PAYMENTS_TAB)
    name_col = _find_column(header, NAME_ALIASES)
    paid_col = _find_column(header, PAID_ALIASES)
    if not name_col or not paid_col:
        raise ValidationError(
            f"The {PAYMENTS_TAB} tab needs a Name column and a Paid Amount column"
        )

    directory, warnings = read_directory(db, spreadsheet_id)
    sync_stats = sync_directory(db, directory, actor)

    batch = ImportHistory(
        filename=f"Google Sheets: {PAYMENTS_TAB}",
        file_type="SHEET",
        mode=ImportMode.ADJUSTMENT.value,
        status=ImportStatus.PREVIEWED.value,
        row_count=len(data_rows),
        created_by=getattr(actor, "id", None),
        summary={
            "tab": PAYMENTS_TAB,
            "spreadsheet_id": spreadsheet_id,
            "kind": "payments",
        },
    )
    db.add(batch)
    db.flush()

    rows: list[SheetPreviewRowOut] = []
    reason_counts: dict[str, int] = {}
    matched_count = 0
    for row_number, raw in data_rows:
        name = _clean_name(raw.get(name_col))
        paid, amount_error = _parse_amount(raw.get(paid_col))
        customer: Customer | None = None
        phone: str | None = None
        current: Decimal | None = None
        difference: Decimal | None = None
        balance_after: Decimal | None = None
        pending_payment_id: UUID | None = None
        reason = "ready"

        if len(name) < 2:
            reason = "invalid_name"
        elif amount_error:
            reason = "invalid_amount"
        else:
            entry = directory.get(_name_key(name))
            if not entry:
                reason = "unmatched"
            else:
                phone = entry["phone"]
                customer = customer_service.get_by_phone(db, phone)
                if not customer:
                    reason = "unmatched"
                else:
                    matched_count += 1
                    current = balance_service.decimals(customer.outstanding_balance)
                    if current <= 0:
                        reason = "no_outstanding"
                    elif paid is not None and paid > current:
                        reason = "exceeds_balance"
                    else:
                        pending = (
                            db.query(Payment)
                            .filter(
                                Payment.customer_id == customer.id,
                                Payment.status == PaymentStatus.AWAITING_CONFIRMATION.value,
                            )
                            .order_by(Payment.created_at)
                            .all()
                        )
                        if pending:
                            pending_payment_id = pending[0].id
                            matching = next(
                                (
                                    item
                                    for item in pending
                                    if balance_service.decimals(item.amount) == paid
                                ),
                                None,
                            )
                            if matching:
                                # Confirm the already-verified gateway payment; never
                                # record a second payment for the same collection.
                                pending_payment_id = matching.id
                                difference = -paid
                                balance_after = current - paid
                            else:
                                reason = "awaiting_confirmation"
                        else:
                            difference = -paid
                            balance_after = current - paid

        breakdown_key = reason
        reason_counts[breakdown_key] = reason_counts.get(breakdown_key, 0) + 1
        valid = reason == "ready"
        rows.append(
            SheetPreviewRowOut(
                row_number=row_number,
                name=name or "(blank)",
                phone=phone,
                amount=paid,
                current_balance=current,
                difference=difference,
                balance_after=balance_after,
                status="VALID" if valid else reason[:20],
                reason=reason,
                customer_id=customer.id if customer and (valid or pending_payment_id) else None,
                pending_payment_id=pending_payment_id,
                error=None if valid else REASON_LABELS.get(reason, reason),
            )
        )
        db.add(
            ImportRow(
                import_id=batch.id,
                row_number=row_number,
                name=name or None,
                raw_phone=phone,
                normalized_phone=phone,
                amount=paid,
                status="VALID" if valid else reason[:20],
                error=None if valid else REASON_LABELS.get(reason, reason),
                matched_customer_id=customer.id if customer and valid else None,
                current_balance=current,
                difference=difference,
            )
        )

    eligible_count = sum(1 for row in rows if row.status == "VALID")
    batch.valid_count = eligible_count
    batch.invalid_count = len(rows) - eligible_count
    batch.summary = {
        "tab": PAYMENTS_TAB,
        "spreadsheet_id": spreadsheet_id,
        "kind": "payments",
        "matched": matched_count,
        "unmatched": reason_counts.get("unmatched", 0),
        "reason_breakdown": reason_counts,
        "directory": sync_stats,
        "directory_warnings": warnings,
    }
    batch.errors = warnings
    db.commit()

    eligible_row_ids = [
        row.id
        for row in db.query(ImportRow)
        .filter(ImportRow.import_id == batch.id, ImportRow.status == "VALID")
        .order_by(ImportRow.row_number)
        .all()
    ]
    return SheetPreviewOut(
        import_id=batch.id,
        tab=PAYMENTS_TAB,
        spreadsheet_id=spreadsheet_id,
        kind="payments",
        row_count=len(rows),
        matched_count=matched_count,
        unmatched_count=reason_counts.get("unmatched", 0),
        opted_out_count=0,
        eligible_count=eligible_count,
        invalid_count=len(rows) - eligible_count,
        reason_breakdown=reason_counts,
        rows=rows,
        confirmation_token=_issue_token(batch.id, eligible_row_ids, PAYMENTS_TAB),
        quiet_hours=reminder_service.is_quiet_hours(db),
        directory_created=sync_stats["created"],
        directory_updated=sync_stats["updated"],
        warnings=warnings,
    )


def confirm_payments(
    db: Session, payload: SheetSendRequest, actor: Any = None
) -> SheetPaymentConfirmOut:
    """Admin-confirm payments from a Customer Details preview."""
    batch = (
        db.query(ImportHistory)
        .filter(ImportHistory.id == payload.import_id, ImportHistory.file_type == "SHEET")
        .first()
    )
    if not batch:
        raise NotFoundError("Sheet preview not found")
    if (batch.summary or {}).get("kind") != "payments":
        raise ValidationError("This preview is not a Customer Details payment preview")
    if batch.status == ImportStatus.COMPLETED.value:
        raise ConflictError("This sheet preview has already been confirmed")
    if batch.status != ImportStatus.PREVIEWED.value:
        raise ConflictError("This sheet preview can no longer be confirmed")

    rows = (
        db.query(ImportRow)
        .filter(ImportRow.import_id == batch.id, ImportRow.status == "VALID")
        .order_by(ImportRow.row_number)
        .all()
    )
    if not rows:
        raise ValidationError("This preview has no eligible payments to confirm")
    _validate_token(payload.confirmation_token, batch.id, [row.id for row in rows], PAYMENTS_TAB)

    confirmed = skipped = 0
    total = Decimal("0.00")
    for row in rows:
        customer = db.get(Customer, row.matched_customer_id) if row.matched_customer_id else None
        paid = balance_service.decimals(row.amount)
        if not customer:
            row.status = "SKIPPED"
            row.error = "Customer no longer exists"
            skipped += 1
            continue

        current = balance_service.decimals(customer.outstanding_balance)
        if current <= 0:
            row.status = "SKIPPED"
            row.error = REASON_LABELS["no_outstanding"]
            skipped += 1
            continue
        if paid > current:
            row.status = "SKIPPED"
            row.error = REASON_LABELS["exceeds_balance"]
            skipped += 1
            continue

        pending = (
            db.query(Payment)
            .filter(
                Payment.customer_id == customer.id,
                Payment.status == PaymentStatus.AWAITING_CONFIRMATION.value,
            )
            .order_by(Payment.created_at)
            .all()
        )
        matching = next(
            (item for item in pending if balance_service.decimals(item.amount) == paid),
            None,
        )
        if pending and not matching:
            row.status = "SKIPPED"
            row.error = REASON_LABELS["awaiting_confirmation"]
            skipped += 1
            continue

        if matching:
            payment = matching
        else:
            payment = payment_service.create_manual(
                db,
                ManualPaymentCreate(
                    customer_id=customer.id,
                    amount=paid,
                    method="OTHER",
                    reference=f"SHEET:{batch.id}:{row.row_number}",
                    notes=f"Paid amount pasted in {PAYMENTS_TAB} tab",
                    payment_date=datetime.now(timezone.utc),
                ),
                actor=actor,
            )
        payment_service.confirm(db, payment.id, actor=actor, send_confirmation=True)
        row.status = "CONFIRMED"
        row.error = None
        confirmed += 1
        total += paid

    summary = dict(batch.summary or {})
    summary.update(
        {
            "confirmed": confirmed,
            "skipped": skipped,
            "total_amount": str(total),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    batch.status = ImportStatus.COMPLETED.value
    batch.imported_count = confirmed
    batch.updated_count = confirmed
    batch.invalid_count = skipped
    batch.summary = summary
    db.commit()
    audit_service.log(
        db,
        action="sheets.payments.confirm",
        entity_type="SHEET_IMPORT",
        entity_id=str(batch.id),
        after={
            "tab": PAYMENTS_TAB,
            "confirmed": confirmed,
            "skipped": skipped,
            "total_amount": str(total),
        },
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return SheetPaymentConfirmOut(
        import_id=batch.id,
        status=batch.status,
        confirmed_count=confirmed,
        skipped_count=skipped,
        total_amount=total,
        message=(
            f"Confirmed {confirmed} payment(s) totalling {total}; "
            f"{skipped} row(s) skipped. Outstanding balances are updated."
        ),
    )


def reject_pending_payment(
    db: Session, payment_id: UUID, reason: str, actor: Any = None
) -> Payment:
    """Reject a verified webhook payment from the Sheets review screen."""
    payment = db.get(Payment, payment_id)
    if not payment:
        raise NotFoundError("Pending payment not found")
    if payment.status != PaymentStatus.AWAITING_CONFIRMATION.value:
        raise ConflictError("Only payments awaiting confirmation can be rejected")
    return payment_service.reject(db, payment_id, reason, actor=actor)
