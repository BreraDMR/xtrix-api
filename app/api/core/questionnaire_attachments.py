"""Private questionnaire files: server-chosen keys, bounded type and size.

The browser only ever receives a presigned POST for a key this module made. Every
later action uses the attachment id, so a storage key from a request is never trusted.
"""
from typing import Optional
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError

from app.api.core import s3

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_VISIT = 5
UPLOAD_URL_TTL = 900
DOWNLOAD_URL_TTL = 900

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
ALLOWED_CONTENT_TYPES = frozenset(CONTENT_TYPE_EXTENSIONS)


class UploadsUnavailable(Exception):
    """No bucket or credentials, or the storage provider failed us."""


class AttachmentMissing(Exception):
    """The presigned upload never produced an object."""


def sanitize_filename(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(char for char in base if ord(char) >= 32 and ord(char) != 127)
    return (cleaned.strip(". ") or "file")[:255]


def build_storage_key(business_id: int, visit_id: int, content_type: str) -> str:
    extension = CONTENT_TYPE_EXTENSIONS[content_type]
    return f"questionnaires/{business_id}/{visit_id}/{uuid4().hex}{extension}"


def presign_upload(key: str, content_type: str) -> dict:
    if not s3.settings.aws_bucket:
        raise UploadsUnavailable
    try:
        return s3.generate_presigned_post(
            key, content_type, MAX_ATTACHMENT_BYTES, expires_in=UPLOAD_URL_TTL
        )
    except (BotoCoreError, ClientError):
        raise UploadsUnavailable


def head_uploaded(key: str) -> tuple[Optional[str], int]:
    if not s3.settings.aws_bucket:
        raise UploadsUnavailable
    try:
        head = s3.s3.head_object(Bucket=s3.settings.aws_bucket, Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            raise AttachmentMissing
        raise UploadsUnavailable
    except BotoCoreError:
        raise UploadsUnavailable
    return head.get("ContentType"), int(head.get("ContentLength") or 0)


def presign_download(key: str) -> str:
    if not s3.settings.aws_bucket:
        raise UploadsUnavailable
    try:
        return s3.generate_presigned_url(key, "get_object", expires_in=DOWNLOAD_URL_TTL)
    except (BotoCoreError, ClientError):
        raise UploadsUnavailable
