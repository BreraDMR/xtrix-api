"""Safe CSV/XLSX import of clients and their visit history.

The service is split along the wizard's steps so every step can be tested on its own:
parsing never writes CRM data, preview only updates the batch checkpoint, and execution
commits one source row at a time. A repeated confirmation resumes from the last
committed row instead of creating a second client or visit.

Only mapped columns are ever copied into the CRM. Unmapped columns (including medical
notes a previous CRM might have exported) stay in the private source blob, which is
dropped after 24 hours or as soon as the batch finishes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.core.clock import utcnow
from app.api.core.logger import get_logger
from app.api.core.permissions import get_staff_record
from app.api.core.security import validate_email, validate_phone
from app.db.models.business import Business
from app.db.models.client import Client
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.client_specialist_relation import ClientSpecialistRelation
from app.db.models.import_batch import ImportBatch, ImportRow
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.user_preferences import UserPreferences
from app.db.models.visit import Visit
from app.db.session import engine as db_engine
from app.schemas.import_batch import MAPPING_TARGETS

logger = get_logger("import")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5_000
MAX_COLUMNS = 50
MAX_CELL_CHARS = 2_000
SOURCE_RETENTION_HOURS = 24
MAX_PRICE = 100_000_000
DEFAULT_DURATION_MINUTES = 60
VISIT_STATUSES = ("planned", "completed", "cancelled", "in_progress")
PAYMENT_METHODS = ("card", "cash", "other")
GENDERS = ("male", "female", "other")
LANGUAGES = ("en", "ru", "cz")
SAMPLE_COLUMNS = [
    "Name",
    "Phone",
    "Email",
    "Birth date",
    "Gender",
    "Language",
    "Visit date",
    "Service",
    "Specialist",
    "Price",
    "Visit status",
    "Payment method",
]

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "full_name": ("name", "full name", "client", "client name", "имя", "фио", "jméno", "cele jmeno"),
    "phone": ("phone", "phone number", "telephone", "tel", "тел", "телефон", "telefon"),
    "email": ("email", "e mail", "mail", "почта", "imejl", "e mailova adresa"),
    "birth_date": (
        "birth date",
        "birthday",
        "date of birth",
        "dob",
        "дата рождения",
        "datum narozeni",
    ),
    "gender": ("gender", "sex", "пол", "pohlavi"),
    "language": ("language", "locale", "язык", "jazyk"),
    "visit_date": (
        "visit date",
        "date",
        "visit datetime",
        "date start",
        "дата визита",
        "дата",
        "datum navstevy",
    ),
    "visit_service": ("service", "service name", "услуга", "sluzba"),
    "visit_specialist": ("specialist", "master", "employee", "специалист", "мастер", "specialista"),
    "visit_price": ("price", "amount", "total", "стоимость", "цена", "cena"),
    "visit_status": ("visit status", "status", "статус", "stav"),
    "visit_payment_method": (
        "payment method",
        "payment",
        "оплата",
        "способ оплаты",
        "platba",
    ),
}
_HEADER_SPACES = re.compile(r"\s+")
_NON_DIGITS = re.compile(r"\D")


class ImportFileError(Exception):
    """A whole-file problem; the endpoint turns the code into a localized 4xx."""

    def __init__(self, code: str, status_code: int = 422):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class ImportRowError(Exception):
    """A single source row problem, stored on the row and never echoed with cell values."""

    def __init__(self, code: str, detail: Optional[str] = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ImportScope:
    business_id: Optional[int] = None
    owner_user_id: Optional[int] = None
    timezone: str = "UTC"
    currency: str = "EUR"


@dataclass
class ParsedRow:
    row_number: int
    values: list[Optional[str]]
    error_code: Optional[str] = None


@dataclass
class Lookups:
    client_ids_by_phone: dict[str, set[int]] = field(default_factory=dict)
    client_ids_by_email: dict[str, set[int]] = field(default_factory=dict)
    service_ids_by_name: dict[str, list[int]] = field(default_factory=dict)
    specialists: list[User] = field(default_factory=list)
    specialist_ids_by_email: dict[str, list[int]] = field(default_factory=dict)
    specialist_ids_by_name: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class PreviewCounts:
    valid: int = 0
    errors: int = 0
    create: int = 0
    update: int = 0
    skip: int = 0


def safe_filename(value: Optional[str], fallback: str = "import") -> str:
    name = str(value or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    name = os.path.basename(name)[:255]
    return name or fallback


def _normalize_header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _HEADER_SPACES.sub(" ", text.strip()).casefold()


def _normalize_name(value: str) -> str:
    return _HEADER_SPACES.sub(" ", value.strip()).casefold()


def _column_signature(headers: Iterable[str]) -> str:
    joined = "\x1f".join(_normalize_header(header) for header in headers)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _row_key(row_number: int, values: list[Optional[str]]) -> str:
    payload = json.dumps([row_number, values], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def suggest_mapping(
    headers: list[str], previous: Optional[dict[str, str]] = None
) -> dict[str, str]:
    """Aliases give a first guess; a saved profile for the same header signature wins."""
    by_key = {_normalize_header(header): header for header in headers}
    result: dict[str, str] = {}
    if isinstance(previous, dict):
        for target, header in previous.items():
            if target in MAPPING_TARGETS and header in headers:
                result[target] = header
    for target in MAPPING_TARGETS:
        if target in result:
            continue
        for alias in _FIELD_ALIASES[target]:
            header = by_key.get(_normalize_header(alias))
            if header is not None:
                result[target] = header
                break
    return result


def validate_mapping(mapping: dict[str, str], headers: list[str]) -> dict[str, str]:
    if not isinstance(mapping, dict) or not mapping:
        raise ImportFileError("import_mapping_required")
    by_key: dict[str, list[str]] = {}
    for header in headers:
        by_key.setdefault(_normalize_header(header), []).append(header)
    result: dict[str, str] = {}
    used: set[str] = set()
    for target, header in mapping.items():
        if target not in MAPPING_TARGETS:
            raise ImportFileError("import_mapping_unknown_field")
        if not isinstance(header, str) or not header.strip():
            raise ImportFileError("import_mapping_unknown_column")
        exact = header if header in headers else None
        if exact is None:
            candidates = by_key.get(_normalize_header(header), [])
            if len(candidates) != 1:
                raise ImportFileError("import_mapping_unknown_column")
            exact = candidates[0]
        if exact in used:
            raise ImportFileError("import_mapping_duplicate_column")
        used.add(exact)
        result[target] = exact
    if "full_name" not in result:
        raise ImportFileError("import_mapping_required_field")
    return result


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ") if value.time() != time.min else value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def _prepare_headers(raw_headers: list[Any]) -> list[str]:
    while raw_headers and _text(raw_headers[-1]) is None:
        raw_headers.pop()
    if not raw_headers:
        raise ImportFileError("import_file_empty")
    if len(raw_headers) > MAX_COLUMNS:
        raise ImportFileError("import_too_many_columns")
    headers: list[str] = []
    seen: set[str] = set()
    for value in raw_headers:
        text = _text(value)
        if text is None:
            raise ImportFileError("import_empty_header")
        text = _HEADER_SPACES.sub(" ", text)[:255]
        key = _normalize_header(text)
        if key in seen:
            raise ImportFileError("import_duplicate_headers")
        seen.add(key)
        headers.append(text)
    return headers


def _prepare_rows(
    raw_rows: list[tuple[int, list[Any]]],
    column_count: int,
    forced_errors: Optional[dict[int, str]] = None,
) -> list[ParsedRow]:
    forced_errors = forced_errors or {}
    result: list[ParsedRow] = []
    for row_number, raw_values in raw_rows:
        values: list[Any] = list(raw_values)
        error_code = None
        if len(values) > column_count:
            extra = [_text(value) for value in values[column_count:]]
            if any(value is not None for value in extra):
                error_code = "extra_columns"
            values = values[:column_count]
        if len(values) < column_count:
            values.extend([None] * (column_count - len(values)))
        cleaned: list[Optional[str]] = []
        for value in values:
            text = _text(value)
            if text is not None and len(text) > MAX_CELL_CHARS:
                error_code = error_code or "cell_too_long"
            cleaned.append(text)
        forced = forced_errors.get(row_number)
        if not any(value is not None for value in cleaned):
            if forced is None:
                continue
            error_code = error_code or forced
        elif forced is not None:
            error_code = error_code or forced
        result.append(ParsedRow(row_number=row_number, values=cleaned, error_code=error_code))
        if len(result) > MAX_ROWS:
            raise ImportFileError("import_too_many_rows")
    if not result:
        raise ImportFileError("import_file_empty")
    return result


def _detect_delimiter(sample: str) -> str:
    line = next((line for line in sample.splitlines() if line.strip()), "")
    counts = {delimiter: line.count(delimiter) for delimiter in (";", ",", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _parse_csv(content: bytes) -> tuple[list[str], list[ParsedRow]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFileError("import_encoding") from exc
    delimiter = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    raw_headers: list[Any] = []
    raw_rows: list[tuple[int, list[Any]]] = []
    logical_row = 0
    for record in reader:
        if not any(str(cell).strip() for cell in record):
            continue
        logical_row += 1
        if logical_row == 1:
            raw_headers = list(record)
        else:
            raw_rows.append((logical_row, list(record)))
            if len(raw_rows) > MAX_ROWS:
                raise ImportFileError("import_too_many_rows")
    headers = _prepare_headers(raw_headers)
    return headers, _prepare_rows(raw_rows, len(headers))


def _parse_xlsx(content: bytes) -> tuple[list[str], list[ParsedRow]]:
    if b"vbaProject.bin" in content:
        raise ImportFileError("import_macros_not_allowed")
    try:
        workbook = load_workbook(
            io.BytesIO(content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except (InvalidFileException, OSError, KeyError, ValueError, TypeError, zipfile.BadZipFile) as exc:
        raise ImportFileError("import_malformed_file") from exc
    try:
        sheet = workbook.active
        raw_headers: list[Any] = []
        raw_rows: list[tuple[int, list[Any]]] = []
        formula_rows: set[int] = set()
        header_seen = False
        for cells in sheet.iter_rows():
            values = [cell.value for cell in cells]
            if not any(_text(value) is not None for value in values):
                continue
            has_formula = any(getattr(cell, "data_type", None) == "f" for cell in cells)
            if not header_seen:
                if has_formula:
                    raise ImportFileError("import_formula_not_allowed")
                raw_headers = values
                header_seen = True
                continue
            row_number = cells[0].row if cells else len(raw_rows) + 2
            if has_formula:
                # Never keep formula text as importable data; the row is reported instead.
                formula_rows.add(row_number)
                values = [None if getattr(cell, "data_type", None) == "f" else cell.value for cell in cells]
            raw_rows.append((row_number, values))
            if len(raw_rows) > MAX_ROWS:
                raise ImportFileError("import_too_many_rows")
        if not header_seen:
            raise ImportFileError("import_file_empty")
        headers = _prepare_headers(raw_headers)
        forced = {row_number: "formula_not_allowed" for row_number in formula_rows}
        return headers, _prepare_rows(raw_rows, len(headers), forced)
    finally:
        workbook.close()


def parse_file(content: bytes, filename: Optional[str]) -> tuple[list[str], list[ParsedRow], str]:
    if not content:
        raise ImportFileError("import_file_empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImportFileError("import_file_too_large", status_code=413)
    clean_name = safe_filename(filename)
    extension = os.path.splitext(clean_name)[1].lower()
    if extension == ".xlsm":
        raise ImportFileError("import_macros_not_allowed")
    if extension == ".csv":
        headers, rows = _parse_csv(content)
    elif extension == ".xlsx":
        headers, rows = _parse_xlsx(content)
    else:
        raise ImportFileError("import_file_type")
    return headers, rows, clean_name


def scope_for(db: Session, user: User) -> ImportScope:
    """Owners run their business scope, a solo specialist their own clients; staff cannot import."""
    if user.role == "admin":
        if not user.business_id:
            raise ImportFileError("not_allowed", status_code=403)
        business_id = user.business_id
        owner_user_id = None
    elif (user.role in ("business_owner", "specialist")
          and user.employer_id is None and not get_staff_record(db, user)):
        business_id = user.business_id
        owner_user_id = None if business_id else user.id
    else:
        raise ImportFileError("not_allowed", status_code=403)
    timezone_name = "UTC"
    currency = "EUR"
    if business_id:
        business = db.query(Business).filter(Business.id == business_id).first()
        if business is None:
            raise ImportFileError("business_required", status_code=403)
        timezone_name = business.timezone or "UTC"
        currency = business.currency or "EUR"
    else:
        preferences = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
        if preferences and preferences.timezone:
            timezone_name = preferences.timezone
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        timezone_name = "UTC"
    return ImportScope(
        business_id=business_id,
        owner_user_id=owner_user_id,
        timezone=timezone_name,
        currency=currency,
    )


def scoped_query(db: Session, scope: ImportScope, model):
    query = db.query(model)
    if scope.business_id is not None:
        return query.filter(model.business_id == scope.business_id)
    return query.filter(model.owner_user_id == scope.owner_user_id)


def find_batch(db: Session, scope: ImportScope, batch_id: int) -> Optional[ImportBatch]:
    return scoped_query(db, scope, ImportBatch).filter(ImportBatch.id == batch_id).first()


def find_batch_by_source(db: Session, scope: ImportScope, source_hash: str) -> Optional[ImportBatch]:
    return (
        scoped_query(db, scope, ImportBatch)
        .filter(ImportBatch.source_hash == source_hash)
        .first()
    )


def previous_mapping(db: Session, scope: ImportScope, column_signature: str) -> Optional[dict[str, str]]:
    previous = (
        scoped_query(db, scope, ImportBatch)
        .filter(
            ImportBatch.column_signature == column_signature,
            ImportBatch.mapping.isnot(None),
        )
        .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
        .first()
    )
    return previous.mapping if previous and isinstance(previous.mapping, dict) else None


def create_batch(
    db: Session,
    scope: ImportScope,
    user: User,
    *,
    content: bytes,
    filename: Optional[str],
    content_type: Optional[str],
) -> ImportBatch:
    headers, parsed_rows, clean_name = parse_file(content, filename)
    source_hash = hashlib.sha256(content).hexdigest()
    existing = find_batch_by_source(db, scope, source_hash)
    if existing is not None:
        raise ImportSourceExists(existing)
    signature = _column_signature(headers)
    now = utcnow()
    batch = ImportBatch(
        business_id=scope.business_id,
        owner_user_id=scope.owner_user_id,
        source_hash=source_hash,
        source_filename=clean_name,
        content_type=(content_type or "")[:64] or None,
        source_blob=content,
        source_expires_at=now + timedelta(hours=SOURCE_RETENTION_HOURS),
        headers=headers,
        column_signature=signature,
        mapping=None,
        mapping_version=0,
        duplicate_policy="skip",
        status="uploaded",
        preview_version=0,
        row_count=len(parsed_rows),
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(batch)
    db.flush()
    for parsed in parsed_rows:
        raw = {"values": parsed.values, "error": parsed.error_code}
        db.add(
            ImportRow(
                batch_id=batch.id,
                row_number=parsed.row_number,
                row_key=_row_key(parsed.row_number, parsed.values),
                raw=raw,
                status="pending",
                error_code=parsed.error_code,
            )
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        # The unique index is the final guard when two uploads race on the same file.
        existing = find_batch_by_source(db, scope, source_hash)
        if existing is not None:
            raise ImportSourceExists(existing)
        raise
    db.refresh(batch)
    return batch


class ImportSourceExists(Exception):
    def __init__(self, batch: ImportBatch):
        super().__init__("import_source_exists")
        self.batch = batch


def _previous_for_batch(db: Session, scope: ImportScope, batch: ImportBatch) -> Optional[dict[str, str]]:
    previous = (
        scoped_query(db, scope, ImportBatch)
        .filter(
            ImportBatch.column_signature == batch.column_signature,
            ImportBatch.id != batch.id,
            ImportBatch.mapping.isnot(None),
        )
        .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
        .first()
    )
    return previous.mapping if previous and isinstance(previous.mapping, dict) else None


def suggested_mapping(db: Session, scope: ImportScope, batch: ImportBatch) -> dict[str, str]:
    return suggest_mapping(batch.headers or [], _previous_for_batch(db, scope, batch))


def expire_source(db: Session, batch: ImportBatch) -> None:
    if batch.source_blob is None:
        return
    if batch.source_expires_at is not None and batch.source_expires_at > utcnow():
        return
    batch.source_blob = None
    batch.source_expires_at = None
    db.commit()


def save_mapping(db: Session, batch: ImportBatch, mapping: dict[str, str]) -> ImportBatch:
    if batch.status not in ("uploaded", "mapped", "previewed"):
        raise ImportFileError("import_batch_locked", status_code=409)
    cleaned = validate_mapping(mapping, batch.headers or [])
    batch.mapping = cleaned
    batch.mapping_version = (batch.mapping_version or 0) + 1
    batch.preview_version = 0
    batch.status = "mapped"
    batch.previewed_at = None
    for row in batch.rows:
        row.client_data = None
        row.visit_data = None
        row.action = None
        row.status = "pending"
        row.error_code = None
        row.error_detail = None
    db.commit()
    db.refresh(batch)
    return batch


def _parse_birth_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    value = text.strip()
    parsed = None
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(value, pattern).date()
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ImportRowError("invalid_date", "birth_date") from exc
    if not date(1900, 1, 1) <= parsed <= date(2100, 12, 31):
        raise ImportRowError("invalid_date", "birth_date")
    return parsed


def _parse_visit_datetime(text: Optional[str], timezone_name: str) -> datetime:
    if not text:
        raise ImportRowError("required_field", "visit_date")
    value = text.strip().replace("Z", "+00:00")
    parsed: Optional[datetime] = None
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        try:
            parsed = datetime.fromisoformat(value.replace(" ", "T", 1))
        except ValueError:
            parsed = None
    if parsed is None:
        for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(value, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ImportRowError("invalid_date", "visit_date")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt_timezone.utc).replace(tzinfo=None)
    else:
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            zone = ZoneInfo("UTC")
        parsed = parsed.replace(tzinfo=zone).astimezone(dt_timezone.utc).replace(tzinfo=None)
    if not datetime(1900, 1, 1) <= parsed <= datetime(2100, 12, 31):
        raise ImportRowError("invalid_date", "visit_date")
    return parsed


def _parse_price(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    value = (
        text.replace("\u00a0", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        raise ImportRowError("invalid_price", "visit_price")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ImportRowError("invalid_price", "visit_price") from exc
    if amount < 0 or amount > MAX_PRICE:
        raise ImportRowError("invalid_price", "visit_price")
    return float(amount)


def _parse_choice(text: Optional[str], allowed: tuple[str, ...], field_name: str) -> Optional[str]:
    if not text:
        return None
    value = text.strip().lower()
    if value not in allowed:
        raise ImportRowError("invalid_choice", field_name)
    return value


def _row_value(values: list[Optional[str]], mapping: dict[str, str], headers: list[str], target: str) -> Optional[str]:
    header = mapping.get(target)
    if header is None:
        return None
    try:
        index = headers.index(header)
    except ValueError:
        return None
    if index >= len(values):
        return None
    return values[index]


def parse_client_values(values: list[Optional[str]], mapping: dict[str, str], headers: list[str]) -> dict[str, Any]:
    full_name = (_row_value(values, mapping, headers, "full_name") or "").strip()
    if not full_name:
        raise ImportRowError("required_field", "full_name")
    if len(full_name) > 255:
        raise ImportRowError("too_long", "full_name")
    data: dict[str, Any] = {"full_name": full_name}

    phone = _row_value(values, mapping, headers, "phone")
    if phone:
        valid, _error = validate_phone(phone)
        if not valid:
            raise ImportRowError("invalid_phone", "phone")
        data["phone"] = phone

    email = _row_value(values, mapping, headers, "email")
    if email:
        valid, _error = validate_email(email)
        if not valid:
            raise ImportRowError("invalid_email", "email")
        data["email"] = email.strip().lower()

    birth_date = _parse_birth_date(_row_value(values, mapping, headers, "birth_date"))
    if birth_date is not None:
        data["birth_date"] = birth_date.isoformat()

    gender = _parse_choice(_row_value(values, mapping, headers, "gender"), GENDERS, "gender")
    if gender:
        data["gender"] = gender
    language = _parse_choice(_row_value(values, mapping, headers, "language"), LANGUAGES, "language")
    if language:
        data["language"] = language
    return data


def normalize_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = _NON_DIGITS.sub("", value)
    return digits or None


def normalize_email(value: Optional[str]) -> Optional[str]:
    return value.strip().lower() if value else None


def load_lookups(db: Session, scope: ImportScope, user: User) -> Lookups:
    lookups = Lookups()
    if scope.business_id is not None:
        clients = (
            db.query(Client)
            .join(ClientBusinessRelation, ClientBusinessRelation.client_id == Client.id)
            .filter(
                ClientBusinessRelation.business_id == scope.business_id,
                Client.deleted_at.is_(None),
            )
            .distinct()
            .all()
        )
        services = (
            db.query(Service)
            .filter(Service.business_id == scope.business_id, Service.deleted_at.is_(None))
            .all()
        )
        lookups.specialists = (
            db.query(User)
            .filter(User.business_id == scope.business_id, User.deleted_at.is_(None))
            .all()
        )
    else:
        clients = (
            db.query(Client)
            .filter(Client.owner_id == scope.owner_user_id, Client.deleted_at.is_(None))
            .all()
        )
        services = (
            db.query(Service)
            .filter(Service.specialist_id == scope.owner_user_id, Service.deleted_at.is_(None))
            .all()
        )
        owner = db.query(User).filter(User.id == scope.owner_user_id).first()
        lookups.specialists = [owner] if owner else [user]

    for client in clients:
        phone = normalize_phone(client.contact_phone)
        if phone:
            lookups.client_ids_by_phone.setdefault(phone, set()).add(client.id)
        email = normalize_email(client.contact_email)
        if email:
            lookups.client_ids_by_email.setdefault(email, set()).add(client.id)
    for service in services:
        name = _normalize_name(service.name or "")
        if name:
            lookups.service_ids_by_name.setdefault(name, []).append(service.id)
    for specialist in lookups.specialists:
        email = normalize_email(specialist.email)
        if email:
            lookups.specialist_ids_by_email.setdefault(email, []).append(specialist.id)
        name = _normalize_name(specialist.full_name or "")
        if name:
            lookups.specialist_ids_by_name.setdefault(name, []).append(specialist.id)
    return lookups


def match_client(
    lookups: Lookups, phone: Optional[str], email: Optional[str]
) -> Optional[int]:
    matched: set[int] = set()
    phone_key = normalize_phone(phone)
    if phone_key:
        matched.update(lookups.client_ids_by_phone.get(phone_key, set()))
    email_key = normalize_email(email)
    if email_key:
        matched.update(lookups.client_ids_by_email.get(email_key, set()))
    if len(matched) > 1:
        raise ImportRowError("ambiguous_match", "phone/email")
    return next(iter(matched)) if matched else None


def ensure_no_contact_conflict(
    lookups: Lookups,
    phone: Optional[str],
    email: Optional[str],
    exclude_client_id: int,
) -> None:
    phone_key = normalize_phone(phone)
    if phone_key:
        others = lookups.client_ids_by_phone.get(phone_key, set()) - {exclude_client_id}
        if others:
            raise ImportRowError("contact_conflict", "phone")
    email_key = normalize_email(email)
    if email_key:
        others = lookups.client_ids_by_email.get(email_key, set()) - {exclude_client_id}
        if others:
            raise ImportRowError("contact_conflict", "email")


def decide_action(match_id: Optional[int], policy: str) -> str:
    if match_id is None:
        return "create"
    if policy == "skip":
        return "skip"
    if policy == "update":
        return "update"
    return "create"


def resolve_specialist(lookups: Lookups, value: Optional[str], default_id: int) -> tuple[int, str]:
    if not value:
        if not lookups.specialists:
            raise ImportRowError("unknown_specialist", "visit_specialist")
        chosen = next((item for item in lookups.specialists if item.id == default_id), None)
        if chosen is None:
            chosen = lookups.specialists[0]
        return chosen.id, chosen.full_name or ""
    text = value.strip()
    ids = lookups.specialist_ids_by_email.get(normalize_email(text) or "", [])
    if not ids:
        ids = lookups.specialist_ids_by_name.get(_normalize_name(text), [])
    if not ids:
        raise ImportRowError("unknown_specialist", "visit_specialist")
    if len(set(ids)) > 1:
        raise ImportRowError("ambiguous_specialist", "visit_specialist")
    chosen_id = ids[0]
    chosen = next((item for item in lookups.specialists if item.id == chosen_id), None)
    return chosen_id, (chosen.full_name if chosen else "") or ""


def resolve_service(lookups: Lookups, name: Optional[str]) -> tuple[Optional[int], bool]:
    if not name:
        return None, False
    ids = lookups.service_ids_by_name.get(_normalize_name(name), [])
    if len(set(ids)) > 1:
        raise ImportRowError("ambiguous_service", "visit_service")
    if ids:
        return ids[0], False
    return None, True


def parse_visit_values(
    values: list[Optional[str]],
    mapping: dict[str, str],
    headers: list[str],
    lookups: Lookups,
    scope: ImportScope,
    default_specialist_id: int,
    db: Session,
) -> Optional[dict[str, Any]]:
    has_any = any(
        _row_value(values, mapping, headers, target)
        for target in ("visit_date", "visit_service", "visit_specialist", "visit_price", "visit_status", "visit_payment_method")
    )
    if not has_any:
        return None
    start = _parse_visit_datetime(_row_value(values, mapping, headers, "visit_date"), scope.timezone)
    specialist_id, specialist_name = resolve_specialist(
        lookups, _row_value(values, mapping, headers, "visit_specialist"), default_specialist_id
    )
    service_name = _row_value(values, mapping, headers, "visit_service")
    if service_name and len(service_name) > 255:
        raise ImportRowError("too_long", "visit_service")
    service_id, creates_service = resolve_service(lookups, service_name)
    price = _parse_price(_row_value(values, mapping, headers, "visit_price"))
    service = db.get(Service, service_id) if service_id else None
    if price is None and service is not None:
        price = service.price
    status = _parse_choice(
        _row_value(values, mapping, headers, "visit_status"), VISIT_STATUSES, "visit_status"
    ) or "completed"
    payment_method = _parse_choice(
        _row_value(values, mapping, headers, "visit_payment_method"),
        PAYMENT_METHODS,
        "visit_payment_method",
    )
    duration = (service.duration_minutes if service and service.duration_minutes else None) or DEFAULT_DURATION_MINUTES
    return {
        "date_start": start.isoformat(),
        "service_id": service_id,
        "service_name": (service.name if service is not None else service_name),
        "specialist_id": specialist_id,
        "specialist_name": specialist_name,
        "price": price,
        "status": status,
        "payment_method": payment_method,
        "creates_service": creates_service,
        "duration_minutes": duration,
    }


def build_preview(
    db: Session,
    batch: ImportBatch,
    scope: ImportScope,
    user: User,
    policy: str,
) -> ImportBatch:
    if batch.status not in ("uploaded", "mapped", "previewed"):
        raise ImportFileError("import_batch_locked", status_code=409)
    mapping = validate_mapping(batch.mapping or {}, batch.headers or [])
    lookups = load_lookups(db, scope, user)
    counts = PreviewCounts()
    headers = batch.headers or []
    for row in batch.rows:
        row.client_data = None
        row.visit_data = None
        row.action = None
        row.error_code = None
        row.error_detail = None
        raw_error = row.raw.get("error") if isinstance(row.raw, dict) else None
        values = row.raw.get("values") if isinstance(row.raw, dict) else row.raw
        if raw_error:
            row.status = "error"
            row.error_code = raw_error
            counts.errors += 1
            continue
        try:
            if not isinstance(values, list):
                raise ImportRowError("malformed_row")
            client_data = parse_client_values(values, mapping, headers)
            match_id = match_client(lookups, client_data.get("phone"), client_data.get("email"))
            action = decide_action(match_id, policy)
            if action == "update" and match_id is not None:
                ensure_no_contact_conflict(
                    lookups,
                    client_data.get("phone"),
                    client_data.get("email"),
                    match_id,
                )
            visit_data = parse_visit_values(
                values,
                mapping,
                headers,
                lookups,
                scope,
                user.id,
                db,
            )
        except ImportRowError as exc:
            row.status = "error"
            row.error_code = exc.code
            row.error_detail = exc.detail
            counts.errors += 1
            continue
        row.client_data = client_data
        row.visit_data = visit_data
        row.action = action
        row.status = "valid"
        counts.valid += 1
        if action == "create":
            counts.create += 1
        elif action == "update":
            counts.update += 1
        else:
            counts.skip += 1
    batch.duplicate_policy = policy
    batch.preview_version = (batch.preview_version or 0) + 1
    batch.previewed_at = utcnow()
    batch.status = "previewed"
    batch.valid_count = counts.valid
    batch.error_count = counts.errors
    batch.create_count = counts.create
    batch.update_count = counts.update
    batch.skip_count = counts.skip
    batch.updated_at = utcnow()
    db.commit()
    db.refresh(batch)
    return batch


def _ensure_relations(db: Session, scope: ImportScope, user: User, client_id: int) -> None:
    if scope.business_id is None:
        return
    business_relation = (
        db.query(ClientBusinessRelation)
        .filter(
            ClientBusinessRelation.client_id == client_id,
            ClientBusinessRelation.business_id == scope.business_id,
        )
        .first()
    )
    if business_relation is None:
        db.add(ClientBusinessRelation(client_id=client_id, business_id=scope.business_id))
        db.flush()
    specialist_relation = (
        db.query(ClientSpecialistRelation)
        .filter(
            ClientSpecialistRelation.client_id == client_id,
            ClientSpecialistRelation.specialist_id == user.id,
            ClientSpecialistRelation.business_id == scope.business_id,
        )
        .first()
    )
    if specialist_relation is None:
        db.add(
            ClientSpecialistRelation(
                client_id=client_id,
                specialist_id=user.id,
                business_id=scope.business_id,
            )
        )
        db.flush()


def _apply_client_values(client: Client, data: dict[str, Any]) -> None:
    if data.get("full_name"):
        client.full_name = data["full_name"]
    if data.get("phone"):
        client.contact_phone = data["phone"]
    if data.get("email"):
        client.contact_email = data["email"]
    if data.get("birth_date"):
        client.birth_date = date.fromisoformat(data["birth_date"])
    if data.get("gender"):
        client.gender = data["gender"]
    if data.get("language"):
        client.language = data["language"]


def _create_client(
    db: Session,
    scope: ImportScope,
    user: User,
    data: dict[str, Any],
) -> Client:
    client = Client(
        owner_id=user.id if scope.business_id is None else None,
        full_name=data["full_name"],
        birth_date=date.fromisoformat(data["birth_date"]) if data.get("birth_date") else None,
        gender=data.get("gender"),
        contact_phone=data.get("phone"),
        contact_email=data.get("email"),
        language=data.get("language") or "ru",
    )
    db.add(client)
    db.flush()
    _ensure_relations(db, scope, user, client.id)
    return client


def _resolve_service_for_row(
    db: Session,
    lookups: Lookups,
    data: dict[str, Any],
    scope: ImportScope,
    user: User,
) -> tuple[Optional[Service], Optional[tuple[str, int]]]:
    service_id = data.get("service_id")
    if service_id:
        service = db.get(Service, service_id)
        if service is None or service.deleted_at is not None:
            raise ImportRowError("unknown_service", "visit_service")
        if scope.business_id is not None and service.business_id != scope.business_id:
            raise ImportRowError("unknown_service", "visit_service")
        if scope.business_id is None and service.specialist_id != user.id:
            raise ImportRowError("unknown_service", "visit_service")
        return service, None
    name = (data.get("service_name") or "").strip()
    if not name:
        return None, None
    ids = lookups.service_ids_by_name.get(_normalize_name(name), [])
    if len(set(ids)) > 1:
        raise ImportRowError("ambiguous_service", "visit_service")
    if ids:
        service = db.get(Service, ids[0])
        if service is not None:
            return service, None
    if not data.get("creates_service"):
        raise ImportRowError("unknown_service", "visit_service")
    service = Service(
        specialist_id=user.id,
        business_id=scope.business_id,
        name=name[:255],
        duration_minutes=int(data.get("duration_minutes") or DEFAULT_DURATION_MINUTES),
        price=data.get("price"),
        is_active=True,
    )
    db.add(service)
    db.flush()
    return service, (_normalize_name(name), service.id)


@dataclass
class RowOutcome:
    action: str
    client_id: Optional[int]
    old_phone: Optional[str] = None
    old_email: Optional[str] = None
    new_phone: Optional[str] = None
    new_email: Optional[str] = None
    created_service: Optional[tuple[str, int]] = None


def _forget_contact(values: dict[str, set[int]], key: Optional[str], client_id: int) -> None:
    if not key:
        return
    current = values.get(key)
    if current is None:
        return
    current.discard(client_id)
    if not current:
        values.pop(key, None)


def apply_row_outcome(lookups: Lookups, outcome: RowOutcome) -> None:
    if outcome.client_id is None:
        return
    if outcome.action == "update":
        _forget_contact(lookups.client_ids_by_phone, normalize_phone(outcome.old_phone), outcome.client_id)
        _forget_contact(lookups.client_ids_by_email, normalize_email(outcome.old_email), outcome.client_id)
    if outcome.action in ("update", "create"):
        new_phone = normalize_phone(outcome.new_phone)
        if new_phone:
            lookups.client_ids_by_phone.setdefault(new_phone, set()).add(outcome.client_id)
        new_email = normalize_email(outcome.new_email)
        if new_email:
            lookups.client_ids_by_email.setdefault(new_email, set()).add(outcome.client_id)
    if outcome.created_service is not None:
        name, service_id = outcome.created_service
        lookups.service_ids_by_name.setdefault(name, []).append(service_id)


def _execute_row(
    db: Session,
    batch: ImportBatch,
    scope: ImportScope,
    user: User,
    row: ImportRow,
    lookups: Lookups,
) -> RowOutcome:
    client_data = row.client_data or {}
    phone = client_data.get("phone")
    email = client_data.get("email")
    match_id = match_client(lookups, phone, email)
    policy = batch.duplicate_policy
    action = decide_action(match_id, policy)

    if action == "skip" and match_id is not None:
        row.client_id = match_id
        row.action = "skip"
        row.status = "skipped"
        row.processed_at = utcnow()
        return RowOutcome(action="skip", client_id=match_id)

    if action == "update" and match_id is not None:
        ensure_no_contact_conflict(lookups, phone, email, match_id)
        client = db.get(Client, match_id)
        if client is None or client.deleted_at is not None:
            raise ImportRowError("client_missing")
        old_phone = client.contact_phone
        old_email = client.contact_email
        _apply_client_values(client, client_data)
        _ensure_relations(db, scope, user, client.id)
        row.client_id = client.id
        row.action = "update"
        outcome = RowOutcome(
            action="update",
            client_id=client.id,
            old_phone=old_phone,
            old_email=old_email,
            new_phone=client.contact_phone,
            new_email=client.contact_email,
        )
    else:
        client = _create_client(db, scope, user, client_data)
        row.client_id = client.id
        row.action = "create"
        outcome = RowOutcome(
            action="create",
            client_id=client.id,
            new_phone=client.contact_phone,
            new_email=client.contact_email,
        )

    visit_data = row.visit_data
    if visit_data and not row.visit_id:
        specialist_id = int(visit_data.get("specialist_id") or user.id)
        service, created_service = _resolve_service_for_row(db, lookups, visit_data, scope, user)
        start = datetime.fromisoformat(visit_data["date_start"])
        duration = int(visit_data.get("duration_minutes") or DEFAULT_DURATION_MINUTES)
        if service is not None and service.duration_minutes:
            duration = service.duration_minutes
        from app.api.core.crm_metering import enforce_visit_quota
        enforce_visit_quota(db, scope.business_id, specialist_id, start, visit_data.get("status") or "completed")
        visit = Visit(
            client_id=client.id,
            specialist_id=specialist_id,
            service_id=service.id if service is not None else None,
            business_id=scope.business_id,
            date_start=start,
            date_end=start + timedelta(minutes=duration),
            service_name=(service.name if service is not None else visit_data.get("service_name")),
            price=visit_data.get("price"),
            status=visit_data.get("status") or "completed",
            payment_method=visit_data.get("payment_method"),
            created_at=utcnow(),
        )
        db.add(visit)
        db.flush()
        row.visit_id = visit.id
        outcome.created_service = created_service

    row.status = "imported"
    row.processed_at = utcnow()
    return outcome


def _refresh_batch_counts(db: Session, batch: ImportBatch) -> None:
    actions: dict[str, int] = {}
    rows = (
        db.query(ImportRow.status, ImportRow.action, func.count(ImportRow.id))
        .filter(ImportRow.batch_id == batch.id)
        .group_by(ImportRow.status, ImportRow.action)
        .all()
    )
    errors = 0
    for status_value, action_value, amount in rows:
        if status_value in ("imported", "skipped") and action_value:
            actions[action_value] = actions.get(action_value, 0) + amount
        if status_value in ("error", "failed"):
            errors += amount
    batch.created_count = actions.get("create", 0)
    batch.updated_count = actions.get("update", 0)
    batch.skipped_count = actions.get("skip", 0)
    batch.error_count = errors
    batch.valid_count = max(0, batch.row_count - errors)
    batch.updated_at = utcnow()


def execute_batch(
    db: Session,
    batch: ImportBatch,
    scope: ImportScope,
    user: User,
) -> bool:
    """Run a previewed batch. Returns True when this call actually executed rows."""
    if batch.status in ("completed", "completed_with_errors"):
        return False
    if batch.status not in ("previewed", "importing", "failed"):
        raise ImportFileError("import_preview_required", status_code=409)
    if not batch.preview_version:
        raise ImportFileError("import_preview_required", status_code=409)

    # A session advisory lock survives the per-row commits and is released by PostgreSQL
    # if the worker dies, so a double click cannot run the same rows twice while a retry
    # after a crash still resumes from the checkpoint. It lives on its own connection:
    # the batch session releases and re-checks out connections on every per-row commit.
    lock_key = 7_400_000 + int(batch.id)
    lock_connection = db_engine.connect()
    try:
        acquired = lock_connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}
        ).scalar()
        if not acquired:
            raise ImportFileError("import_in_progress", status_code=409)
        try:
            return _execute_batch_locked(db, batch, scope, user)
        finally:
            try:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key}
                )
                lock_connection.commit()
            except Exception:
                lock_connection.rollback()
    finally:
        lock_connection.close()


def _execute_batch_locked(
    db: Session,
    batch: ImportBatch,
    scope: ImportScope,
    user: User,
) -> bool:
    batch.status = "importing"
    batch.confirmed_at = batch.confirmed_at or utcnow()
    if (
        batch.source_blob is not None
        and batch.source_expires_at is not None
        and batch.source_expires_at <= utcnow()
    ):
        batch.source_blob = None
        batch.source_expires_at = None
    batch.updated_at = utcnow()
    db.commit()

    lookups = load_lookups(db, scope, user)
    pending = (
        db.query(ImportRow)
        .filter(ImportRow.batch_id == batch.id, ImportRow.status == "valid")
        .order_by(ImportRow.row_number)
        .all()
    )
    for queued in pending:
        row_id = queued.id
        try:
            row = db.get(ImportRow, row_id)
            if row is None or row.status != "valid":
                continue
            outcome = _execute_row(db, batch, scope, user, row, lookups)
            batch.last_checkpoint = row.row_number
            _refresh_batch_counts(db, batch)
            db.commit()
            apply_row_outcome(lookups, outcome)
        except ImportRowError as exc:
            db.rollback()
            row = db.get(ImportRow, row_id)
            batch = db.get(ImportBatch, batch.id)
            if row is not None:
                row.status = "failed"
                row.error_code = exc.code
                row.error_detail = exc.detail
                row.processed_at = utcnow()
                batch.last_checkpoint = row.row_number
            _refresh_batch_counts(db, batch)
            db.commit()
            lookups = load_lookups(db, scope, user)
        except Exception:
            db.rollback()
            logger.exception("import: row %s of batch %s failed", row_id, batch.id)
            row = db.get(ImportRow, row_id)
            batch = db.get(ImportBatch, batch.id)
            if row is not None:
                row.status = "failed"
                row.error_code = "row_failed"
                row.error_detail = None
                row.processed_at = utcnow()
                batch.last_checkpoint = row.row_number
            _refresh_batch_counts(db, batch)
            db.commit()
            lookups = load_lookups(db, scope, user)

    remaining = (
        db.query(func.count(ImportRow.id))
        .filter(ImportRow.batch_id == batch.id, ImportRow.status == "valid")
        .scalar()
        or 0
    )
    _refresh_batch_counts(db, batch)
    if remaining == 0:
        batch.status = "completed_with_errors" if batch.error_count else "completed"
        batch.finished_at = utcnow()
        batch.source_blob = None
        batch.source_expires_at = None
    batch.updated_at = utcnow()
    db.commit()
    db.refresh(batch)
    return True


def batch_payload(db: Session, scope: ImportScope, batch: ImportBatch) -> dict[str, Any]:
    failed = (
        db.query(func.count(ImportRow.id))
        .filter(ImportRow.batch_id == batch.id, ImportRow.status == "failed")
        .scalar()
        or 0
    )
    return {
        "id": batch.id,
        "status": batch.status,
        "source_filename": batch.source_filename,
        "source_hash": batch.source_hash,
        "content_type": batch.content_type,
        "headers": batch.headers or [],
        "column_signature": batch.column_signature,
        "mapping": batch.mapping,
        "suggested_mapping": suggested_mapping(db, scope, batch),
        "mapping_version": batch.mapping_version,
        "preview_version": batch.preview_version,
        "duplicate_policy": batch.duplicate_policy or "skip",
        "row_count": batch.row_count,
        "valid_count": batch.valid_count,
        "error_count": batch.error_count,
        "create_count": batch.create_count,
        "update_count": batch.update_count,
        "skip_count": batch.skip_count,
        "created_count": batch.created_count,
        "updated_count": batch.updated_count,
        "skipped_count": batch.skipped_count,
        "failed_count": failed,
        "last_checkpoint": batch.last_checkpoint,
        "timezone": scope.timezone,
        "currency": scope.currency,
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
        "previewed_at": batch.previewed_at,
        "confirmed_at": batch.confirmed_at,
        "finished_at": batch.finished_at,
        "source_expires_at": batch.source_expires_at,
    }


def row_payload(row: ImportRow) -> dict[str, Any]:
    return {
        "row_number": row.row_number,
        "status": row.status,
        "action": row.action,
        "error_code": row.error_code,
        "error_detail": row.error_detail,
        "client_id": row.client_id,
        "visit_id": row.visit_id,
        "client": row.client_data,
        "visit": row.visit_data,
    }
