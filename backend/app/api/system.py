from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models import AuditLog, SchedulerRun, User
from app.schemas import AuditLogOut
from app.services import reminder_service, settings_service

router = APIRouter(tags=["System"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "unavailable"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "app": settings.app_name,
        "environment": settings.environment,
        "database": database,
        "timezone": settings.timezone,
        "razorpay_mode": settings_service.razorpay_credentials(db)["mode"],
    }


@router.get("/webhook-events")
def webhook_events(
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from app.models import WebhookEvent

    rows = (
        db.query(WebhookEvent)
        .order_by(WebhookEvent.received_at.desc())
        .limit(min(limit, 300))
        .all()
    )
    return [
        {
            "id": str(r.id),
            "provider": r.provider,
            "event_type": r.event_type,
            "signature_valid": r.signature_valid,
            "processed": r.processed,
            "processing_error": r.processing_error,
            "received_at": r.received_at.isoformat() if r.received_at else None,
        }
        for r in rows
    ]


@router.get("/audit-logs")
def audit_logs(
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 500)).all()
    return [AuditLogOut.model_validate(row, from_attributes=True) for row in rows]


@router.get("/scheduler/runs")
def scheduler_runs(
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = (
        db.query(SchedulerRun).order_by(SchedulerRun.created_at.desc()).limit(min(limit, 200)).all()
    )
    return [
        {
            "id": str(r.id),
            "job_name": r.job_name,
            "status": r.status,
            "processed": r.processed,
            "failed": r.failed,
            "detail": r.detail,
            "started_at": r.started_at.isoformat() if r.started_at else None,
        }
        for r in rows
    ]


@router.post("/scheduler/run")
def run_job(
    job: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    jobs = {
        "reminders": reminder_service.run_reminder_job,
        "retry": reminder_service.run_retry_job,
        "reconcile": reminder_service.run_link_reconcile_job,
    }
    if job not in jobs:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"Unknown job: {job}")
    return jobs[job](db)
