"""What a service looks like on the wire: typed specialists and the service's own specialist."""
from tests.tenant_world import build_tenant


def _schema(name):
    from app.main import app
    return app.openapi()["components"]["schemas"][name]


def test_specialists_are_a_typed_list_in_the_contract():
    prop = _schema("ServiceOut")["properties"]["specialists"]
    items = next(v for v in prop.get("anyOf", [prop]) if v.get("type") == "array")["items"]
    ref = items["$ref"].rsplit("/", 1)[-1]
    assert set(_schema(ref)["properties"]) == {"id", "full_name", "email", "phone"}


def test_service_carries_its_specialist_name_everywhere(client, make_user, headers_for):
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    h, sid, owner = a["headers"], a["service"]["id"], a["owner"]
    expected = {"id": owner.id, "full_name": owner.full_name}

    assert a["service"]["specialist"] == expected  # create
    assert client.get(f"/services/{sid}", headers=h).json()["specialist"] == expected
    listed = [s for s in client.get("/services/", headers=h).json()["items"] if s["id"] == sid][0]
    assert listed["specialist"] == expected
    assert client.put(f"/services/{sid}", json={"price": 1}, headers=h).json()["specialist"] == expected


def test_specialist_of_an_employees_service_is_the_employee(client, db, make_user, headers_for):
    from app.db.models.user import User
    a = build_tenant(client, make_user, headers_for, "Alpha", "+42070011")
    emp = db.get(User, a["employee_user_id"])
    r = client.post("/services/", json={"name": "Alpha Nails", "specialist_id": emp.id}, headers=a["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["specialist"] == {"id": emp.id, "full_name": emp.full_name}
    # nothing beyond id and name in the brief object
    assert "email" not in r.json()["specialist"]
