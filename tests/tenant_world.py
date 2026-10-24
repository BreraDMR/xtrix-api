"""Two independent tenants built through the public API, for isolation tests.

Everything that belongs to tenant A carries the MARKER in a text field, so a leak to
tenant B shows up as the marker appearing anywhere in a B response.
"""
from datetime import datetime, timedelta

MARKER = "Alpha"


def _ok(r, what):
    assert r.status_code in (200, 201), f"{what}: {r.status_code} {r.text[:300]}"
    return r.json()


def build_tenant(client, make_user, headers_for, name, phone_prefix):
    owner = make_user("business_owner", full_name=f"{name} Owner", email=f"{name.lower()}.owner@example.com")
    h = headers_for(owner)
    t = {"owner": owner, "headers": h}
    t["business"] = _ok(client.post("/businesses/", json={"name": f"{name} Salon", "business_type": "salon"}, headers=h), "business")
    biz = t["business"]["id"]

    emp = _ok(client.post("/employees/create-with-user", json={
        "full_name": f"{name} Employee", "email": f"{name.lower()}.emp@example.com",
        "phone": f"{phone_prefix}100", "position": f"{name} masseur", "base_salary": 1000,
    }, headers=h), "employee")
    t["employee"] = emp.get("employee", emp)
    t["employee_user_id"] = t["employee"].get("employee_user_id") or t["employee"].get("user_id")

    t["client"] = _ok(client.post("/clients/", json={
        "full_name": f"{name} Klientka", "contact_phone": f"{phone_prefix}200",
        "contact_email": f"{name.lower()}.client@example.com",
    }, headers=h), "client")
    t["service"] = _ok(client.post("/services/", json={
        "name": f"{name} Massage", "price": 500, "duration_minutes": 60, "specialist_id": owner.id,
    }, headers=h), "service")
    start = (datetime.utcnow() + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    t["visit"] = _ok(client.post("/visits/", json={
        "client_id": t["client"]["id"], "specialist_id": owner.id, "service_id": t["service"]["id"],
        "date_start": start.isoformat(), "date_end": (start + timedelta(hours=1)).isoformat(),
        "price": 500, "note": f"{name} visit note",
    }, headers=h), "visit")
    t["product"] = _ok(client.post("/products/", json={
        "name": f"{name} Oil", "business_id": biz, "quantity": 10, "price_per_unit": 5,
    }, headers=h), "product")
    t["category"] = _ok(client.post("/expenses/categories", json={"name": f"{name} Rent", "business_id": biz}, headers=h), "category")
    t["expense"] = _ok(client.post("/expenses/", json={
        "amount": 123, "expense_date": datetime.utcnow().isoformat(), "business_id": biz,
        "category_id": t["category"]["id"], "description": f"{name} expense",
    }, headers=h), "expense")
    # schedules are specialist-only and self-only, so they hang off a separate solo specialist
    spec = make_user("specialist", full_name=f"{name} Solo", email=f"{name.lower()}.solo@example.com")
    sh = headers_for(spec)
    t["specialist"], t["specialist_headers"] = spec, sh
    t["work"] = _ok(client.post("/schedules/work", json={
        "weekday": 1, "start_time": "09:00", "end_time": "17:00", "specialist_id": spec.id,
    }, headers=sh), "work schedule")
    t["block"] = _ok(client.post("/schedules/blocks", json={
        "start": (start + timedelta(days=1)).isoformat(), "end": (start + timedelta(days=1, hours=1)).isoformat(),
        "specialist_id": spec.id, "reason": f"{name} block",
    }, headers=sh), "block")
    t["program"] = _ok(client.post("/loyalty/program", json={
        "program_type": "points", "business_id": biz, "name": f"{name} Points",
    }, headers=h), "loyalty program")
    t["level"] = _ok(client.post("/loyalty/levels", json={
        "name": f"{name} Gold", "level_number": 1, "min_spending": 0, "program_id": t["program"]["id"],
    }, headers=h), "loyalty level")
    today = datetime.utcnow().date()
    t["promotion"] = _ok(client.post("/loyalty/promotions", json={
        "name": f"{name} Promo", "promotion_type": "bonus_points", "program_id": t["program"]["id"],
        "start_date": today.isoformat(), "end_date": (today + timedelta(days=30)).isoformat(),
    }, headers=h), "promotion")
    t["usage"] = _ok(client.post("/products/usage", json={
        "product_id": t["product"]["id"], "quantity": 1, "business_id": biz,
        "client_id": t["client"]["id"], "notes": f"{name} usage",
    }, headers=h), "product usage")
    t["salary"] = _ok(client.post(f"/employees/{t['employee']['id']}/salary/calculate", json={
        "period_start": (today - timedelta(days=30)).isoformat(), "period_end": today.isoformat(),
    }, headers=h), "salary")
    sal = f"/employees/{t['employee']['id']}/salary/{t['salary']['id']}"
    t["adjustment"] = _ok(client.post(f"{sal}/adjustments", json={
        "type": "bonus", "amount": 50, "description": f"{name} bonus",
    }, headers=h), "salary adjustment")
    t["payment"] = _ok(client.post(f"{sal}/payments", json={
        "amount": 100, "payment_date": datetime.utcnow().isoformat(), "payment_method": "cash", "notes": f"{name} payment",
    }, headers=h), "salary payment")
    return t


def configure_owner_schedule(db, tenant):
    """Slot-search fixtures need explicit hours; an absent schedule is closed."""
    from datetime import time
    from app.db.models.business import Business
    from app.db.models.work_schedule import WorkSchedule
    business = db.get(Business, tenant['business']['id'])
    business.timezone = 'UTC'
    business.booking_horizon_days = 365
    db.add_all([WorkSchedule(specialist_id=tenant['owner'].id, weekday=day,
                            start_time=time(9), end_time=time(18)) for day in range(7)])
    db.commit()
