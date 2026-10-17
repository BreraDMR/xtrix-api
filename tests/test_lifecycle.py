"""Happy paths for delete/restore - these used to 500 on a NameError."""
import pytest

from tests.tenant_world import build_tenant


@pytest.mark.parametrize("kind", ["services", "visits"])
def test_soft_delete_and_restore(client, make_user, headers_for, kind):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    h = a["headers"]
    obj_id = a[kind[:-1]]["id"]

    assert client.delete(f"/{kind}/{obj_id}", headers=h).status_code == 200
    assert client.get(f"/{kind}/{obj_id}", headers=h).status_code == 404
    assert client.post(f"/{kind}/{obj_id}/restore", headers=h).status_code == 200
    assert client.get(f"/{kind}/{obj_id}", headers=h).status_code == 200


@pytest.mark.parametrize("path,key", [
    ("/products/{}", "product"), ("/expenses/{}", "expense"), ("/employees/{}", "employee"),
    ("/expenses/categories/{}", "category"), ("/loyalty/promotions/{}", "promotion"),
])
def test_delete(client, make_user, headers_for, path, key):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    r = client.delete(path.format(a[key]["id"]), headers=a["headers"])
    assert r.status_code in (200, 204), r.text


def test_update_service_and_visit(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    h = a["headers"]
    r = client.put(f"/services/{a['service']['id']}", json={"name": "Alpha Deep Massage", "price": 650}, headers=h)
    assert r.status_code == 200 and r.json()["price"] == 650
    r = client.put(f"/visits/{a['visit']['id']}", json={"note": "moved inside"}, headers=h)
    assert r.status_code == 200 and r.json()["note"] == "moved inside"


def test_employee_sees_own_salary_but_not_a_colleagues(client, db, make_user, headers_for):
    from app.db.models.user import User

    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    me = headers_for(db.get(User, a["employee_user_id"]))
    emp, sal = a["employee"]["id"], a["salary"]["id"]
    assert client.get(f"/employees/{emp}/salary/{sal}", headers=me).status_code == 200
    assert client.get(f"/employees/{emp}/salary/{sal}/payments", headers=me).status_code == 200

    colleague = client.post("/employees/create-with-user", headers=a["headers"],
                            json={"full_name": "Alpha Colleague", "email": "colleague@example.com", "phone": "+420700112233"})
    assert colleague.status_code == 200, colleague.text
    colleague_user = db.get(User, colleague.json()["user_id"])
    assert client.get(f"/employees/{emp}/salary/{sal}", headers=headers_for(colleague_user)).status_code == 403
