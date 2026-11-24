"""Excel/CSV import with preview, validation, reconciliation and audit trail."""

import io
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import (
    Customer,
    ImportHistory,
    ImportMode,
    ImportRow,
    ImportStatus,
    TransactionEffect,
    TransactionType,
)
from app.schemas import ExcelImportOut, ExcelPreviewOut, PreviewRowOut
from app.services import audit_service, balance_service, customer_service

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("customer name", "name", "customer", "party", "client name"),
    "phone": (
        "whatsapp number",
        "whatsapp",
        "phone",
        "mobile",
        "mobile number",
        "phone number",
        "contact",
    ),
    "amount": (
        "outstanding amount",
        "outstanding",
        "amount",
        "balance",
        "due amount",
        "pending amount",
    ),
}


def _find_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {str(c).strip().lower(): c for c in columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    for col in columns:
        low = str(col).strip().lower()
        if any(alias in low for alias in aliases):
            return col
    return None


def parse_upload(filename: str, content: bytes) -> pd.DataFrame:
    lower = filename.lower()
    try:
        if lower.endswith(".csv"):
            return pd.read_csv(io.BytesIO(content), dtype=str).fillna("")
        if lower.endswith((".xlsx", ".xls")):
            return pd.read_excel(io.BytesIO(content), dtype=str).fillna("")
    except Exception as exc:
        raise ValidationError(f"Could not read the uploaded file: {type(exc).__name__}") from exc
    raise ValidationError("Unsupported file format. Upload .xlsx, .xls or .csv.")


def _amount(value: Any) -> tuple[Decimal | None, str | None]:
    if value is None or str(value).strip() == "":
        return None, "Outstanding amount is required"
    text = str(value).strip().replace(",", "").replace("₹", "").replace(" ", "")
    try:
        amount = Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None, "Invalid amount"
    if amount < 0:
        return None, "Negative amounts are not allowed in this mode"
    return amount, None


def preview_upload(
    db: Session,
    filename: str,
    content: bytes,
    *,
    mode: str = ImportMode.UPDATE_BALANCE.value,
    actor: Any = None,
) -> ExcelPreviewOut:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ("xlsx", "xls", "csv"):
        raise ValidationError("Only .xlsx, .xls and .csv files are supported")
    if len(content) > 10 * 1024 * 1024:
        raise ValidationError("File is too large (maximum 10 MB)")
    frame = parse_upload(filename, content)
    if frame.empty:
        raise ValidationError("The file contains no data rows")
    columns = list(frame.columns)
    name_col = _find_column(columns, HEADER_ALIASES["name"])
    phone_col = _find_column(columns, HEADER_ALIASES["phone"])
    amount_col = _find_column(columns, HEADER_ALIASES["amount"])
    missing = [
        label
        for label, col in (("Customer Name", name_col), ("WhatsApp Number", phone_col), ("Outstanding Amount", amount_col))
        if not col
    ]
    if missing:
        raise ValidationError("Missing required column(s): " + ", ".join(missing))
    if mode not in {m.value for m in ImportMode}:
        raise ValidationError("Invalid import mode")

    batch = ImportHistory(
        filename=filename[:255],
        file_type=extension,
        mode=mode,
        status=ImportStatus.PREVIEWED.value,
        row_count=len(frame),
        created_by=getattr(actor, "id", None),
    )
    db.add(batch)
    db.flush()

    seen: set[str] = set()
    preview_rows: list[PreviewRowOut] = []
    differences = 0
    valid_count = 0
    for index, record in frame.iterrows():
        row_number = int(index) + 2  # header is row 1
        name = str(record.get(name_col, "") or "").strip()
        raw_phone = str(record.get(phone_col, "") or "").strip()
        amount, amount_error = _amount(record.get(amount_col))
        error: str | None = None
        phone: str | None = None
        if len(name) < 2:
            error = "Customer name is missing or too short"
        try:
            phone = customer_service.normalize_phone(raw_phone)
        except ValidationError as exc:
            error = error or str(exc)
        if amount_error:
            error = error or amount_error
        if phone:
            if phone in seen:
                error = "Duplicate WhatsApp number in file"
            else:
                seen.add(phone)
        matched = db.query(Customer).filter(Customer.whatsapp_number == phone).first() if phone else None
        if not error and matched and mode == ImportMode.UPDATE_BALANCE.value:
            difference = (amount or Decimal("0")) - balance_service.decimals(matched.outstanding_balance)
            if difference != 0:
                differences += 1
        else:
            difference = Decimal("0")
        status = "VALID" if not error else "INVALID"
        if status == "VALID":
            valid_count += 1
        db.add(
            ImportRow(
                import_id=batch.id,
                row_number=row_number,
                name=name[:200],
                raw_phone=raw_phone[:50],
                normalized_phone=phone,
                amount=amount,
                status=status,
                error=error,
                matched_customer_id=matched.id if matched else None,
                current_balance=matched.outstanding_balance if matched else None,
                difference=difference,
            )
        )
        preview_rows.append(
            PreviewRowOut(
                row_number=row_number,
                name=name,
                phone=raw_phone,
                normalized_phone=phone,
                amount=amount,
                status=status,
                error=error,
                current_balance=matched.outstanding_balance if matched else None,
                difference=difference,
                customer_id=matched.id if matched else None,
            )
        )
    batch.valid_count = valid_count
    batch.invalid_count = len(frame) - valid_count
    batch.summary = {"differences": differences, "mode": mode}
    db.commit()
    return ExcelPreviewOut(
        import_id=batch.id,
        filename=filename,
        row_count=len(frame),
        valid_count=valid_count,
        invalid_count=len(frame) - valid_count,
        rows=preview_rows,
        mode=mode,
        requires_difference_confirmation=differences > 0,
    )


def confirm_import(
    db: Session, import_id: UUID, confirm_differences: bool = False, actor: Any = None
) -> ExcelImportOut:
    batch = db.get(ImportHistory, import_id)
    if not batch:
        raise NotFoundError("Import preview not found")
    if batch.status == ImportStatus.COMPLETED.value:
        raise ConflictError("This import has already been applied")
    differences = (batch.summary or {}).get("differences", 0)
    if differences and not confirm_differences:
        raise ValidationError(
            f"{differences} row(s) change existing balances. Confirm the differences to proceed."
        )
    imported = updated = 0
    total = Decimal("0.00")
    for row in (
        db.query(ImportRow)
        .filter(ImportRow.import_id == batch.id, ImportRow.status == "VALID")
        .order_by(ImportRow.row_number)
        .all()
    ):
        amount = balance_service.decimals(row.amount)
        total += amount
        customer = (
            db.get(Customer, row.matched_customer_id) if row.matched_customer_id else None
        )
        if not customer and row.normalized_phone:
            customer = (
                db.query(Customer)
                .filter(Customer.whatsapp_number == row.normalized_phone)
                .first()
            )
        if customer:
            current = balance_service.decimals(customer.outstanding_balance)
            if batch.mode == ImportMode.UPDATE_BALANCE.value:
                delta = amount - current
                if delta > 0:
                    balance_service.add_transaction(
                        db,
                        customer,
                        amount=delta,
                        transaction_type=TransactionType.ADJUSTMENT.value,
                        effect=TransactionEffect.INCREASE.value,
                        description=f"Excel import {batch.filename} - balance update (increase)",
                        reference=str(batch.id),
                        actor_id=getattr(actor, "id", None),
                    )
                elif delta < 0:
                    balance_service.add_transaction(
                        db,
                        customer,
                        amount=-delta,
                        transaction_type=TransactionType.ADJUSTMENT.value,
                        effect=TransactionEffect.DECREASE.value,
                        description=f"Excel import {batch.filename} - balance update (decrease)",
                        reference=str(batch.id),
                        actor_id=getattr(actor, "id", None),
                    )
            else:
                if amount > current:
                    raise ConflictError(
                        f"Row {row.row_number}: payment adjustment ₹{amount} exceeds outstanding ₹{current}"
                    )
                balance_service.add_transaction(
                    db,
                    customer,
                    amount=amount,
                    transaction_type=TransactionType.ADJUSTMENT.value,
                    effect=TransactionEffect.DECREASE.value,
                    description=f"Excel import {batch.filename} - payment adjustment",
                    reference=str(batch.id),
                    actor_id=getattr(actor, "id", None),
                )
            updated += 1
        else:
            opening = amount if batch.mode == ImportMode.UPDATE_BALANCE.value else Decimal("0")
            customer = Customer(
                name=row.name,
                whatsapp_number=row.normalized_phone,
                opening_balance=opening,
                outstanding_balance=opening,
                created_by=getattr(actor, "id", None),
            )
            db.add(customer)
            db.flush()
            balance_service.opening_transaction(db, customer, getattr(actor, "id", None))
            if batch.mode == ImportMode.ADJUSTMENT.value:
                if amount > 0:
                    raise ConflictError(
                        f"Row {row.row_number}: payment adjustment for a new customer is not supported"
                    )
            imported += 1
    batch.status = ImportStatus.COMPLETED.value
    batch.imported_count = imported
    batch.updated_count = updated
    batch.summary = {
        **(batch.summary or {}),
        "total_amount": str(total),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    db.commit()
    audit_service.log(
        db,
        action="excel.import",
        entity_type="IMPORT",
        entity_id=str(batch.id),
        after={
            "filename": batch.filename,
            "mode": batch.mode,
            "imported": imported,
            "updated": updated,
            "invalid": batch.invalid_count,
        },
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        commit=True,
    )
    return ExcelImportOut(
        import_id=batch.id,
        status=batch.status,
        imported_count=imported,
        updated_count=updated,
        invalid_count=batch.invalid_count,
        total_amount=total,
        message=f"Imported {imported} new customer(s) and updated {updated} existing customer(s).",
    )


def list_imports(db: Session, limit: int = 50) -> list[ImportHistory]:
    return db.query(ImportHistory).order_by(ImportHistory.created_at.desc()).limit(limit).all()


def template_bytes() -> bytes:
    frame = pd.DataFrame(
        [
            {"Customer Name": "Rahul", "WhatsApp Number": "919876543210", "Outstanding Amount": 2500},
            {"Customer Name": "Anil", "WhatsApp Number": "919876543211", "Outstanding Amount": 5000},
            {"Customer Name": "Arun", "WhatsApp Number": "919876543212", "Outstanding Amount": 1200},
        ]
    )
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Customers")
        worksheet = writer.sheets["Customers"]
        for column, width in zip("ABC", (24, 24, 24)):
            worksheet.column_dimensions[column].width = width
    return output.getvalue()


def dataframe_to_bytes(frame: pd.DataFrame, format: str, filename: str) -> tuple[bytes, str, str]:
    format = format.lower()
    if format == "csv":
        return (
            frame.to_csv(index=False).encode("utf-8-sig"),
            "text/csv; charset=utf-8",
            f"{filename}.csv",
        )
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Report")
        worksheet = writer.sheets["Report"]
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value or "")) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(length + 2, 12), 45)
    return (
        output.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"{filename}.xlsx",
    )


def customers_export(db: Session) -> pd.DataFrame:
    rows = []
    for customer in db.query(Customer).order_by(Customer.name).all():
        rows.append(
            {
                "Customer Name": customer.name,
                "WhatsApp Number": customer.whatsapp_number,
                "Current Outstanding Balance": customer.outstanding_balance,
                "Total Payments Received": customer.total_paid,
                "Last Payment Date": (
                    customer.last_payment_at.strftime("%Y-%m-%d") if customer.last_payment_at else ""
                ),
                "Customer Status": customer.status,
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "Customer Name",
            "WhatsApp Number",
            "Current Outstanding Balance",
            "Total Payments Received",
            "Last Payment Date",
            "Customer Status",
        ],
    )
