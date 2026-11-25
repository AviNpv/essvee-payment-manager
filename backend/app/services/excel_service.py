"""Spreadsheet export helpers.

Excel/CSV import was removed. Customer data now enters the app through the
Google Sheets workflow, while exports remain available in Reports.
"""

import io

import pandas as pd
from sqlalchemy.orm import Session

from app.models import Customer


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
