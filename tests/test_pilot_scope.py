"""Scope changes at model and execution boundaries never cross salon ownership."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.ai import assistant
from app.ai.config import AIConfig
from app.ai.plan import AssistantPlan
from app.api.core.clock import utcnow
from app.api.core.permissions import check_service_access, check_visit_access, get_user_business_id
from app.api.crud import crud_employee
from app.api.v1.endpoints import assistant as endpoints
from app.db.models.ai_usage import AIUsage
from app.db.models.assistant_conversation import AssistantConversation, AssistantMessage
from app.db.models.business import Business
from app.db.models.employee import Employee
from app.db.models.service import Service
from app.db.models.user import User
from app.db.models.visit import Visit
from app.db.session import SessionLocal
from app.main import app
from app.schemas.employee import EmployeeCreate


class Model:
    config = AIConfig()
    slots = ("fake",)

    def __init__(self, callback=None):
        self.callback = callback
        self.calls = 0

    def generate(self, *args, **kwargs):
        self.calls += 1
        if self.callback:
            self.callback()
        return SimpleNamespace(value=AssistantPlan(summary="Safe response"), provider="test",
                               slot_index=1, model="test", prompt_tokens=1,
                               completion_tokens=1, cost_estimate=0.0)


def salon(db, make_user):
    owner = make_user("business_owner")
    business = Business(name="Scope salon", business_type="salon")
    db.add(business)
    db.commit()
    owner.business_id = business.id
    db.commit()
    staff = make_user("specialist")
    employment = crud_employee.create_employee(db, EmployeeCreate(employee_user_id=staff.id), owner.id)
    return owner, staff, employment


def change_employment(employee_id):
    with SessionLocal() as other:
        other.query(Employee).filter(Employee.id == employee_id).update({"is_active": False})
        other.commit()


def test_legacy_plan_rejects_deactivated_staff_before_model_or_quota(client, db, make_user, headers_for):
    _, staff, employment = salon(db, make_user)
    change_employment(employment.id)
    model = Model()
    app.dependency_overrides[endpoints.llm_router] = lambda: model
    try:
        response = client.post("/assistant/plan", headers=headers_for(staff), json={"text": "Hello"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409, response.text
    assert model.calls == 0
    assert db.query(AIUsage).count() == 0


def test_staff_deactivated_during_model_does_not_save_plan_or_charge_quota(db, make_user):
    _, staff, employment = salon(db, make_user)
    store = assistant.PlanStore()
    model = Model(lambda: change_employment(employment.id))
    with pytest.raises(HTTPException) as exc:
        assistant.make_plan(db, staff, "Hello", locale="en", router=model, store=store)
    assert exc.value.status_code == 409
    assert model.calls == 1
    assert db.query(AIUsage).count() == 0
    assert not store._plans


def test_scope_change_after_reserve_is_rejected_before_model(db, make_user, monkeypatch):
    _, staff, employment = salon(db, make_user)
    reserve = assistant.reserve_request

    def race(*args, **kwargs):
        row = reserve(*args, **kwargs)
        change_employment(employment.id)
        return row

    monkeypatch.setattr(assistant, "reserve_request", race)
    model = Model()
    with pytest.raises(HTTPException) as exc:
        assistant.make_plan(db, staff, "Hello", locale="en", router=model, store=assistant.PlanStore())
    assert exc.value.status_code == 409
    assert model.calls == 0
    assert db.query(AIUsage).count() == 0


def test_business_change_during_model_does_not_persist_conversation_answer(client, db, make_user, headers_for):
    owner, _, _ = salon(db, make_user)
    new_business = Business(name="Other salon", business_type="salon")
    db.add(new_business)
    db.commit()
    user_id, new_id = owner.id, new_business.id

    def switch_business():
        with SessionLocal() as other:
            other.query(User).filter(User.id == user_id).update({"business_id": new_id})
            other.commit()

    chat = AssistantConversation(owner_user_id=owner.id, business_id=owner.business_id,
                                client_request_id=uuid4(), request_hash="a" * 64, title="Old salon")
    db.add(chat)
    db.commit()
    model = Model(switch_business)
    store = assistant.PlanStore()
    app.dependency_overrides[endpoints.llm_router] = lambda: model
    app.dependency_overrides[endpoints.plan_store] = lambda: store
    try:
        response = client.post("/assistant/plan", headers=headers_for(owner), json={
            "text": "Hello", "conversation_id": str(chat.id), "client_request_id": str(uuid4())})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409, response.text
    db.expire_all()
    answer = db.query(AssistantMessage).filter(AssistantMessage.role == "assistant").one()
    assert answer.plan_payload is None
    assert db.query(AIUsage).count() == 0
    assert not store._plans


def test_fired_specialist_cannot_use_own_id_to_read_old_salon_records(db, make_user):
    owner, staff, employment = salon(db, make_user)
    old_business_id, staff_id = owner.business_id, staff.id
    old_visit = Visit(client_id=1, specialist_id=staff_id, business_id=old_business_id,
                      date_start=utcnow() + timedelta(days=1))
    old_service = Service(name="Old salon service", specialist_id=staff_id, business_id=old_business_id)
    crud_employee.fire_employee(db, employment)
    assert staff.business_id is None
    assert not check_visit_access(db, staff, old_visit)
    assert not check_service_access(db, staff, old_service)
    # The same now-solo user may still work on their own personal records.
    personal_visit = Visit(client_id=1, specialist_id=staff_id, business_id=None, date_start=utcnow())
    personal_service = Service(name="Personal", specialist_id=staff_id, business_id=None)
    assert check_visit_access(db, staff, personal_visit)
    assert check_service_access(db, staff, personal_service)


def test_active_staff_uses_current_owner_scope_instead_of_stale_user_business(db, make_user):
    owner, staff, employment = salon(db, make_user)
    current = Business(name="Current salon", business_type="salon")
    db.add(current)
    db.commit()
    owner.business_id = current.id
    db.commit()
    assert staff.business_id != current.id
    assert get_user_business_id(staff, db) == current.id
    assert not check_service_access(db, staff, Service(name="Old", specialist_id=staff.id,
                                                     business_id=staff.business_id))
    assert not check_visit_access(db, staff, Visit(client_id=1, specialist_id=staff.id,
                                                 business_id=staff.business_id, date_start=utcnow()))


def test_execute_rejects_plan_created_under_another_scope(db, make_user):
    owner, _, _ = salon(db, make_user)
    store = assistant.PlanStore()
    draft = assistant.make_plan(db, owner, "Hello", locale="en", router=Model(), store=store)
    other = Business(name="New salon", business_type="salon")
    db.add(other)
    db.commit()
    owner.business_id = other.id
    db.commit()
    with pytest.raises(HTTPException) as exc:
        assistant.execute_saved_plan(db, owner, draft.plan_id, locale="en", store=store)
    assert exc.value.status_code == 409


def test_started_execution_is_not_automatically_retried(client, db, make_user, headers_for, monkeypatch):
    owner = make_user("specialist")
    chat = AssistantConversation(owner_user_id=owner.id, business_id=None, client_request_id=uuid4(),
                                request_hash="a" * 64, title="Uncertain execution")
    db.add(chat)
    db.flush()
    message = AssistantMessage(conversation_id=chat.id, ordinal=1, request_id=uuid4(),
                               request_hash="b" * 64, role="assistant", text="Plan",
                               status="succeeded", plan_id="uncertain-plan", execution_started_at=utcnow())
    db.add(message)
    db.commit()
    calls = []
    monkeypatch.setattr(assistant, "execute_saved_plan", lambda *args, **kwargs: calls.append(1))
    response = client.post("/assistant/execute", headers=headers_for(owner), json={"plan_id": message.plan_id})
    assert response.status_code == 409, response.text
    assert not calls
