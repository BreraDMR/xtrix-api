from tests.test_imports import _upload, _csv
from app.db.models.user import User



def test_inactive_specialist_cannot_inherit_owner_import_access(client, db, make_user, headers_for):
    from tests.tenant_world import build_tenant
    world = build_tenant(client, make_user, headers_for, "ImportScope", "+42071500")
    body = _csv([["Private imported client", "+420715555555", "private.import@example.com"]],
                headers=["Name", "Phone", "Email"])
    upload = _upload(client, world["headers"], body)
    assert upload.status_code == 200, upload.text
    user = db.get(User, world["employee_user_id"])
    user.role = "specialist"
    db.commit()
    disabled = client.put(f"/employees/{world['employee']['id']}", headers=world["headers"],
                          json={"is_active": False})
    assert disabled.status_code == 200, disabled.text
    db.refresh(user)
    assert user.business_id == world["business"]["id"] and user.employer_id == world["owner"].id
    headers = headers_for(user)
    response = client.get("/businesses/me/imports", headers=headers)
    assert response.status_code == 403, response.text
    assert client.get(f"/businesses/me/imports/{upload.json()['id']}/rows", headers=headers).status_code == 403
