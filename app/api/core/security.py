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
    """Обрезает пароль до 72 байт, корректно обрабатывая UTF-8."""
    if len(password_bytes) <= 72:
        return password_bytes
    
    # Обрезаем до 72 байт
    truncated = password_bytes[:72]
    
    # Если обрезали в середине UTF-8 символа, убираем последний неполный байт
    # Байты продолжения UTF-8 имеют биты 10xxxxxx, т.е. & 0xC0 == 0x80
    while len(truncated) > 0 and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]
    
    return truncated


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify password against hash.
    Uses bcrypt directly for bcrypt hashes to avoid passlib issues.
    """
    # Если хеш начинается с $2 (bcrypt формат), используем bcrypt напрямую
    if hashed_password.startswith("$2"):
        try:
            password_bytes = plain_password.encode('utf-8')
            # Ограничение bcrypt: пароль не более 72 байт
            password_bytes = _truncate_password_bytes(password_bytes)
            hashed_bytes = hashed_password.encode('utf-8')
            return bcrypt.checkpw(password_bytes, hashed_bytes)
        except Exception:
            # Если что-то пошло не так, пробуем через passlib
            pass
    
    # Для других форматов используем passlib
    try:
        # Также обрезаем для passlib, если нужно
        password_bytes = plain_password.encode('utf-8')
        if len(password_bytes) > 72:
            password_bytes = _truncate_password_bytes(password_bytes)
            plain_password = password_bytes.decode('utf-8', errors='ignore')
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """
    Hash password using bcrypt.
    Bcrypt has a 72-byte limit, so we truncate if necessary.
    """
    # Bcrypt ограничен 72 байтами
    password_bytes = password.encode('utf-8')
    if len(password_bytes) > 72:
        # Обрезаем до 72 байт с корректной обработкой UTF-8
        password_bytes = _truncate_password_bytes(password_bytes)
        password = password_bytes.decode('utf-8', errors='ignore')
    
    # Используем bcrypt напрямую через passlib
    try:
        return pwd_context.hash(password)
    except ValueError as e:
        # Если все еще ошибка из-за длины, используем bcrypt напрямую
        if "72 bytes" in str(e):
            password_bytes = password.encode('utf-8')
            password_bytes = _truncate_password_bytes(password_bytes)
            salt = bcrypt.gensalt()
            return bcrypt.hashpw(password_bytes, salt).decode('utf-8')
        raise


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Validate password strength with enhanced checks.
    Returns (is_valid, error_message)
    Requirements:
    - At least 8 characters, maximum 128 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    - No common passwords
    - No sequential characters (12345, abcde)
    - No repeated characters (aaaa, 1111)
    """
    # Minimum and maximum length
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if len(password) > 128:
        return False, "Password must be no more than 128 characters long"
    
    # Character requirements
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    
    if not re.search(r'\d', password):
        return False, "Password must contain at least one digit"
    
    # Extended special characters
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', password):
        return False, "Password must contain at least one special character (!@#$%^&*()_+-=[]{}|;:,.<>?)"
    
    # Check for common weak passwords
    common_passwords = [
        'password', '12345678', 'qwerty', 'abc123', 'password123',
        'admin123', 'letmein', 'welcome', 'monkey', '1234567890',
        'qwerty123', 'password1', 'admin', 'root', 'toor'
    ]
    if password.lower() in common_passwords:
        return False, "Password is too common. Please choose a more unique password"
    
    # Check for sequential characters (3+ in a row)
    sequences = [
        '0123456789', 'abcdefghijklmnopqrstuvwxyz', 'qwertyuiop', 'asdfghjkl', 'zxcvbnm'
    ]
    password_lower = password.lower()
    for seq in sequences:
        for i in range(len(seq) - 2):
            if seq[i:i+3] in password_lower:
                return False, "Password contains sequential characters. Please avoid patterns like '123' or 'abc'"
    
    # Check for repeated characters (4+ same in a row)
    if re.search(r'(.)\1{3,}', password):
        return False, "Password contains too many repeated characters. Please use more variety"
    
    # Check for keyboard patterns
    keyboard_rows = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']
    for row in keyboard_rows:
        for i in range(len(row) - 2):
            pattern = row[i:i+3]
            if pattern in password_lower or pattern[::-1] in password_lower:
                return False, "Password contains keyboard patterns. Please avoid patterns like 'qwe' or 'asd'"
    
    return True, ""


def validate_email(email: str) -> tuple[bool, str]:
    """
    Enhanced email validation.
    Returns (is_valid, error_message)
    """
    if not email or len(email) == 0:
        return False, "Email cannot be empty"
    
    if len(email) > 254:  # RFC 5321 limit
        return False, "Email address is too long (maximum 254 characters)"
    
    # Basic format check
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, "Invalid email format. Please use format: user@example.com"
    
    # Split email into local and domain parts
    parts = email.split('@')
    if len(parts) != 2:
        return False, "Invalid email format"
    
    local_part, domain = parts
    
    # Validate local part (before @)
    if len(local_part) > 64:  # RFC 5321 limit
        return False, "Email local part is too long (maximum 64 characters)"
    
    if local_part.startswith('.') or local_part.endswith('.'):
        return False, "Email local part cannot start or end with a dot"
    
    if '..' in local_part:
        return False, "Email local part cannot contain consecutive dots"
    
    # Validate domain part
    if len(domain) > 253:  # RFC 5321 limit
        return False, "Email domain is too long (maximum 253 characters)"
    
    if domain.startswith('.') or domain.endswith('.'):
        return False, "Email domain cannot start or end with a dot"
    
    if '..' in domain:
        return False, "Email domain cannot contain consecutive dots"
    
    # Check for valid TLD (at least 2 characters, letters only)
    domain_parts = domain.split('.')
    if len(domain_parts) < 2:
        return False, "Email domain must have at least one dot (e.g., example.com)"
    
    tld = domain_parts[-1]
    if len(tld) < 2:
        return False, "Email domain TLD must be at least 2 characters"
    
    if not re.match(r'^[a-zA-Z]+$', tld):
        return False, "Email domain TLD must contain only letters"
    
    # Check for common disposable email domains (optional, can be extended)
    disposable_domains = [
        'tempmail.com', '10minutemail.com', 'guerrillamail.com',
        'mailinator.com', 'throwaway.email', 'trashmail.com'
    ]
    if domain.lower() in disposable_domains:
        return False, "Disposable email addresses are not allowed"
    
    return True, ""


def validate_phone(phone: str) -> tuple[bool, str]:
    """
    Enhanced phone validation with country code checks.
    Returns (is_valid, error_message)
    """
    if not phone or len(phone) == 0:
        return False, "Phone number cannot be empty"
    
    # Remove spaces, dashes, parentheses, and other formatting
    phone_clean = re.sub(r'[\s\-\(\)\.]', '', phone)
    
    # Must start with +
    if not phone_clean.startswith('+'):
        return False, "Phone number must start with '+' and country code (e.g., +1234567890)"
    
    # Check format: + followed by 1-15 digits (E.164 format)
    pattern = r'^\+[1-9]\d{1,14}$'
    if not re.match(pattern, phone_clean):
        return False, "Invalid phone format. Use international format: +[country code][number] (e.g., +1234567890)"
    
    # Validate length (E.164: max 15 digits after +)
    if len(phone_clean) < 8:  # Minimum: +1 + 6 digits
        return False, "Phone number is too short (minimum 7 digits after country code)"
    
    if len(phone_clean) > 16:  # + plus max 15 digits
        return False, "Phone number is too long (maximum 15 digits after country code)"
    
    # Check for valid country codes (common ones)
    # This is a simplified check - in production, use a proper library
    country_code = phone_clean[1:4] if len(phone_clean) > 4 else phone_clean[1:2]
    
    # Common country codes (1-3 digits)
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
    
    # Check if country code is valid (1-3 digit codes)
    code_found = False
    for code in valid_country_codes:
        if phone_clean.startswith('+' + code):
            code_found = True
            break
    
    if not code_found and len(phone_clean) > 4:
        # Allow unknown country codes but warn
        pass  # We'll allow it but could add warning
    
    # Check for suspicious patterns (all same digits, sequential, etc.)
    digits_only = phone_clean[1:]  # Remove +
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
    """Create a refresh token and return token string and expiration datetime."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(
        to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return encoded_jwt, expire


def verify_refresh_token(token: str) -> Optional[dict]:
    """Verify and decode a refresh token. Returns payload if valid, None otherwise."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        if payload.get("type") != "refresh":
            return None
        return payload
    except (JWTError, ValueError, Exception):
        # Handle all JWT errors, decode errors, and other exceptions
        return None

