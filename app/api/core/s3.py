import time
from typing import Literal

import boto3

from app.api.core.config import settings


s3 = boto3.client(
    "s3",
    region_name=settings.aws_region,
    aws_access_key_id=settings.aws_access_key,
    aws_secret_access_key=settings.aws_secret_key,
)


def generate_presigned_url(
    key: str, method: Literal["get_object", "put_object"] = "put_object", expires_in=3600
) -> str:
    return s3.generate_presigned_url(
        ClientMethod=method,
        Params={
            "Bucket": settings.aws_bucket,
            "Key": key,
        },
        ExpiresIn=expires_in,
    )


def build_object_key(prefix: str, filename: str) -> str:
    ts = int(time.time())
    return f"{prefix}/{ts}_{filename}"











