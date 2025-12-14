"""TOTP (Time-based One-Time Password) utilities for 2FA."""
import pyotp
import qrcode
from io import BytesIO
import base64
from app.api.core.config import settings


def generate_totp_secret() -> str:
    """Generate a new TOTP secret."""
    return pyotp.random_base32()


def generate_totp_qr_code(email: str, secret: str) -> str:
    """
    Generate QR code as base64 string for TOTP.
    Format: otpauth://totp/AppName:email?secret=SECRET&issuer=AppName
    """
    app_name = "Xtrix"
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=email,
        issuer_name=app_name
    )
    
    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(totp_uri)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to base64
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_str = base64.b64encode(buffer.getvalue()).decode()
    
    return f"data:image/png;base64,{img_str}"


def verify_totp_token(secret: str, token: str) -> bool:
    """
    Verify a TOTP token against a secret.
    Allows a small window for clock skew (±1 time step).
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(token, valid_window=1)





