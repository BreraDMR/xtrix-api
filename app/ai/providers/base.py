"""What every model adapter speaks: a list of messages in, plain text plus token counts out.

Adapters don't parse or validate the answer - the router does that the same way for all of them.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal, Optional, Protocol

import httpx


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


# rate_limited: try later (seconds); quota: out of today's/credit allowance; server: 5xx, overload;
# timeout/network: no answer; auth: bad or revoked key; bad_request: our request or the model id is wrong;
# bad_response: 200 but nothing usable in it (refusal, empty, not JSON at the HTTP level)
ErrorKind = Literal["rate_limited", "quota", "server", "timeout", "network", "auth", "bad_request", "bad_response"]


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, *, status: Optional[int] = None,
                 retry_after: Optional[float] = None, daily: bool = False, detail: str = ""):
        super().__init__(f"{kind} status={status} {detail}".strip())
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.daily = daily
        self.detail = detail


class ModelAdapter(Protocol):
    def complete(self, messages: list[Message], *, model: str, json_schema: Optional[dict],
                 max_tokens: int, timeout: float) -> Completion: ...


# words providers use when the daily (not per-minute) allowance is gone
# ("quota" alone is no hint: Google says "quota exceeded" for per-minute limits too)
_DAILY = re.compile(r"per[ _-]?day|daily|\brpd\b|\btpd\b", re.I)
# Google puts the wait in the body instead of a Retry-After header
_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


def parse_retry_after(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


def error_from_response(response: httpx.Response) -> ProviderError:
    status = response.status_code
    retry_after = parse_retry_after(response.headers.get("retry-after"))
    try:
        body = response.text[:2000]
    except Exception:
        body = ""
    # keep only a short machine-ish hint; the body can quote our prompt back
    detail = f"http {status}"
    if retry_after is None:
        found = _RETRY_DELAY.search(body)
        retry_after = float(found.group(1)) if found else None
    if status == 429:
        daily = bool(_DAILY.search(body))
        return ProviderError("quota" if daily else "rate_limited", status=status,
                             retry_after=retry_after, daily=daily, detail=detail)
    if status == 402:
        return ProviderError("quota", status=status, retry_after=retry_after, daily=True, detail=detail)
    if status in (401, 403):
        return ProviderError("auth", status=status, detail=detail)
    if status in (408, 409, 425) or status >= 500:
        return ProviderError("server", status=status, retry_after=retry_after, detail=detail)
    return ProviderError("bad_request", status=status, detail=detail)


def post_json(http: httpx.Client, url: str, *, headers: dict, body: dict, timeout: float) -> dict:
    try:
        response = http.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.TimeoutException:
        raise ProviderError("timeout", detail="no answer in time") from None
    except httpx.HTTPError as exc:
        raise ProviderError("network", detail=type(exc).__name__) from None
    if response.status_code >= 400:
        raise error_from_response(response)
    try:
        data = response.json()
    except ValueError:
        raise ProviderError("bad_response", status=response.status_code, detail="body is not JSON") from None
    if not isinstance(data, dict):
        raise ProviderError("bad_response", status=response.status_code, detail="body is not an object")
    return data


def as_int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
