from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import ImportHistory, User
from app.schemas import (
    ExcelImportOut,
    ExcelImportRequest,
    ExcelPreviewOut,
    ImportOut,
)
from app.services import excel_service

router = APIRouter(prefix="/excel", tags=["Excel"])


@router.post("/preview", response_model=ExcelPreviewOut)
async def preview(
    file: UploadFile = File(...),
    mode: str = Form("UPDATE_BALANCE"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = await file.read()
    return excel_service.preview_upload(
        db, file.filename or "upload.xlsx", content, mode=mode, actor=user
    )


@router.post("/import", response_model=ExcelImportOut)
def do_import(
    payload: ExcelImportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return excel_service.confirm_import(
        db, payload.import_id, confirm_differences=payload.confirm_differences, actor=user
    )


@router.get("/template")
def template(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    content = excel_service.template_bytes()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="essvee_customer_template.xlsx"'},
    )


@router.get("/export")
def export_customers(
    format: str = "xlsx",
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    frame = excel_service.customers_export(db)
    from app.services.settings_service import company_name

    content, media_type, filename = excel_service.dataframe_to_bytes(
        frame, format, f"{company_name(db).replace(' ', '_')}_customers"
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/imports", response_model=list[ImportOut])
def list_imports(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
):
    return [
        ImportOut.model_validate(row, from_attributes=True)
        for row in excel_service.list_imports(db)
    ]
