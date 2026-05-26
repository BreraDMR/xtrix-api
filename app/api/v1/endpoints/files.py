from fastapi import APIRouter, Depends, HTTPException
from app.api import deps
from app.api.core import s3
from app.db.models.user import User
router = APIRouter()
@router.get("/presign")
def presign_upload(
    filename: str,
    prefix: str = "uploads",
    method: str = "put_object",
    current_user: User = Depends(deps.role_required("admin", "specialist")),
):
    if not s3.settings.aws_bucket:
        raise HTTPException(status_code=400, detail="S3 bucket not configured")
    key = s3.build_object_key(prefix, filename)
    url = s3.generate_presigned_url(key, method=method)
    return {"url": url, "key": key}
