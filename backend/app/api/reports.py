from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas import ExportRequest
from app.services import excel_service, report_service, settings_service

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return report_service.dashboard(db)


@router.get("/{report_name}")
def get_report(
    report_name: str,
    from_date: str | None = None,
    to_date: str | None = None,
    customer_id: str | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from datetime import datetime
    from uuid import UUID

    start = datetime.fromisoformat(from_date) if from_date else None
    end = datetime.fromisoformat(to_date) if to_date else None
    cid = UUID(customer_id) if customer_id else None
    reports = {
        "collections": lambda: report_service.collections_report(db, start, end),
        "weekly": lambda: report_service.collections_report(db, start, end),
        "monthly": lambda: report_service.collections_report(db, start, end),
        "outstanding": lambda: report_service.outstanding_report(db, cid),
        "customer-payments": lambda: report_service.collections_report(db, start, end),
        "whatsapp": lambda: report_service.whatsapp_report(db, start, end),
        "failed-payments": lambda: report_service.payments_report(db, "FAILED", start, end),
        "pending-verification": lambda: report_service.payments_report(
            db, "AWAITING_CONFIRMATION", start, end
        ),
        "confirmed-payments": lambda: report_service.collections_report(db, start, end),
    }
    if report_name not in reports:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"Unknown report: {report_name}")
    frame = reports[report_name]()
    return {
        "report": report_name,
        "rows": frame.to_dict(orient="records"),
        "count": len(frame),
    }


@router.post("/export")
def export_report(
    payload: ExportRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    if payload.report == "outstanding":
        frame = report_service.outstanding_report(db, payload.customer_id)
    elif payload.report == "whatsapp":
        frame = report_service.whatsapp_report(db, payload.from_date, payload.to_date)
    elif payload.report == "failed-payments":
        frame = report_service.payments_report(db, "FAILED", payload.from_date, payload.to_date)
    elif payload.report == "pending-verification":
        frame = report_service.payments_report(
            db, "AWAITING_CONFIRMATION", payload.from_date, payload.to_date
        )
    elif payload.report == "customers":
        frame = excel_service.customers_export(db)
    else:
        frame = report_service.collections_report(db, payload.from_date, payload.to_date)
    content, media_type, filename = excel_service.dataframe_to_bytes(
        frame,
        payload.format,
        f"{settings_service.company_name(db).replace(' ', '_')}_{payload.report}",
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
