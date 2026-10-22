"""The text and voice assistant (plan sections Б and В).

POST /plan asks the model for a plan and keeps it on the server; nothing changes until POST /execute
runs that saved plan as the same user. POST /transcribe only turns speech into text - the user reads
and edits it before asking for a plan.
"""
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.ai import assistant, stt
from app.ai.config import AIConfig, load_ai_config
from app.ai.display import describe_plan
from app.ai.errors import AIBusy, AIError, AILimitReached, AINotConfigured
from app.ai.persona import effective_assistant
from app.ai.router import LLMRouter, get_router
from app.ai.usage import _day_bounds, _month_bounds, monthly_limit, pilot_used_today, used_this_month
from app.api import deps
from app.api.core.ai_metering import consume_ai_unit
from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.subscription_access import get_subscription_access_state
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
from app.api.core.assistant_conversation_service import (
    get_conversation_context,
    generation_scope_snapshot,
    prepare_user_request,
    record_assistant_failure,
    record_assistant_success,
    validate_generation_scope,
)
from app.api.crud.crud_assistant_conversation import resolve_conversation
from app.db.models.assistant_conversation import AssistantMessage
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
    429: {"description": "Assistant limit used up (code ai_limit_reached, period month or day in the pilot), "
                         "Retry-After = seconds to the next period; or too many requests a minute"},
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
        key = "ai_daily_limit_reached" if err.period == "day" else "ai_limit_reached"
        return HTTPException(429, detail={"message": _(key, locale), "code": err.code,
                                          "limit": err.limit, "used": err.used, "period": err.period},
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
    if settings.pilot_mode_enabled:
        access = get_subscription_access_state(db, current_user.id)
        limit = (access.get("ai_daily_limit") or 0) if access["can_use_ai"] else 0
        used = pilot_used_today(db, current_user, now)
        resets_at = _day_bounds(now)[1]
        period = "day"
    else:
        limit = monthly_limit(db, current_user, config)
        used = used_this_month(db, current_user, now)
        resets_at = _month_bounds(now)[1]
        period = "month"
    return {
        "enabled": bool(model and model.slots),
        "voice_enabled": bool(speech and speech.slots),
        "assistant": persona.__dict__,
        "limit": limit,
        "used": used,
        "resets_at": resets_at,
        "period": period,
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
    expected_scope = generation_scope_snapshot(db, current_user, locale)
    if model is None:
        raise ai_http_error(AINotConfigured(), locale)

    assistant_msg = None
    if body.conversation_id:
        if not body.client_request_id:
            raise _bad_input("client_request_id", "missing_request_id", "client_request_id is required with conversation_id")
        conv, user_msg, existing_assistant_msg = prepare_user_request(
            db, current_user, body.conversation_id, body.client_request_id, body.text, locale=locale, expected_scope=expected_scope
        )
        if existing_assistant_msg and existing_assistant_msg.status == "succeeded" and existing_assistant_msg.plan_payload:
            return existing_assistant_msg.plan_payload
        if existing_assistant_msg and existing_assistant_msg.status == "failed":
            raise HTTPException(existing_assistant_msg.error_status or 500, detail={"code": existing_assistant_msg.error_code or "ai_unavailable"})
        assistant_msg = existing_assistant_msg
    elif body.client_request_id:
        raise _bad_input("conversation_id", "missing_conversation_id", "conversation_id is required with client_request_id")

    history = []
    if body.conversation_id:
        history = get_conversation_context(db, body.conversation_id, body.client_request_id)

    tz = assistant.user_timezone(db, current_user, model.config)
    try:
        draft = assistant.make_plan(db, current_user, body.text, locale=locale, tz=tz, router=model,
                                    store=store, history=history, expected_scope=expected_scope)
    except AIError as err:
        if assistant_msg:
            http_err = ai_http_error(err, locale)
            retry_after = getattr(err, 'retry_after', None)
            record_assistant_failure(db, body.conversation_id, assistant_msg.id, err.code, http_err.status_code, retry_after)
        raise ai_http_error(err, locale)
    except HTTPException as err:
        if assistant_msg:
            record_assistant_failure(db, body.conversation_id, assistant_msg.id, "request_conflict", err.status_code)
        raise
    except ValueError as err:
        if assistant_msg:
            record_assistant_failure(db, body.conversation_id, assistant_msg.id, "invalid_request", 422)
        raise _bad_input("text", "invalid_request", str(err))
    except Exception as err:
        if assistant_msg:
            record_assistant_failure(db, body.conversation_id, assistant_msg.id, "server_error", 500)
        raise

    shown = describe_plan(db, current_user, draft.plan, tz=tz)
    actions = [{**a.model_dump(mode="json"), "display": d.to_dict()} for a, d in zip(draft.plan.actions, shown)]
    # one JSON-safe shape for both the answer and the stored message: the preview carries
    # naive UTC datetimes, and the response contract writes them as ISO with Z
    plan_data = PlanOut.model_validate({
        "plan_id": draft.plan_id,
        "summary": draft.plan.summary,
        "questions": draft.plan.questions,
        "actions": actions,
        "assistant": draft.assistant.__dict__,
        "expires_at": draft.expires_at.isoformat(),
    }).model_dump(mode="json")
    # A fresh successful generation is the only thing that spends a PRICE unit:
    # replays return earlier, failures never get here, execute is not a generation.
    business_id = get_user_business_id(current_user, db)
    consume_ai_unit(db, business_id, body.client_request_id or uuid4(), succeeded=True,
                    user_id=None if business_id else current_user.id)
    if assistant_msg:
        record_assistant_success(
            db, body.conversation_id, assistant_msg.id, draft.plan.summary, draft.plan_id, plan_data, draft.expires_at
        )
    else:
        db.commit()
    return plan_data


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
    expected_scope = generation_scope_snapshot(db, current_user, locale)
    message = db.query(AssistantMessage).filter(
        AssistantMessage.plan_id == body.plan_id
    ).populate_existing().with_for_update().first()
    if message:
        validate_generation_scope(db, current_user, locale, expected_scope)
        resolve_conversation(db, current_user, message.conversation_id, locale=locale, allow_archived=True)
        if message.result_payload:
            return message.result_payload
        if message.execution_started_at is not None:
            raise HTTPException(409, detail={"code": "request_in_progress"})
        message.execution_started_at = utcnow()
        db.commit()

    validate_generation_scope(db, current_user, locale, expected_scope)
    try:
        result = assistant.execute_saved_plan(db, current_user, body.plan_id, locale=locale, store=store,
                                              config=_limits_config(model), expected_scope=expected_scope)
    except assistant.PlanNotFound:
        raise HTTPException(404, detail={"message": _("ai_plan_not_found", locale), "code": "plan_not_found"})

    result_dict = result.to_dict()
    if message:
        message.result_payload = result_dict
        message.status = "succeeded" if result.ok else "failed"
        db.commit()
    return result_dict


# the multipart wrapping around the file is a boundary and a few headers - 64 KB is plenty
MAX_TRANSCRIBE_BODY = stt.MAX_AUDIO_BYTES + 64 * 1024


class _SizeCheckedRoute(APIRoute):
    """Turns an upload away by its Content-Length, before Starlette spools the whole multipart.
    Chunked uploads have no length - the check after parsing still catches those."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_TRANSCRIBE_BODY:
                err = stt.too_large()
                raise _bad_input("file", err.code, str(err))
            return await handler(request)

        return guarded


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


router.add_api_route("/transcribe", transcribe, methods=["POST"], response_model=TranscribeOut, responses=AI_ERRORS,
                     summary="Speech to text for the assistant input (not planned automatically)",
                     route_class_override=_SizeCheckedRoute)
