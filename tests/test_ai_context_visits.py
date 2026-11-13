"""The model can only move, cancel or note a visit whose id it was given. Before this the context had
clients without their visits, so "перенеси стрижку Анны на пятницу" always ended in "which visit id?"."""
import json
from datetime import datetime, timedelta

from app.ai.context import build_context
from app.db.models.visit import Visit
from tests import test_ai_assistant as base
from tests.tenant_world import MARKER

ab = base.ab
fresh = base.fresh


def visits_of(ctx, client_id):
    return next(c for c in ctx["client_matches"] if c["id"] == client_id).get("visits", [])


def test_a_matched_client_comes_with_their_visits(db, ab):
    a, b = ab
    ctx = build_context(db, fresh(db, b["owner"]), "перенеси визит Beta Klientka на пятницу", locale="ru",
                        tz_name="Europe/Prague")
    [visit] = visits_of(ctx, b["client"]["id"])
    assert visit["id"] == b["visit"]["id"]
    assert visit["service"] == b["service"]["name"]
    assert visit["specialist_id"] == b["owner"].id and visit["specialist"] == b["owner"].full_name
    assert visit["status"] == "planned"
    # the salon's wall clock, the same way the person talks about it
    assert visit["start"].endswith(("+01:00", "+02:00"))
    text = json.dumps(ctx, ensure_ascii=False)
    assert "visit note" not in text  # notes can hold anything, they stay out
    assert MARKER not in text


def test_cancelled_deleted_and_far_visits_stay_out(db, ab):
    _, b = ab
    base_visit = db.get(Visit, b["visit"]["id"])
    extra = []
    for days, status, deleted in ((3, "cancelled", False), (4, "planned", True), (120, "planned", False)):
        start = datetime.utcnow().replace(microsecond=0) + timedelta(days=days)
        v = Visit(client_id=base_visit.client_id, specialist_id=base_visit.specialist_id,
                  business_id=base_visit.business_id, service_id=base_visit.service_id,
                  service_name=base_visit.service_name, date_start=start, date_end=start + timedelta(hours=1),
                  status=status, deleted_at=datetime.utcnow() if deleted else None)
        db.add(v)
        extra.append(v)
    db.commit()
    ctx = build_context(db, fresh(db, b["owner"]), "Beta Klientka", locale="en", tz_name="Europe/Prague")
    assert [v["id"] for v in visits_of(ctx, b["client"]["id"])] == [b["visit"]["id"]]


def test_a_followup_without_a_name_finds_the_client_from_the_conversation(db, ab):
    _, b = ab
    history = [{"role": "user", "text": "Запиши Beta Klientka на массаж"},
               {"role": "assistant", "text": "Сашенька запишет Beta Klientka на массаж",
                "execution": {"status": "succeeded"}}]
    ctx = build_context(db, fresh(db, b["owner"]), "перенеси её на пятницу", locale="ru",
                        tz_name="Europe/Prague", history=history)
    assert [c["id"] for c in ctx["client_matches"]] == [b["client"]["id"]]
    assert visits_of(ctx, b["client"]["id"])


def test_the_prompt_explains_where_visit_ids_come_from():
    from app.ai.prompt import system_prompt
    text = system_prompt()
    assert "client_matches" in text and "visits" in text and "reschedule_visit" in text
