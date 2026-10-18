"""GET /users/me tells the frontend which business the user works in and who employs them."""


def _me(client, headers):
    r = client.get("/users/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _owner_with_business(client, make_user, headers_for, name="Alpha"):
    owner = make_user("business_owner", full_name=f"{name} Owner")
    h = headers_for(owner)
    biz = client.post("/businesses/", json={"name": f"{name} Salon", "business_type": "salon"}, headers=h).json()
    return owner, h, biz["id"]


def _hire_new(client, owner_h, email):
    r = client.post("/employees/create-with-user", json={"full_name": "Staff", "email": email}, headers=owner_h)
    assert r.status_code == 200, r.text
    return r.json()


def test_solo_specialist_has_no_business_and_no_employer(client, make_user, headers_for):
    me = _me(client, headers_for(make_user("specialist")))
    assert me["business_id"] is None
    assert me["employer_id"] is None


def test_owner_without_business_yet(client, make_user, headers_for):
    me = _me(client, headers_for(make_user("business_owner")))
    assert me["business_id"] is None
    assert me["employer_id"] is None


def test_owner_sees_own_business_and_no_employer(client, make_user, headers_for):
    owner, h, biz = _owner_with_business(client, make_user, headers_for)
    me = _me(client, h)
    assert me["business_id"] == biz
    assert me["employer_id"] is None


def test_admin_sees_own_business(client, make_user, headers_for, db):
    _owner, _h, biz = _owner_with_business(client, make_user, headers_for)
    admin = make_user("admin")
    admin.business_id = biz
    db.commit()
    me = _me(client, headers_for(admin))
    assert me["business_id"] == biz
    assert me["employer_id"] is None


def test_staff_made_with_user_sees_owner_and_business(client, make_user, headers_for, db):
    from app.db.models.user import User
    owner, h, biz = _owner_with_business(client, make_user, headers_for)
    emp = _hire_new(client, h, "staff@example.com")
    staff = db.get(User, emp["employee_user_id"])
    me = _me(client, headers_for(staff))
    assert me["business_id"] == biz
    assert me["employer_id"] == owner.id


def test_hired_specialist_from_the_business_sees_owner(client, make_user, headers_for):
    owner, h, biz = _owner_with_business(client, make_user, headers_for)
    spec = make_user("specialist")
    from app.db.session import SessionLocal
    from app.db.models.user import User
    with SessionLocal() as s:
        s.get(User, spec.id).business_id = biz
        s.commit()
    r = client.post("/employees/", json={"employee_user_id": spec.id}, headers=h)
    assert r.status_code == 200, r.text
    me = _me(client, headers_for(spec))
    assert me["business_id"] == biz
    assert me["employer_id"] == owner.id


def test_staff_hired_before_the_business_existed_still_gets_it(client, make_user, headers_for, db):
    # hired first, salon created later - the user row has no business_id, the job does
    from app.db.models.user import User
    owner = make_user("business_owner")
    h = headers_for(owner)
    emp = _hire_new(client, h, "early@example.com")
    biz = client.post("/businesses/", json={"name": "Late Salon", "business_type": "salon"}, headers=h).json()["id"]
    staff = db.get(User, emp["employee_user_id"])
    me = _me(client, headers_for(staff))
    assert me["business_id"] == biz
    assert me["employer_id"] == owner.id


def test_fired_staff_loses_both(client, make_user, headers_for, db):
    from app.db.models.user import User
    _owner, h, _biz = _owner_with_business(client, make_user, headers_for)
    emp = _hire_new(client, h, "gone@example.com")
    assert client.delete(f"/employees/{emp['id']}", headers=h).status_code == 200
    staff = db.get(User, emp["employee_user_id"])
    me = _me(client, headers_for(staff))
    assert me["business_id"] is None
    assert me["employer_id"] is None


def test_user_list_carries_the_same_fields(client, make_user, headers_for):
    owner, h, biz = _owner_with_business(client, make_user, headers_for)
    items = client.get("/users/", headers=h).json()["items"]
    mine = next(u for u in items if u["id"] == owner.id)
    assert mine["business_id"] == biz
    assert mine["employer_id"] is None
