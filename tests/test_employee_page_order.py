"""Employee array pages have a stable order inside the allowed owner scope."""
import pytest
from sqlalchemy import insert

from app.api.core.pagination import PaginationParams
from app.api.crud.crud_employee import list_employees
from app.db.models.employee import Employee
from app.db.models.user import User


@pytest.mark.parametrize('is_active,multiple_owners', [(None, False), (True, True), (False, False)])
def test_employee_pages_are_ordered_before_pagination_and_keep_scope(db, make_user, is_active, multiple_owners):
    owner = make_user('business_owner')
    other = make_user('business_owner')
    active = is_active is not False
    db.execute(insert(User), [dict(id=1000 + i, full_name=f'Ordered Staff {i}',
                                  hashed_password='unused-synthetic-hash', role='specialist')
                              for i in range(127)])
    # Physical insertion order is the opposite of the directory order.
    db.execute(insert(Employee), [dict(id=2000 + i, business_owner_id=owner.id,
                                      employee_user_id=1000 + i, is_active=active)
                                  for i in reversed(range(125))])
    db.execute(insert(Employee), [
        dict(id=1998, business_owner_id=other.id, employee_user_id=1125, is_active=active),
        dict(id=1999, business_owner_id=owner.id, employee_user_id=1126, is_active=not active),
    ])
    db.commit()
    scope = {'business_owner_ids': [owner.id]} if multiple_owners else {'business_owner_id': owner.id}
    pages = [list_employees(db, **scope, is_active=is_active,
                            pagination=PaginationParams(page=page, per_page=100)) for page in (1, 2)]
    assert [total for _, total in pages] == [125, 125]
    assert [len(rows) for rows, _ in pages] == [100, 25]
    ids = [[employee.id for employee in rows] for rows, _ in pages]
    assert ids[0] == list(range(2000, 2100))
    assert ids[1] == list(range(2100, 2125))
    assert not set(ids[0]) & set(ids[1])
    unpaged, total = list_employees(db, **scope, is_active=is_active)
    assert total == 125
    assert [employee.id for employee in unpaged] == list(range(2000, 2125))
