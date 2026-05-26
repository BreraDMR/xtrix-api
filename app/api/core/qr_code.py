import secrets
import hashlib
from typing import Optional
from io import BytesIO
import base64
try:
    import qrcode
    from qrcode.image.pil import PilImage
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False
from app.api.core.config import settings
from app.api.core.security import get_password_hash, verify_password
def generate_qr_code_data(client_id: int) -> str:
    random_token = secrets.token_urlsafe(16)
    hash_value = hashlib.sha256(f"{client_id}:{random_token}:{settings.jwt_secret}".encode()).hexdigest()[:16]
    return f"{client_id}:{hash_value}"
def generate_access_password() -> str:
    return str(secrets.randbelow(9000) + 1000)  
def generate_qr_code_image(data: str) -> Optional[bytes]:
    if not QRCODE_AVAILABLE:
        return None
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_bytes = BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes.read()
def generate_qr_code_base64(data: str) -> Optional[str]:
    img_bytes = generate_qr_code_image(data)
    if img_bytes:
        return base64.b64encode(img_bytes).decode('utf-8')
    return None
def parse_qr_code_data(qr_data: str) -> Optional[tuple[int, str]]:
    try:
        parts = qr_data.split(':')
        if len(parts) != 2:
            return None
        client_id = int(parts[0])
        hash_value = parts[1]
        return client_id, hash_value
    except (ValueError, AttributeError):
        return None
def verify_qr_code_hash(client_id: int, hash_value: str) -> bool:
    if len(hash_value) != 16:
        return False
    return True
