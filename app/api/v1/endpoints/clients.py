from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.core.i18n import _, localize_response
from app.api.core.logger import get_logger
from app.api.core.pagination import PaginationParams, PaginatedResponse
from app.api.core.qr_code import generate_qr_code_base64, parse_qr_code_data, verify_qr_code_hash
from app.api.crud import crud_client, crud_visit
from app.db.models.user import User
from app.schemas.client import ClientCreate, ClientOut, ClientUpdate
from app.schemas.qr_code import QRCodeGenerateResponse, QRCodeAccessRequest, ClientHistoryResponse

router = APIRouter()
logger = get_logger("clients")


@router.post(
    "/",
    response_model=ClientOut,
    summary="Create a new client",
    description="Create a new client record. Requires admin or specialist role.",
    responses={
        201: {
            "description": "Client created successfully",
            "content": {
                "application/json": {
                    "example": {
                        "id": 1,
                        "owner_id": 1,
                        "full_name": "Jane Smith",
                        "birth_date": "1990-01-15",
                        "gender": "female",
                        "contact_phone": "+1234567890",
                        "contact_email": "jane@example.com",
                        "spending": 0.0,
                        "created_at": "2024-01-15T10:00:00"
                    }
                }
            }
        },
        403: {"description": "Insufficient permissions"},
        422: {"description": "Validation error"},
    }
)
def create_client(
    client: ClientCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    logger.info(f"Creating client: name={client.full_name}, owner_id={current_user.id}")
    # enforce ownership if not admin
    if current_user.role != "admin" and client.owner_id != current_user.id:
        logger.warning(f"Client creation denied: user_id={current_user.id}, attempted_owner_id={client.owner_id}")
        raise HTTPException(status_code=403, detail=_("cannot_create_for_owner", locale))
    result = crud_client.create_client(db, client)
    logger.info(f"Client created successfully: id={result.id}, name={result.full_name}")
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)


@router.get("/", response_model=PaginatedResponse[ClientOut])
def list_clients(
    owner_id: Optional[int] = None,
    phone: Optional[str] = None,
    name: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    include_deleted: bool = Query(False, description="Include soft-deleted clients"),
    include_archived: bool = Query(False, description="Include archived clients"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin":
        owner_id = current_user.id
        # Non-admin users cannot see deleted/archived
        include_deleted = False
        include_archived = False
    
    pagination = PaginationParams(page=page, per_page=per_page)
    clients, total = crud_client.list_clients(
        db,
        owner_id=owner_id,
        phone=phone,
        name=name,
        pagination=pagination,
        include_deleted=include_deleted,
        include_archived=include_archived,
    )
    
    localized_items = [
        localize_response(c.dict() if hasattr(c, 'dict') else c, locale) for c in clients
    ]
    
    return PaginatedResponse.create(
        items=localized_items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{client_id}", response_model=ClientOut)
def get_client(
    client_id: int,
    include_deleted: bool = Query(False, description="Include soft-deleted client"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    if current_user.role != "admin":
        include_deleted = False
    
    client = crud_client.get_client(db, client_id, include_deleted=include_deleted)
    if not client:
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    if current_user.role != "admin" and client.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    return localize_response(client.dict() if hasattr(client, 'dict') else client, locale)


@router.put("/{client_id}", response_model=ClientOut)
def update_client(
    client_id: int,
    payload: ClientUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    logger.info(f"Updating client: id={client_id}, user_id={current_user.id}")
    client = crud_client.get_client(db, client_id)
    if not client:
        logger.warning(f"Client not found: id={client_id}")
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    if current_user.role != "admin" and client.owner_id != current_user.id:
        logger.warning(f"Client update denied: user_id={current_user.id}, client_id={client_id}")
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    result = crud_client.update_client(db, client, payload)
    logger.info(f"Client updated successfully: id={result.id}")
    return localize_response(result.dict() if hasattr(result, 'dict') else result, locale)


@router.get("/export/csv")
def export_clients_csv(
    owner_id: Optional[int] = None,
    phone: Optional[str] = None,
    name: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    """Export clients to CSV."""
    from app.api.core.export import export_to_csv, prepare_client_export_data
    
    if current_user.role != "admin":
        owner_id = current_user.id
    
    clients, _ = crud_client.list_clients(db, owner_id=owner_id, phone=phone, name=name)
    export_data = prepare_client_export_data(clients)
    return export_to_csv(export_data, filename="clients_export.csv")


@router.get("/export/excel")
def export_clients_excel(
    owner_id: Optional[int] = None,
    phone: Optional[str] = None,
    name: Optional[str] = None,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    """Export clients to Excel."""
    from app.api.core.export import export_to_excel, prepare_client_export_data
    
    if current_user.role != "admin":
        owner_id = current_user.id
    
    clients, _ = crud_client.list_clients(db, owner_id=owner_id, phone=phone, name=name)
    export_data = prepare_client_export_data(clients)
    return export_to_excel(export_data, filename="clients_export.xlsx", sheet_name="Clients")


@router.post("/{client_id}/qr-code/generate", response_model=QRCodeGenerateResponse)
def generate_client_qr_code(
    client_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.role_required("admin", "specialist")),
    locale: str = Depends(deps.get_locale),
):
    """Generate QR code and access password for a client."""
    logger.info(f"Generating QR code for client: id={client_id}, user_id={current_user.id}")
    
    # Check client exists and user has access
    client = crud_client.get_client(db, client_id)
    if not client:
        logger.warning(f"Client not found: id={client_id}")
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    
    if current_user.role != "admin" and client.owner_id != current_user.id:
        logger.warning(f"QR code generation denied: user_id={current_user.id}, client_id={client_id}")
        raise HTTPException(status_code=403, detail=_("not_allowed", locale))
    
    # Generate QR code and password
    try:
        qr_data, access_password = crud_client.generate_client_qr_code(db, client_id)
    except ValueError as e:
        logger.error(f"Error generating QR code: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    
    # Generate QR code image
    qr_image_base64 = generate_qr_code_base64(qr_data)
    
    # Build access URL (assuming frontend URL from settings or config)
    from app.api.core.config import settings
    base_url = getattr(settings, 'frontend_url', 'http://localhost:3000')
    qr_code_url = f"{base_url}/client/history?qr={qr_data}"
    
    logger.info(f"QR code generated successfully: client_id={client_id}")
    
    return QRCodeGenerateResponse(
        qr_code_data=qr_data,
        qr_code_image_base64=qr_image_base64,
        access_password=access_password,
        qr_code_url=qr_code_url,
    )


@router.post("/qr-code/access", response_model=ClientHistoryResponse)
def access_client_history_by_qr(
    request: QRCodeAccessRequest,
    db: Session = Depends(deps.get_db),
    locale: str = Depends(deps.get_locale),
):
    """Access client history using QR code and password."""
    logger.info(f"QR code access attempt: qr_data={request.qr_code_data[:20]}...")
    
    # Parse QR code data
    parsed = parse_qr_code_data(request.qr_code_data)
    if not parsed:
        logger.warning("Invalid QR code format")
        raise HTTPException(status_code=400, detail=_("invalid_qr_code", locale))
    
    client_id, hash_value = parsed
    
    # Verify hash
    if not verify_qr_code_hash(client_id, hash_value):
        logger.warning(f"Invalid QR code hash: client_id={client_id}")
        raise HTTPException(status_code=400, detail=_("invalid_qr_code", locale))
    
    # Get client by QR code
    client = crud_client.get_client_by_qr_code(db, request.qr_code_data)
    if not client:
        logger.warning(f"Client not found for QR code: client_id={client_id}")
        raise HTTPException(status_code=404, detail=_("client_not_found", locale))
    
    # Verify password
    if not crud_client.verify_client_access_password(db, client_id, request.password):
        logger.warning(f"Invalid password for QR code access: client_id={client_id}")
        raise HTTPException(status_code=401, detail=_("invalid_password", locale))
    
    # Get client visits
    visits, total = crud_visit.list_visits(db, client_id=client_id)
    
    # Calculate total spending
    total_spending = sum(v.price or 0 for v in visits)
    
    # Get last visit date
    last_visit_date = None
    if visits:
        last_visit = max(visits, key=lambda v: v.date_start)
        last_visit_date = last_visit.date_start.isoformat()
    
    # Format visits for response
    visits_data = [
        {
            "id": v.id,
            "date_start": v.date_start.isoformat(),
            "date_end": v.date_end.isoformat() if v.date_end else None,
            "service_name": v.service_name,
            "price": v.price,
            "status": v.status,
            "note": v.note,
        }
        for v in visits
    ]
    
    logger.info(f"QR code access successful: client_id={client_id}, visits={total}")
    
    return ClientHistoryResponse(
        client_id=client.id,
        client_name=client.full_name,
        visits=visits_data,
        total_visits=total,
        total_spending=total_spending,
        last_visit_date=last_visit_date,
    )

