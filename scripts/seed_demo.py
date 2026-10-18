"""Demo data for E2E runs against an empty, migrated database.

Usage: DATABASE_URL=postgresql://... python scripts/seed_demo.py

Goes through the API in-process (same rules as the web app), except the admin and the passwords,
which are set directly. Safe to run again: things already there are found by e-mail, name or a
"demo:" note and reused, and passwords are reset to the ones below. Visits are placed around the
day of the first run (UTC) and are not moved by later runs.
Prints every login, password and id as JSON on stdout; logs go to stderr.
The passwords live in this file, so never point it at production.
"""
import contextlib
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# an in-process client counts as one IP, the limiter would only get in the way here
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

PASSWORDS = {
    "admin": "Demo!Admin-47x",
    "owner_a": "Demo!OwnerA-58x",
    "employee_a": "Demo!StaffA-69x",
    "employee_a_noperm": "Demo!Blank-71x",
    "owner_b": "Demo!OwnerB-82x",
    "solo": "Demo!Solo-93x",
}
ALL_PERMISSIONS = {"can_create_visits": True, "can_edit_clients": True,
                   "can_view_financials": True, "can_manage_services": True}
NO_PERMISSIONS = {k: False for k in ALL_PERMISSIONS}


class Seeder:
    def __init__(self, client, db):
        self.client = client
        self.db = db

    def call(self, user, method, url, **kwargs):
        from app.api.core.security import create_access_token

        headers = {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"} if user else {}
        r = self.client.request(method, url, headers=headers, **kwargs)
        if r.status_code >= 300:
            raise SystemExit(f"seed_demo: {method} {url} -> {r.status_code} {r.text[:500]}")
        return r.json()

    def _set_password(self, user, password):
        from app.api.core.security import get_password_hash

        user.hashed_password = get_password_hash(password)
        # a rerun should also undo lockouts from failed E2E logins
        user.failed_login_attempts = 0
        user.is_locked = False
        user.locked_until = None
        self.db.commit()
        self.db.refresh(user)
        return user

    def user(self, email):
        from app.db.models.user import User

        self.db.expire_all()
        return self.db.query(User).filter(User.email == email).first()

    def ensure_user(self, key, email, full_name, role, phone, **extra):
        from app.api.crud import crud_user
        from app.schemas.user import UserCreate

        password = PASSWORDS[key]
        user = self.user(email)
        if not user:
            if role == "admin":
                # signup can't make admins on purpose, same path as scripts/create_admin.py
                crud_user.create_user(self.db, UserCreate(email=email, full_name=full_name, password=password,
                                                          role=role, phone=phone))
            else:
                self.call(None, "POST", "/users/", json={"email": email, "full_name": full_name, "password": password,
                                                         "role": role, "phone": phone, **extra})
            user = self.user(email)
        return self._set_password(user, password)

    def ensure_business(self, owner, name, currency):
        if not owner.business_id:
            self.call(owner, "POST", "/businesses/", json={"name": name, "business_type": "salon", "currency": currency})
            self.db.refresh(owner)
        return self.call(owner, "GET", "/businesses/me")

    def ensure_employee(self, key, owner, email, full_name, phone, position, permissions):
        from app.db.models.employee import Employee

        if not self.user(email):
            self.call(owner, "POST", "/employees/create-with-user", json={
                "full_name": full_name, "email": email, "phone": phone, "position": position,
                "permissions": permissions, "base_salary": 1000, "commission_percentage": 10,
            })
        user = self.ensure_user(key, email, full_name, "specialist", phone)
        employee = self.db.query(Employee).filter(Employee.employee_user_id == user.id).first()
        return user, employee

    def ensure_service(self, owner, specialist_id, name, minutes, price):
        from app.db.models.service import Service

        found = self.db.query(Service).filter(Service.specialist_id == specialist_id, Service.name == name,
                                              Service.deleted_at.is_(None)).first()
        if found:
            return found.id
        return self.call(owner, "POST", "/services/", json={
            "name": name, "duration_minutes": minutes, "price": price, "specialist_id": specialist_id})["id"]

    def ensure_client(self, owner, full_name, email, phone):
        from app.db.models.client import Client

        found = self.db.query(Client).filter(Client.contact_email == email, Client.deleted_at.is_(None)).first()
        if found:
            return found.id
        return self.call(owner, "POST", "/clients/", json={
            "full_name": full_name, "contact_email": email, "contact_phone": phone})["id"]

    def ensure_visit(self, owner, marker, business_id, **body):
        from app.db.models.visit import Visit

        note = f"demo:{marker}"
        found = self.db.query(Visit).filter(Visit.business_id == business_id, Visit.note == note,
                                            Visit.deleted_at.is_(None)).first()
        if found:
            return found.id
        body["date_start"] = body["date_start"].isoformat()
        return self.call(owner, "POST", "/visits/", json={**body, "note": note})["id"]

    def ensure_product(self, owner, business_id, name, **body):
        from app.db.models.product import Product

        found = self.db.query(Product).filter(Product.business_id == business_id, Product.name == name).first()
        if found:
            return found.id
        return self.call(owner, "POST", "/products/", json={"business_id": business_id, "name": name, **body})["id"]

    def ensure_expense(self, owner, business_id, description, category_code, amount, when):
        from app.db.models.expense import Expense

        found = self.db.query(Expense).filter(Expense.business_id == business_id,
                                              Expense.description == description).first()
        if found:
            return found.id
        categories = self.call(owner, "GET", "/expenses/categories")
        category_id = next((c["id"] for c in categories if c.get("code") == category_code), None)
        return self.call(owner, "POST", "/expenses/", json={
            "business_id": business_id, "category_id": category_id, "amount": amount, "currency": "EUR",
            "description": description, "expense_date": when.isoformat(), "payment_method": "transfer"})["id"]

    def ensure_subscription(self, admin, owner):
        from app.api.core.clock import utcnow

        sub = self.call(owner, "GET", "/subscriptions/me")
        if not sub:
            plans = self.call(admin, "GET", "/subscriptions/plans")
            plan = next(p for p in plans if p["code"] == "admin_up_to_3_employees")
            self.call(admin, "POST", "/subscriptions/", json={
                "user_id": owner.id, "plan_id": plan["id"], "provider": "manual", "status": "active",
                "current_period_end": (utcnow() + timedelta(days=30)).isoformat()})
            sub = self.call(owner, "GET", "/subscriptions/me")
        return {"id": sub["id"], "status": sub["status"], "is_trial": sub["is_trial"],
                "current_period_end": sub["current_period_end"]}


def seed():
    from fastapi.testclient import TestClient

    from app.api.core.clock import utcnow
    from app.api.core.security import validate_password_strength
    from app.main import app
    from app.db.session import SessionLocal

    from app.api.core.config import settings

    if settings.environment == "production":
        raise SystemExit("seed_demo: refusing to run with ENVIRONMENT=production")
    for key, password in PASSWORDS.items():
        ok, why = validate_password_strength(password)
        assert ok, f"{key}: {why}"

    db = SessionLocal()
    try:
        s = Seeder(TestClient(app), db)
        admin = s.ensure_user("admin", "demo.admin@example.com", "Demo Admin", "admin", "+420700900001")
        owner_a = s.ensure_user("owner_a", "demo.owner.a@example.com", "Anna Owner", "business_owner", "+420700900002")
        biz_a = s.ensure_business(owner_a, "Demo Salon A", "EUR")
        s.call(owner_a, "PUT", "/user-preferences/me", json={"language": "en"})
        staff, staff_emp = s.ensure_employee("employee_a", owner_a, "demo.staff.a@example.com", "Eva Staff",
                                             "+420700900003", "Stylist", ALL_PERMISSIONS)
        noperm, noperm_emp = s.ensure_employee("employee_a_noperm", owner_a, "demo.noperm.a@example.com",
                                               "Nik Noperm", "+420700900004", "Trainee", NO_PERMISSIONS)
        owner_b = s.ensure_user("owner_b", "demo.owner.b@example.com", "Boris Owner", "business_owner", "+420700900005")
        biz_b = s.ensure_business(owner_b, "Demo Studio B", "CZK")
        solo = s.ensure_user("solo", "demo.solo@example.com", "Sara Solo", "specialist", "+420700900006",
                             specialist_type="massage")

        services = {
            "a_haircut": s.ensure_service(owner_a, owner_a.id, "Haircut", 45, 30),
            "a_brow_tint": s.ensure_service(owner_a, staff.id, "Brow tint", 10, 12),
            "b_massage": s.ensure_service(owner_b, owner_b.id, "Massage", 60, 900),
            "solo_massage": s.ensure_service(solo, solo.id, "Home massage", 60, 50),
        }
        clients = {
            "a_1": s.ensure_client(owner_a, "Clara Client", "demo.client.a1@example.com", "+420700910001"),
            "a_2": s.ensure_client(owner_a, "Dan Client", "demo.client.a2@example.com", "+420700910002"),
            "b_1": s.ensure_client(owner_b, "Bea Client", "demo.client.b1@example.com", "+420700910003"),
            "solo_1": s.ensure_client(solo, "Sol Client", "demo.client.s1@example.com", "+420700910004"),
        }
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        a = biz_a["id"]
        visits = {
            "a_yesterday_done": s.ensure_visit(owner_a, "a_yesterday_done", a, client_id=clients["a_1"],
                                               specialist_id=owner_a.id, service_id=services["a_haircut"],
                                               date_start=today - timedelta(days=1) + timedelta(hours=10),
                                               status="completed", price=30, payment_method="cash"),
            "a_today_morning": s.ensure_visit(owner_a, "a_today_morning", a, client_id=clients["a_2"],
                                              specialist_id=owner_a.id, service_id=services["a_haircut"],
                                              date_start=today + timedelta(hours=9)),
            "a_today_staff": s.ensure_visit(owner_a, "a_today_staff", a, client_id=clients["a_1"],
                                            specialist_id=staff.id, service_id=services["a_brow_tint"],
                                            date_start=today + timedelta(hours=14)),
            "a_tomorrow": s.ensure_visit(owner_a, "a_tomorrow", a, client_id=clients["a_2"],
                                         specialist_id=owner_a.id, service_id=services["a_haircut"],
                                         date_start=today + timedelta(days=1, hours=11)),
            "b_today": s.ensure_visit(owner_b, "b_today", biz_b["id"], client_id=clients["b_1"],
                                      specialist_id=owner_b.id, service_id=services["b_massage"],
                                      date_start=today + timedelta(hours=10)),
        }
        products = {
            "a_shampoo": s.ensure_product(owner_a, a, "Shampoo", unit="ml", quantity=5000, min_quantity=1000,
                                          price_per_unit=0.02),
            "a_dye": s.ensure_product(owner_a, a, "Hair dye", unit="pcs", quantity=2, min_quantity=5,
                                      price_per_unit=8),
        }
        expenses = {"a_rent": s.ensure_expense(owner_a, a, "demo: rent", "rent", 800, today)}
        subscription_a = s.ensure_subscription(admin, owner_a)

        def login(key, user, **more):
            return {"id": user.id, "email": user.email, "password": PASSWORDS[key], "role": user.role,
                    "business_id": user.business_id, **more}

        return {
            "users": {
                "admin": login("admin", admin),
                "owner_a": login("owner_a", owner_a),
                "employee_a": login("employee_a", staff, employee_id=staff_emp.id, permissions=ALL_PERMISSIONS),
                "employee_a_noperm": login("employee_a_noperm", noperm, employee_id=noperm_emp.id,
                                           permissions=NO_PERMISSIONS),
                "owner_b": login("owner_b", owner_b),
                "solo": login("solo", solo),
            },
            "businesses": {
                "a": {"id": biz_a["id"], "name": biz_a["name"], "currency": biz_a["currency"]},
                "b": {"id": biz_b["id"], "name": biz_b["name"], "currency": biz_b["currency"]},
            },
            "services": services,
            "clients": clients,
            "visits": visits,
            "products": products,
            "expenses": expenses,
            "subscription_a": subscription_a,
        }
    finally:
        db.close()


def main():
    # the app logs to stdout - keep stdout for the JSON only
    with contextlib.redirect_stdout(sys.stderr):
        result = seed()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
