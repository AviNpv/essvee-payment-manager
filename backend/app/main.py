"""ESSVEE Payment Manager – FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    auth,
    customers,
    excel,
    pay,
    payments,
    reports,
    settings,
    sheets,
    system,
    webhooks,
    whatsapp,
)
from app.core.config import settings as env
from app.core.errors import AppError, app_error_handler
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services import reminder_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("essvee")

scheduler: BackgroundScheduler | None = None


def _run_job(fn) -> None:
    with SessionLocal() as db:
        try:
            fn(db)
        except Exception as exc:
            logger.error("Scheduled job %s failed: %s", getattr(fn, "__name__", fn), exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    global scheduler
    if env.enable_scheduler:
        scheduler = BackgroundScheduler(timezone=env.timezone)
        scheduler.add_job(
            lambda: _run_job(reminder_service.run_reminder_job),
            IntervalTrigger(minutes=1),
            id="reminders",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.add_job(
            lambda: _run_job(reminder_service.run_retry_job),
            IntervalTrigger(minutes=2),
            id="message_retry",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.add_job(
            lambda: _run_job(reminder_service.run_link_reconcile_job),
            IntervalTrigger(minutes=5),
            id="link_reconcile",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.start()
        logger.info("Scheduler started (timezone=%s)", env.timezone)
    yield
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


app = FastAPI(
    title=env.app_name,
    description="Private WhatsApp payment collection platform for ESSVEE Associates",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=env.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(AppError, app_error_handler)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request",
                "details": [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                    for e in exc.errors()
                ],
            }
        },
    )


app.include_router(auth.router, prefix="/api")
app.include_router(customers.router, prefix="/api")
app.include_router(excel.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(whatsapp.router, prefix="/api")
app.include_router(webhooks.router, prefix="/api")
app.include_router(pay.router)
app.include_router(reports.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(sheets.router, prefix="/api")
app.include_router(system.router, prefix="/api")


@app.get("/health", include_in_schema=False)
def root_health():
    return {"status": "ok", "app": env.app_name}


# Serve the production React build when it exists (Docker/Render single-service mode).
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        index = FRONTEND_DIST / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse(status_code=404, content={"detail": "Not found"})
