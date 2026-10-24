"""Owner-facing import wizard: upload, mapping, dry-run, confirmation and report."""
from typing import NoReturn, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core import import_service
from app.api.core.i18n import _
from app.api.core.pagination import MAX_PAGE, PaginatedResponse, PaginationParams
from app.api.middleware.rate_limit import EXPORT_LIMIT, limiter
from app.db.models.import_batch import ImportRow
from app.db.models.user import User
from app.schemas.import_batch import (
    ImportBatchOut,
    ImportConfirmIn,
    ImportMappingIn,
    ImportPreviewIn,
    ImportRowOut,
    ImportSummary,
)

router = APIRouter()

ROW_STATUSES = ("pending", "valid", "error", "imported", "skipped", "failed")


def _fail(code: str, locale: str, status_code: int = 422, **extra) -> NoReturn:
    detail = {"message": _ (code, locale), "code": code, **extra}
    raise HTTPException(status_code=status_code, detail=detail)


def _scope(db: Session, user: User, locale: str) -> import_service.ImportScope:
    try:
        return import_service.scope_for(db, user)
    except import_service.ImportFileError as exc:
        _fail(exc.code, locale, exc.status_code)


def _batch_out(db: Session, scope, batch) -> ImportBatchOut:
    return ImportBatchOut(**import_service.batch_payload(db, scope, batch))


def _require_batch(db: Session, scope, batch_id: int, locale: str):
    batch = import_service.find_batch(db, scope, batch_id)
    if batch is None:
        _fail("import_batch_not_found", locale, 404)
    return batch


@router.post("", response_model=ImportBatchOut, summary="Upload a client import file")
@limiter.limit(EXPORT_LIMIT)
async def upload_import(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    scope = _scope(db, current_user, locale)
    content = await file.read(import_service.MAX_UPLOAD_BYTES + 1)
    try:
        batch = import_service.create_batch(
            db,
            scope,
            current_user,
            content=content,
            filename=file.filename,
            content_type=file.content_type,
        )
    except import_service.ImportSourceExists as exc:
        _fail(
            "import_source_exists",
            locale,
            409,
            existing_batch_id=exc.batch.id,
        )
    except import_service.ImportFileError as exc:
        _fail(exc.code, locale, exc.status_code)
    return _batch_out(db, scope, batch)


@router.get("", response_model=PaginatedResponse[ImportBatchOut], summary="List import batches")
def list_imports(
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    scope = _scope(db, current_user, locale)
    pagination = PaginationParams(page=page, per_page=per_page)
    query = import_service.scoped_query(db, scope, import_service.ImportBatch)
    total = query.count()
    batches = (
        query.order_by(import_service.ImportBatch.created_at.desc(), import_service.ImportBatch.id.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
        .all()
    )
    for batch in batches:
        import_service.expire_source(db, batch)
    return PaginatedResponse.create(
        items=[_batch_out(db, scope, batch) for batch in batches],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{batch_id}", response_model=ImportBatchOut, summary="Read an import batch")
def get_import(
    batch_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    scope = _scope(db, current_user, locale)
    batch = _require_batch(db, scope, batch_id, locale)
    import_service.expire_source(db, batch)
    return _batch_out(db, scope, batch)


@router.put("/{batch_id}/mapping", response_model=ImportBatchOut, summary="Save the column mapping")
def save_import_mapping(
    batch_id: int,
    payload: ImportMappingIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    scope = _scope(db, current_user, locale)
    batch = _require_batch(db, scope, batch_id, locale)
    try:
        import_service.save_mapping(db, batch, payload.mapping)
    except import_service.ImportFileError as exc:
        _fail(exc.code, locale, exc.status_code)
    return _batch_out(db, scope, batch)


@router.post("/{batch_id}/preview", response_model=ImportBatchOut, summary="Dry-run the mapped rows")
def preview_import(
    batch_id: int,
    payload: ImportPreviewIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    scope = _scope(db, current_user, locale)
    batch = _require_batch(db, scope, batch_id, locale)
    try:
        import_service.build_preview(db, batch, scope, current_user, payload.duplicate_policy)
    except import_service.ImportFileError as exc:
        _fail(exc.code, locale, exc.status_code)
    return _batch_out(db, scope, batch)


@router.get("/{batch_id}/rows", response_model=PaginatedResponse[ImportRowOut], summary="Read preview rows and errors")
def list_import_rows(
    batch_id: int,
    status: Optional[str] = Query(None, max_length=16),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    if status is not None and status not in ROW_STATUSES:
        _fail("invalid_choice", locale, 422)
    scope = _scope(db, current_user, locale)
    batch = _require_batch(db, scope, batch_id, locale)
    pagination = PaginationParams(page=page, per_page=per_page)
    query = db.query(ImportRow).filter(ImportRow.batch_id == batch.id)
    if status:
        query = query.filter(ImportRow.status == status)
    total = query.count()
    rows = (
        query.order_by(ImportRow.row_number)
        .offset(pagination.offset)
        .limit(pagination.limit)
        .all()
    )
    items = [
        ImportRowOut(**import_service.row_payload(row))
        for row in rows
    ]
    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/{batch_id}/confirm", response_model=ImportSummary, summary="Confirm and execute an import batch")
def confirm_import(
    batch_id: int,
    payload: ImportConfirmIn,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    scope = _scope(db, current_user, locale)
    batch = _require_batch(db, scope, batch_id, locale)
    if (
        payload.source_hash != batch.source_hash
        or payload.mapping_version != batch.mapping_version
        or payload.preview_version != batch.preview_version
        or payload.duplicate_policy != batch.duplicate_policy
    ):
        _fail("import_preview_outdated", locale, 409)
    try:
        executed = import_service.execute_batch(db, batch, scope, current_user, expected=(
            payload.source_hash, payload.mapping_version, payload.preview_version, payload.duplicate_policy))
    except import_service.ImportFileError as exc:
        _fail(exc.code, locale, exc.status_code)
    if executed:
        from app.api.core.redis_client import invalidate_client, invalidate_client_list

        updated_ids = (
            db.query(ImportRow.client_id)
            .filter(
                ImportRow.batch_id == batch.id,
                ImportRow.action == "update",
                ImportRow.client_id.isnot(None),
            )
            .all()
        )
        try:
            for (client_id,) in updated_ids:
                invalidate_client(client_id)
            invalidate_client_list()
        except Exception:
            pass
    return ImportSummary(
        batch=_batch_out(db, scope, batch),
        already_completed=not executed,
    )


@router.get("/{batch_id}/report.csv", summary="Export the batch result and row errors")
@limiter.limit(EXPORT_LIMIT)
def export_import_report(
    batch_id: int,
    request: Request,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    locale: str = Depends(deps.get_locale),
):
    from app.api.core.export import export_to_csv

    scope = _scope(db, current_user, locale)
    batch = _require_batch(db, scope, batch_id, locale)
    rows = (
        db.query(ImportRow)
        .filter(ImportRow.batch_id == batch.id)
        .order_by(ImportRow.row_number)
        .all()
    )
    export_data = []
    for row in rows:
        client_data = row.client_data or {}
        visit_data = row.visit_data or {}
        export_data.append(
            {
                "Row": row.row_number,
                "Status": row.status,
                "Action": row.action or "",
                "Error code": row.error_code or "",
                "Error detail": row.error_detail or "",
                "Client ID": row.client_id or "",
                "Visit ID": row.visit_id or "",
                "Name": client_data.get("full_name") or "",
                "Phone": client_data.get("phone") or "",
                "Email": client_data.get("email") or "",
                "Visit date": visit_data.get("date_start") or "",
                "Price": visit_data.get("price") if visit_data.get("price") is not None else "",
            }
        )
    export_data.append(
        {
            "Row": "",
            "Status": "summary",
            "Action": "batch",
            "Error code": "",
            "Error detail": (
                f"batch_status={batch.status}; total={batch.row_count}; "
                f"valid={batch.valid_count}; errors={batch.error_count}; "
                f"created={batch.created_count}; updated={batch.updated_count}; "
                f"skipped={batch.skipped_count}"
            ),
            "Client ID": "",
            "Visit ID": "",
            "Name": "",
            "Phone": "",
            "Email": "",
            "Visit date": "",
            "Price": "",
        }
    )
    return export_to_csv(export_data, filename=f"import_{batch.id}_report.csv")
