from tests import test_waitlist
import pytest
from app.db.models.user import User
from app.db.models.waitlist import WaitlistEntry

salon = test_waitlist.salon


@pytest.mark.parametrize("role", ["employee", "specialist"])
@pytest.mark.parametrize("action", ["read", "cancel"])
def test_salon_staff_cannot_read_or_cancel_the_owner_waitlist(client, db, salon, headers_for, role, action):
    joined = test_waitlist._join(client, salon)
    assert joined.status_code in (200, 201, 202), joined.text
    row = db.query(WaitlistEntry).filter_by(business_id=salon["business"]["id"]).one()
    employee = client.post("/employees/create-with-user", headers=salon["headers"], json={
        "full_name": "Restricted Staff", "email": "restricted.wait@example.com", "phone": "+420717171717",
        "position": "staff", "base_salary": 1000,
    })
    assert employee.status_code in (200, 201), employee.text
    user = db.query(User).filter_by(email="restricted.wait@example.com").one()
    user.role = role
    db.commit()
    headers = headers_for(user)
    result = client.get("/waitlist", headers=headers) if action == "read" else client.post(
        f"/waitlist/{row.id}/cancel", headers=headers)
    assert result.status_code == 403, result.text
    db.refresh(row)
    assert row.status == "active"
