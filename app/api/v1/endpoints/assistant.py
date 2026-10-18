"""The text and voice assistant (plan sections Б and В).

POST /plan asks the model for a plan and keeps it on the server; nothing changes until POST /execute
runs that saved plan as the same user. POST /transcribe only turns speech into text - the user reads
and edits it before asking for a plan.
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.ai import assistant, stt
from app.ai.config import AIConfig, load_ai_config
from app.ai.display import describe_plan
from app.ai.errors import AIBusy, AIError, AILimitReached, AINotConfigured
from app.ai.persona import effective_assistant
from app.ai.router import LLMRouter, get_router
from app.ai.usage import _month_bounds, monthly_limit, used_this_month
from app.api import deps
from app.api.core.clock import utcnow
from app.api.core.i18n import _
from app.api.core.logger import get_logger
from app.api.core.permissions import get_user_business_id
from app.api.crud import crud_business
from app.api.middleware.rate_limit import (
    ASSISTANT_EXECUTE_LIMIT,
    ASSISTANT_PLAN_LIMIT,
    ASSISTANT_TRANSCRIBE_LIMIT,
    limiter,
    user_or_ip,
)
from app.db.models.user import User
from app.schemas.assistant import (
    AssistantStatusOut,
    ExecuteOut,
    ExecuteRequest,
    PlanOut,
    PlanRequest,
    TranscribeOut,
)

router = APIRouter()
logger = get_logger("assistant")
# same roles that can work with clients and visits
staff_user = deps.role_required("admin", "business_owner", "specialist", "employee")

AI_ERRORS = {
    429: {"description": "Monthly assistant limit used up (code ai_limit_reached), Retry-After = seconds "
                         "to the next month; or too many requests a minute"},
    503: {"description": "ai_busy (all provider slots resting, Retry-After), ai_not_configured or "
                         "ai_unavailable"},
}


# --- injectable pieces, so tests can swap in fakes ---------------------------

def llm_router() -> Optional[LLMRouter]:
    try:
        return get_router()
    except (AIError, ValueError) as exc:
        # e.g. the CLI or fake provider in production, or a broken LLM_* setting
        logger.warning("assistant model router unavailable: %s", type(exc).__name__)
        return None


def stt_router() -> Optional[stt.SttRouter]:
    try:
        return stt.get_stt_router()
    except (AIError, ValueError) as exc:
        logger.warning("assistant speech router unavailable: %s", type(exc).__name__)
        return None


def plan_store():
    return assistant._default_store


def _limits_config(router: Optional[LLMRouter]) -> AIConfig:
    if router is not None:
        return router.config
    try:
        return load_ai_config()
    except ValueError:
        return AIConfig()


# --- errors ------------------------------------------------------------------

def ai_http_error(err: AIError, locale: str) -> HTTPException:
    if isinstance(err, AILimitReached):
        return HTTPException(429, detail={"message": _("ai_limit_reached", locale), "code": err.code,
                                          "limit": err.limit, "used": err.used},
                             headers={"Retry-After": str(err.retry_after)})
    if isinstance(err, AIBusy):
        return HTTPException(503, detail={"message": _("ai_busy", locale), "code": err.code,
                                          "retry_after": err.retry_after},
                             headers={"Retry-After": str(err.retry_after)})
    if isinstance(err, AINotConfigured):
        return HTTPException(503, detail={"message": _("ai_not_configured", locale), "code": err.code})
    return HTTPException(503, detail={"message": _("ai_unavailable", locale), "code": "ai_unavailable"})


def _bad_input(field: str, code: str, message: str) -> RequestValidationError:
    # same 422 shape as any other field error: error.detail.validation_errors[0].code
    return RequestValidationError([{"loc": ("body", field), "msg": message, "type": code, "input": None}])


# --- endpoints ---------------------------------------------------------------

@router.get("/status", response_model=AssistantStatusOut, summary="Is the assistant on, and how much is left")
def get_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(staff_user),
    locale: str = Depends(deps.get_locale),
    model: Optional[LLMRouter] = Depends(llm_router),
    speech: Optional[stt.SttRouter] = Depends(stt_router),
):
    config = _limits_config(model)
    business_id = get_user_business_id(current_user, db)
    business = crud_business.get_business(db, business_id) if business_id else None
    persona = effective_assistant(business, locale)
    now = utcnow()
    return {
        "enabled": bool(model and model.slots),
        "voice_enabled": bool(speech and speech.slots),
        "assistant": persona.__dict__,
        "limit": monthly_limit(db, current_user, config),
        "used": used_this_month(db, current_user, now),
        "resets_at": _month_bounds(now)[1],
    }


@router.post("/plan", response_model=PlanOut, responses=AI_ERRORS,
             summary="Turn a request into a plan to confirm (nothing is changed yet)")
@limiter.limit(ASSISTANT_PLAN_LIMIT, key_func=user_or_ip)
def make_plan(
    request: Request,
    body: PlanRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(staff_user),
    locale: str = Depends(deps.get_locale),
    model: Optional[LLMRouter] = Depends(llm_router),
    store=Depends(plan_store),
):
    locale = body.locale or locale
    if model is None:
        raise ai_http_error(AINotConfigured(), locale)
    tz = assistant.user_timezone(db, current_user, model.config)
    try:
        draft = assistant.make_plan(db, current_user, body.text, locale=locale, tz=tz, router=model, store=store)
    except AIError as err:
        raise ai_http_error(err, locale)
    except ValueError as err:
        raise _bad_input("text", "invalid_request", str(err))
    shown = describe_plan(db, current_user, draft.plan, tz=tz)
    actions = [{**a.model_dump(mode="json"), "display": d.to_dict()} for a, d in zip(draft.plan.actions, shown)]
    return {
        "plan_id": draft.plan_id,
        "summary": draft.plan.summary,
        "questions": draft.plan.questions,
        "actions": actions,
        "assistant": draft.assistant.__dict__,
        "expires_at": draft.expires_at,
    }


@router.post("/execute", response_model=ExecuteOut,
             responses={404: {"description": "plan_not_found: unknown, someone else's, expired or already run"}},
             summary="Run a saved plan once, as the current user")
@limiter.limit(ASSISTANT_EXECUTE_LIMIT, key_func=user_or_ip)
def execute_plan(
    request: Request,
    body: ExecuteRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(staff_user),
    locale: str = Depends(deps.get_locale),
    model: Optional[LLMRouter] = Depends(llm_router),
    store=Depends(plan_store),
):
    try:
        result = assistant.execute_saved_plan(db, current_user, body.plan_id, locale=locale, store=store,
                                              config=_limits_config(model))
    except assistant.PlanNotFound:
        raise HTTPException(404, detail={"message": _("ai_plan_not_found", locale), "code": "plan_not_found"})
    return result.to_dict()


@router.post("/transcribe", response_model=TranscribeOut, responses=AI_ERRORS,
             summary="Speech to text for the assistant input (not planned automatically)")
@limiter.limit(ASSISTANT_TRANSCRIBE_LIMIT, key_func=user_or_ip)
def transcribe(
    request: Request,
    file: UploadFile = File(..., description="audio/webm, audio/ogg, audio/mp4, audio/mpeg or audio/wav, up to 5 MB"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(staff_user),
    locale: str = Depends(deps.get_locale),
    model: Optional[LLMRouter] = Depends(llm_router),
    speech: Optional[stt.SttRouter] = Depends(stt_router),
):
    data = file.file.read(stt.MAX_AUDIO_BYTES + 1)
    try:
        if speech is None:
            stt.check_audio(data, file.content_type)  # bad input is still a 422 first
            raise AINotConfigured()
        text = stt.transcribe(db, current_user, data, file.content_type, locale=locale, router=speech,
                              config=_limits_config(model))
    except stt.AudioRejected as err:
        raise _bad_input("file", err.code, str(err))
    except AIError as err:
        raise ai_http_error(err, locale)
    return {"text": text}
