from datetime import datetime, timedelta
from typing import Optional
import re
import jwt
from jose import JWTError
from passlib.context import CryptContext
import bcrypt
from app.api.core.config import settings
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
def _truncate_password_bytes(password_bytes: bytes) -> bytes:
    if len(password_bytes) <= 72:
        return password_bytes
    truncated = password_bytes[:72]
    while len(truncated) > 0 and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]
    return truncated
def verify_password(plain_password: str, hashed_password: str) -> bool:
    if hashed_password.startswith("$2"):
        try:
            password_bytes = plain_password.encode('utf-8')
            password_bytes = _truncate_password_bytes(password_bytes)
            hashed_bytes = hashed_password.encode('utf-8')
            return bcrypt.checkpw(password_bytes, hashed_bytes)
        except Exception:
            pass
    try:
        password_bytes = plain_password.encode('utf-8')
        if len(password_bytes) > 72:
            password_bytes = _truncate_password_bytes(password_bytes)
            plain_password = password_bytes.decode('utf-8', errors='ignore')
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False
def get_password_hash(password: str) -> str:
    password_bytes = password.encode('utf-8')
    if len(password_bytes) > 72:
        password_bytes = _truncate_password_bytes(password_bytes)
        password = password_bytes.decode('utf-8', errors='ignore')
    try:
        return pwd_context.hash(password)
    except ValueError as e:
        if "72 bytes" in str(e):
            password_bytes = password.encode('utf-8')
            password_bytes = _truncate_password_bytes(password_bytes)
            salt = bcrypt.gensalt()
            return bcrypt.hashpw(password_bytes, salt).decode('utf-8')
        raise
def validate_password_strength(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if len(password) > 128:
        return False, "Password must be no more than 128 characters long"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r'\d', password):
        return False, "Password must contain at least one digit"
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', password):
        return False, "Password must contain at least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)"
    common_passwords = [
        'password', '12345678', 'qwerty', 'abc123', 'password123',
        'admin123', 'letmein', 'welcome', 'monkey', '1234567890',
        'qwerty123', 'password1', 'admin', 'root', 'toor'
    ]
    if password.lower() in common_passwords:
        return False, "Password is too common. Please choose a more unique password"
    sequences = [
        '0123456789', 'abcdefghijklmnopqrstuvwxyz', 'qwertyuiop', 'asdfghjkl', 'zxcvbnm'
    ]
    password_lower = password.lower()
    for seq in sequences:
        for i in range(len(seq) - 2):
            if seq[i:i+3] in password_lower:
                return False, "Password contains sequential characters. Please avoid patterns like '123' or 'abc'"
    if re.search(r'(.)\1{3,}', password):
        return False, "Password contains too many repeated characters. Please use more variety"
    keyboard_rows = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']
    for row in keyboard_rows:
        for i in range(len(row) - 2):
            pattern = row[i:i+3]
            if pattern in password_lower or pattern[::-1] in password_lower:
                return False, "Password contains keyboard patterns. Please avoid patterns like 'qwe' or 'asd'"
    return True, ""
def generate_secure_password(length: int = 12) -> str:
    import secrets
    import string
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    special = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    password = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    all_chars = uppercase + lowercase + digits + special
    for _ in range(length - 4):
        password.append(secrets.choice(all_chars))
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)
def generate_secure_password(length: int = 12) -> str:
    import secrets
    import string
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    special = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    password = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    all_chars = uppercase + lowercase + digits + special
    for _ in range(length - 4):
        password.append(secrets.choice(all_chars))
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)
def validate_email(email: str) -> tuple[bool, str]:
    if not email or len(email) == 0:
        return False, "Email cannot be empty"
    if len(email) > 254:  
        return False, "Email address is too long (maximum 254 characters)"
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, "Invalid email format. Please use format: user@example.com"
    parts = email.split('@')
    if len(parts) != 2:
        return False, "Invalid email format"
    local_part, domain = parts
    if len(local_part) > 64:  
        return False, "Email local part is too long (maximum 64 characters)"
    if local_part.startswith('.') or local_part.endswith('.'):
        return False, "Email local part cannot start or end with a dot"
    if '..' in local_part:
        return False, "Email local part cannot contain consecutive dots"
    if len(domain) > 253:  
        return False, "Email domain is too long (maximum 253 characters)"
    if domain.startswith('.') or domain.endswith('.'):
        return False, "Email domain cannot start or end with a dot"
    if '..' in domain:
        return False, "Email domain cannot contain consecutive dots"
    domain_parts = domain.split('.')
    if len(domain_parts) < 2:
        return False, "Email domain must have at least one dot (e.g., example.com)"
    tld = domain_parts[-1]
    if len(tld) < 2:
        return False, "Email domain TLD must be at least 2 characters"
    if not re.match(r'^[a-zA-Z]+$', tld):
        return False, "Email domain TLD must contain only letters"
    disposable_domains = [
        'tempmail.com', '10minutemail.com', 'guerrillamail.com',
        'mailinator.com', 'throwaway.email', 'trashmail.com'
    ]
    if domain.lower() in disposable_domains:
        return False, "Disposable email addresses are not allowed"
    return True, ""
def validate_phone(phone: str) -> tuple[bool, str]:
    if not phone or len(phone) == 0:
        return False, "Phone number cannot be empty"
    phone_clean = re.sub(r'[\s\-\(\)\.]', '', phone)
    if not phone_clean.startswith('+'):
        return False, "Phone number must start with '+' and country code (e.g., +1234567890)"
    pattern = r'^\+[1-9]\d{1,14}$'
    if not re.match(pattern, phone_clean):
        return False, "Invalid phone format. Use international format: +[country code][number] (e.g., +1234567890)"
    if len(phone_clean) < 8:  
        return False, "Phone number is too short (minimum 7 digits after country code)"
    if len(phone_clean) > 16:  
        return False, "Phone number is too long (maximum 15 digits after country code)"
    country_code = phone_clean[1:4] if len(phone_clean) > 4 else phone_clean[1:2]
    valid_country_codes = [
        '1', '7', '20', '27', '30', '31', '32', '33', '34', '36', '39',
        '40', '41', '43', '44', '45', '46', '47', '48', '49', '51',
        '52', '53', '54', '55', '56', '57', '58', '60', '61', '62',
        '63', '64', '65', '66', '81', '82', '84', '86', '90', '91',
        '92', '93', '94', '95', '98', '212', '213', '216', '218',
        '220', '221', '222', '223', '224', '225', '226', '227', '228',
        '229', '230', '231', '232', '233', '234', '235', '236', '237',
        '238', '239', '240', '241', '242', '243', '244', '245', '246',
        '248', '249', '250', '251', '252', '253', '254', '255', '256',
        '257', '258', '260', '261', '262', '263', '264', '265', '266',
        '267', '268', '269', '290', '291', '297', '298', '299', '350',
        '351', '352', '353', '354', '355', '356', '357', '358', '359',
        '370', '371', '372', '373', '374', '375', '376', '377', '378',
        '380', '381', '382', '383', '385', '386', '387', '389', '420',
        '421', '423', '500', '501', '502', '503', '504', '505', '506',
        '507', '508', '509', '590', '591', '592', '593', '594', '595',
        '596', '597', '598', '599', '670', '672', '673', '674', '675',
        '676', '677', '678', '679', '680', '681', '682', '683', '684',
        '685', '686', '687', '688', '689', '690', '691', '692', '850',
        '852', '853', '855', '856', '880', '886', '960', '961', '962',
        '963', '964', '965', '966', '967', '968', '970', '971', '972',
        '973', '974', '975', '976', '977', '992', '993', '994', '995',
        '996', '998'
    ]
    code_found = False
    for code in valid_country_codes:
        if phone_clean.startswith('+' + code):
            code_found = True
            break
    if not code_found and len(phone_clean) > 4:
        pass  
    digits_only = phone_clean[1:]  
    if len(set(digits_only)) < 3:
        return False, "Phone number contains too many repeated digits"
    return True, ""
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta
        if expires_delta
        else timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(
        to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return encoded_jwt
def create_refresh_token(data: dict) -> tuple[str, datetime]:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(
        to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return encoded_jwt, expire
def verify_refresh_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        if payload.get("type") != "refresh":
            return None
        return payload
    except (JWTError, ValueError, Exception):
        return None
