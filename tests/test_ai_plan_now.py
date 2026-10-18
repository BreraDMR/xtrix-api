"""make_plan can be pinned to a fixed "now", so the eval phrases with relative dates stay stable."""
import json
from datetime import datetime

import httpx

from app.ai import assistant
from app.ai.config import load_ai_config
from app.ai.cooldown import CooldownBoard
from app.ai.router import build_router
from tests.tenant_world import build_tenant

ENV = {"LLM_1_BASE_URL": "https://p1.example/v1", "LLM_1_API_KEY": "k1", "LLM_1_MODEL": "m1"}


def test_make_plan_uses_the_given_now(db, client, make_user, headers_for):
    t = build_tenant(client, make_user, headers_for, "Gamma", "+42070033")
    seen = []

    def model(request):
        seen.append(json.loads(request.content))
        body = {"summary": "ok", "questions": [], "actions": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    cfg = load_ai_config(ENV)
    router = build_router(cfg, http=httpx.Client(transport=httpx.MockTransport(model)), board=CooldownBoard())
    db.expire_all()
    owner = db.merge(t["owner"])
    # naive UTC, like everything else in the app: 07:15 UTC is 09:15 in Prague in October
    assistant.make_plan(db, owner, "tomorrow at ten", locale="en", tz="Europe/Prague",
                        now=datetime(2026, 10, 19, 7, 15), router=router, config=cfg,
                        store=assistant.PlanStore())
    user_message = seen[0]["messages"][-1]["content"]
    data = json.loads(user_message[len("<data>\n"):-len("\n</data>")])
    assert data["context"]["now"] == "2026-10-19T09:15:00+02:00"
    assert data["context"]["weekday"] == "Monday"
