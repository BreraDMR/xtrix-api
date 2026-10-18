"""What the /assistant endpoints call:

    draft = make_plan(db, user, text, locale=...)            # POST /assistant/plan
    result = execute_saved_plan(db, user, plan_id, locale=...) # POST /assistant/execute

make_plan takes one request from the monthly limit, asks the model for a plan and keeps it for 10 minutes.
Only a kept plan can be executed, only by the user it was made for, and only once - the client can't
send its own. Errors: AILimitReached (-> 429 + Retry-After), AIBusy (-> 503 + Retry-After),
AINotConfigured / AIUnavailable (-> 503), PlanNotFound (-> 404), ValueError for a bad request (-> 422).
"""
import hashlib
import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, Union

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
from app.ai.shared import shared_redis
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
    """Plans waiting for confirmation, in this process only. SharedPlanStore below puts them in Redis
    so the execute call can land on any worker."""

    def __init__(self, ttl: timedelta = PLAN_TTL, clock: Optional[Callable[[], datetime]] = None):
        self.ttl = ttl
        self.clock = clock or utc_now
        self._plans: dict[str, _Kept] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: datetime) -> None:
        for key in [k for k, v in self._plans.items() if v.expires_at <= now]:
            del self._plans[key]

    def save(self, *, user_id: int, plan: AssistantPlan, plan_id: Optional[str] = None) -> tuple[str, datetime]:
        now = self.clock()
        plan_id = plan_id or secrets.token_urlsafe(16)
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


class SharedPlanStore:
    """Redis when it answers (SET with the TTL, one-shot GETDEL), process memory when it doesn't.

    The key is the user id plus a hash of the plan id: someone else's id lands on a key that doesn't
    exist instead of deleting the owner's plan, and the plan id itself never sits in Redis."""

    def __init__(self, *, redis: Callable = shared_redis, ttl: timedelta = PLAN_TTL,
                 clock: Optional[Callable[[], datetime]] = None):
        self.redis = redis
        self.ttl = ttl
        self.clock = clock or utc_now
        self.memory = PlanStore(ttl, self.clock)

    @staticmethod
    def _key(plan_id: str, user_id: int) -> str:
        return f"ai:plan:{int(user_id)}:{hashlib.sha256(plan_id.encode()).hexdigest()[:40]}"

    def _decode(self, raw: Optional[str], user_id: int) -> Optional[AssistantPlan]:
        if not raw:
            return None
        try:
            kept = json.loads(raw)
            if kept.get("user_id") != user_id or datetime.fromisoformat(kept["expires_at"]) <= self.clock():
                return None
            # validated again on the way out - whatever sits in Redis is not trusted blindly
            return AssistantPlan.model_validate_json(json.dumps(kept["plan"]))
        except Exception:
            logger.warning("ai plan store: unreadable plan in redis, user=%s", user_id)
            return None

    def save(self, *, user_id: int, plan: AssistantPlan) -> tuple[str, datetime]:
        plan_id = secrets.token_urlsafe(16)
        expires_at = self.clock() + self.ttl
        client = self.redis()
        if client is not None:
            try:
                payload = json.dumps({"user_id": user_id, "expires_at": expires_at.isoformat(),
                                      "plan": plan.model_dump(mode="json")})
                client.set(self._key(plan_id, user_id), payload, ex=int(self.ttl.total_seconds()))
                return plan_id, expires_at
            except Exception as exc:
                logger.warning("ai plan store: redis write failed (%s), keeping it in memory", type(exc).__name__)
        return self.memory.save(user_id=user_id, plan=plan, plan_id=plan_id)

    def _from_redis(self, plan_id: str, user_id: int, *, delete: bool) -> Optional[AssistantPlan]:
        client = self.redis()
        if client is None:
            return None
        key = self._key(plan_id, user_id)
        try:
            raw = client.getdel(key) if delete else client.get(key)
        except Exception as exc:
            logger.warning("ai plan store: redis read failed (%s)", type(exc).__name__)
            return None
        return self._decode(raw, user_id)

    def peek(self, plan_id: str, *, user_id: int) -> Optional[AssistantPlan]:
        return self._from_redis(plan_id, user_id, delete=False) or self.memory.peek(plan_id, user_id=user_id)

    def take(self, plan_id: str, *, user_id: int) -> AssistantPlan:
        plan = self._from_redis(plan_id, user_id, delete=True)
        if plan is not None:
            return plan
        return self.memory.take(plan_id, user_id=user_id)


Store = Union[PlanStore, SharedPlanStore]
_default_store = SharedPlanStore()


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
              role: Role = "fast", router: Optional[LLMRouter] = None, store: Optional[Store] = None,
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
                       store: Optional[Store] = None, config: Optional[AIConfig] = None) -> ExecutionResult:
    store = store or _default_store
    plan = store.take(plan_id, user_id=user.id)
    tz = tz or user_timezone(db, user, config or load_ai_config())
    return execute_plan(db, user, plan, locale=locale, tz=tz)
