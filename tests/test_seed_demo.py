"""scripts/seed_demo.py: runs on a migrated empty database, twice without duplicating anything,
and every account it prints can log in through the API."""
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func

from app.db.models.client import Client
from app.db.models.employee import Employee
from app.db.models.user import User
from app.db.models.visit import Visit

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo.py"


def _run():
    r = subprocess.run([sys.executable, str(SCRIPT)], env=dict(os.environ), capture_output=True, text=True,
                       timeout=120, cwd=SCRIPT.parent.parent)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def _counts(db):
    db.expire_all()
    return {m.__name__: db.query(func.count(m.id)).scalar() for m in (User, Employee, Client, Visit)}


def test_seed_is_idempotent_and_every_account_logs_in(client, db):
    first = _run()
    counts = _counts(db)
    second = _run()
    assert second == first
    assert _counts(db) == counts

    users = first["users"]
    assert set(users) == {"admin", "owner_a", "employee_a", "employee_a_noperm", "owner_b", "solo"}
    for key, u in users.items():
        r = client.post("/users/login", json={"email": u["email"], "password": u["password"]})
        assert r.status_code == 200, f"{key}: {r.text}"
        token = r.json()["access_token"]
        me = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200 and me.json()["id"] == u["id"], key

    a = first["businesses"]["a"]
    assert a["currency"] == "EUR" and first["businesses"]["b"]["id"] != a["id"]
    assert users["employee_a"]["business_id"] == a["id"] == users["employee_a_noperm"]["business_id"]
    assert users["solo"]["business_id"] is None
    assert first["subscription_a"]["status"] == "active"
    assert len(first["visits"]) >= 4 and first["products"] and first["expenses"]


def test_seeded_permissions_and_language_hold(client, db):
    seeded = _run()
    users = seeded["users"]

    def headers(key):
        u = users[key]
        token = client.post("/users/login", json={"email": u["email"], "password": u["password"]}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    assert client.get("/user-preferences/me", headers=headers("owner_a")).json()["language"] == "en"
    # the employee without permissions can't see money, the one with all of them can
    assert client.get("/expenses/", headers=headers("employee_a_noperm")).status_code == 403
    assert client.get("/expenses/", headers=headers("employee_a")).status_code == 200
