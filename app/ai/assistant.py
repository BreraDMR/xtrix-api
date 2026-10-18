"""What the /assistant endpoints call (next wave):

    draft = make_plan(db, user, text, locale=...)            # POST /assistant/plan
    result = execute_saved_plan(db, user, plan_id, locale=...) # POST /assistant/execute

make_plan checks the monthly limit, asks the model for a plan and keeps it for 10 minutes. Only a kept
plan can be executed, only by the user it was made for, and only once - the client can't send its own.
Errors: AILimitReached / AIBusy (-> 429 + Retry-After), AINotConfigured / AIUnavailable (-> 503),
PlanNotFound (-> 404), ValueError for a too long request (-> 422).
"""
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.ai.config import AIConfig, load_ai_config
from app.ai.context import build_context
from app.ai.cooldown import utc_now
from app.ai.errors import AIError, AINotConfigured
from app.ai.executor import ExecutionResult, execute_plan
from app.ai.persona import AssistantPersona, effective_assistant
from app.ai.plan import AssistantPlan
from app.ai.prompt import build_messages
from app.ai.router import LLMRouter, Role, get_router
from app.ai.usage import release, reserve_request, settle
from app.api.core.logger import get_logger
from app.api.core.permissions import get_user_business_id
from app.api.crud import crud_business
from app.db.models.user import User
from app.db.models.user_preferences import UserPreferences

logger = get_logger("ai.assistant")
MAX_REQUEST_CHARS = 2000
PLAN_TTL = timedelta(minutes=10)


class PlanNotFound(Exception):
    """No such plan for this user: never made, someone else's, expired, or already executed."""


@dataclass
class _Kept:
    user_id: int
    plan: AssistantPlan
    expires_at: datetime


class PlanStore:
    """Plans waiting for confirmation. In-process: with several workers the execute call has to land
    on the worker that made the plan - move this to Redis or a table before scaling out."""

    def __init__(self, ttl: timedelta = PLAN_TTL, clock: Optional[Callable[[], datetime]] = None):
        self.ttl = ttl
        self.clock = clock or utc_now
        self._plans: dict[str, _Kept] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: datetime) -> None:
        for key in [k for k, v in self._plans.items() if v.expires_at <= now]:
            del self._plans[key]

    def save(self, *, user_id: int, plan: AssistantPlan) -> tuple[str, datetime]:
        now = self.clock()
        plan_id = secrets.token_urlsafe(16)
        with self._lock:
            self._sweep(now)
            self._plans[plan_id] = _Kept(user_id, plan, now + self.ttl)
        return plan_id, now + self.ttl

    def peek(self, plan_id: str, *, user_id: int) -> Optional[AssistantPlan]:
        with self._lock:
            self._sweep(self.clock())
            kept = self._plans.get(plan_id)
            return kept.plan if kept and kept.user_id == user_id else None

    def take(self, plan_id: str, *, user_id: int) -> AssistantPlan:
        with self._lock:
            self._sweep(self.clock())
            kept = self._plans.get(plan_id)
            if not kept or kept.user_id != user_id:
                raise PlanNotFound(plan_id)
            del self._plans[plan_id]
            return kept.plan


_default_store = PlanStore()


@dataclass
class PlanDraft:
    plan_id: str
    plan: AssistantPlan
    assistant: AssistantPersona
    expires_at: datetime


def user_timezone(db: Session, user: User, config: AIConfig) -> str:
    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
    tz = (prefs.timezone or "").strip() if prefs else ""
    # "UTC" is the column default, so it mostly means "never set" - salons here are in Prague
    return tz if tz and tz != "UTC" else config.default_timezone


def make_plan(db: Session, user: User, text: str, *, locale: str, tz: Optional[str] = None,
              role: Role = "fast", router: Optional[LLMRouter] = None, store: Optional[PlanStore] = None,
              config: Optional[AIConfig] = None) -> PlanDraft:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty request")
    if len(text) > MAX_REQUEST_CHARS:
        raise ValueError(f"request longer than {MAX_REQUEST_CHARS} characters")
    config = config or (router.config if router else load_ai_config())
    router = router or get_router()
    store = store or _default_store
    if not router.slots:
        raise AINotConfigured("no model slot is configured")
    usage = reserve_request(db, user, config, role=role)
    try:
        tz = tz or user_timezone(db, user, config)
        context = build_context(db, user, text, locale=locale, tz_name=tz)
        messages = build_messages(text, context)
        result = router.generate(messages, AssistantPlan, role=role)
    except AIError as err:
        if err.prompt_tokens or err.completion_tokens:
            # the tokens were burnt, so the request counts
            settle(db, usage, provider="pool", slot_index=None, model="-", prompt_tokens=err.prompt_tokens,
                   completion_tokens=err.completion_tokens, cost_estimate=0.0, succeeded=False)
        else:
            release(db, usage)
        logger.warning("ai plan failed: user=%s code=%s chars=%s", user.id, err.code, len(text))
        raise
    except Exception:
        release(db, usage)
        raise
    settle(db, usage, provider=result.provider, slot_index=result.slot_index, model=result.model,
           prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
           cost_estimate=result.cost_estimate, succeeded=True)
    plan_id, expires_at = store.save(user_id=user.id, plan=result.value)
    business_id = get_user_business_id(user, db)
    business = crud_business.get_business(db, business_id) if business_id else None
    logger.info("ai plan: user=%s business=%s actions=%s questions=%s chars=%s",
                user.id, business_id, len(result.value.actions), len(result.value.questions), len(text))
    return PlanDraft(plan_id, result.value, effective_assistant(business, locale), expires_at)


def execute_saved_plan(db: Session, user: User, plan_id: str, *, locale: str, tz: Optional[str] = None,
                       store: Optional[PlanStore] = None, config: Optional[AIConfig] = None) -> ExecutionResult:
    store = store or _default_store
    plan = store.take(plan_id, user_id=user.id)
    tz = tz or user_timezone(db, user, config or load_ai_config())
    return execute_plan(db, user, plan, locale=locale, tz=tz)
