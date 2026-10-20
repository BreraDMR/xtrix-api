"""A conversation stays private to one user in exactly their current business or solo scope."""
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.api.core.clock import utcnow
from app.api.crud.crud_assistant_conversation import resolve_conversation, scoped_conversations
from app.db.models.assistant_conversation import AssistantConversation
from app.db.models.business import Business
from app.db.models.employee import Employee


def business(db):
    row = Business(name='Private workspace', business_type='salon')
    db.add(row)
    db.commit()
    return row


def chat(db, owner, business_id=None):
    row = AssistantConversation(owner_user_id=owner.id, business_id=business_id, client_request_id=uuid4(),
                                request_hash='a' * 64, title='Private chat')
    db.add(row)
    db.commit()
    return row


def missing(db, user, row, **kwargs):
    with pytest.raises(HTTPException) as error:
        resolve_conversation(db, user, row.id, **kwargs)
    assert error.value.status_code == 404
    assert error.value.detail['code'] == 'conversation_not_found'
    assert row.title not in str(error.value.detail)


def test_two_solo_users_never_share_history(db, make_user):
    first, second = make_user(), make_user()
    row = chat(db, first)
    assert resolve_conversation(db, first, row.id).id == row.id
    missing(db, second, row)
    assert scoped_conversations(db, second).count() == 0


def test_same_business_does_not_make_conversations_shared(db, make_user):
    workspace = business(db)
    first, second = make_user(business_id=workspace.id), make_user(business_id=workspace.id)
    row = chat(db, first, workspace.id)
    missing(db, second, row)
    assert scoped_conversations(db, second).count() == 0


@pytest.mark.parametrize('change', ['other_business', 'solo'])
def test_original_owner_cannot_read_history_after_scope_change(db, make_user, change):
    old = business(db)
    owner = make_user(business_id=old.id)
    row = chat(db, owner, old.id)
    owner.business_id = business(db).id if change == 'other_business' else None
    db.commit()
    missing(db, owner, row)
    assert scoped_conversations(db, owner).count() == 0


def test_solo_history_does_not_appear_after_joining_a_business(db, make_user):
    owner = make_user()
    row = chat(db, owner)
    owner.business_id = business(db).id
    db.commit()
    missing(db, owner, row)


def test_staff_scope_comes_from_active_employee_record(db, make_user):
    workspace = business(db)
    owner = make_user('business_owner')
    owner.business_id = workspace.id
    db.commit()
    staff = make_user('specialist')
    employee = Employee(business_owner_id=owner.id, employee_user_id=staff.id, is_active=True)
    db.add(employee)
    db.commit()
    row = chat(db, staff, workspace.id)
    assert staff.business_id is None
    assert resolve_conversation(db, staff, row.id).id == row.id
    missing(db, owner, row)
    employee.is_active = False
    db.commit()
    missing(db, staff, row)


def test_archive_can_be_resolved_only_explicitly_and_by_its_owner(db, make_user):
    owner, stranger = make_user(), make_user()
    row = chat(db, owner)
    row.archived_at = utcnow()
    db.commit()
    missing(db, owner, row)
    assert resolve_conversation(db, owner, row.id, allow_archived=True, lock=True).id == row.id
    missing(db, stranger, row, allow_archived=True)


def test_deleted_business_and_non_staff_role_hide_history(db, make_user):
    workspace = business(db)
    owner = make_user(business_id=workspace.id)
    row = chat(db, owner, workspace.id)
    workspace.deleted_at = utcnow()
    db.commit()
    missing(db, owner, row)
    assert scoped_conversations(db, owner).count() == 0
    other = make_user('client')
    solo = chat(db, other)
    missing(db, other, solo)
    assert scoped_conversations(db, other).count() == 0


def test_deactivated_staff_with_stale_user_business_cannot_read_old_history(db, make_user):
    from app.api.crud import crud_employee
    from app.schemas.employee import EmployeeCreate, EmployeeUpdate
    workspace = business(db)
    owner = make_user('business_owner')
    owner.business_id = workspace.id
    db.commit()
    staff = make_user('specialist')
    employee = crud_employee.create_employee(db, EmployeeCreate(employee_user_id=staff.id), owner.id)
    row = chat(db, staff, workspace.id)
    assert staff.business_id == workspace.id
    assert staff.employer_id == owner.id
    assert resolve_conversation(db, staff, row.id).id == row.id
    crud_employee.update_employee(db, employee, EmployeeUpdate(is_active=False))
    assert staff.business_id == workspace.id
    missing(db, staff, row)
    assert scoped_conversations(db, staff).count() == 0


def test_staff_current_employer_overrides_stale_user_business(db, make_user):
    old, current = business(db), business(db)
    owner = make_user('business_owner')
    owner.business_id = current.id
    db.commit()
    staff = make_user('specialist', business_id=old.id, employer_id=owner.id)
    db.add(Employee(business_owner_id=owner.id, employee_user_id=staff.id, is_active=True))
    db.commit()
    old_chat = chat(db, staff, old.id)
    current_chat = chat(db, staff, current.id)
    missing(db, staff, old_chat)
    assert resolve_conversation(db, staff, current_chat.id).id == current_chat.id
