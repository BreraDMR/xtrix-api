"""The "N no-shows within M months" rule, client counters and the online booking block."""
from datetime import timedelta

from app.api.core.clock import utcnow
from app.db.models.business import Business
from app.db.models.business_audit_event import BusinessAuditEvent
from app.db.models.client_business_relation import ClientBusinessRelation
from app.db.models.visit import Visit
from tests.test_public_booking_acceptance import _body, _book, _local_utc, _ok, _salon


def _visit(client, salon, client_id, hours_from_now, **extra):
    start = (utcnow() + timedelta(hours=hours_from_now)).replace(second=0, microsecond=0)
    return _ok(client.post("/visits/", json={
        "client_id": client_id, "specialist_id": salon["owner"].id, "service_id": salon["service"]["id"],
        "date_start": start.isoformat(), "status": "planned", **extra,
    }, headers=salon["headers"]), "visit")


def _no_show(client, salon, visit_id):
    return _ok(client.put(f"/visits/{visit_id}", json={"status": "no_show"}, headers=salon["headers"]), "no-show")


def _rule(client, salon, **settings):
    return _ok(client.put(f"/businesses/{salon['business']['id']}", json=settings, headers=salon["headers"]), "rule")


def _relation(db, salon, client_id):
    db.expire_all()
    return db.query(ClientBusinessRelation).filter(
        ClientBusinessRelation.business_id == salon["business"]["id"],
        ClientBusinessRelation.client_id == client_id).one()


def _guest(client, salon):
    return _ok(client.post("/clients/", json={
        "full_name": "Guest Example", "contact_email": "guest@example.com",
    }, headers=salon["headers"]), "client")


def test_rule_is_off_by_default_and_settings_round_trip(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Defaults")
    business = salon["business"]
    assert business["noshow_rule_enabled"] is False and business["noshow_rule_block_online"] is False
    assert (business["noshow_rule_threshold"], business["noshow_rule_period_months"]) == (3, 6)
    assert business["late_cancel_window_hours"] == 24 and business["noshow_rule_tag"] == "no-show"
    guest = _guest(client, salon)
    for hours in (-30, -20, -10):
        _no_show(client, salon, _visit(client, salon, guest["id"], hours)["id"])
    relation = _relation(db, salon, guest["id"])
    assert not relation.tags and relation.online_booking_blocked_at is None

    saved = _rule(client, salon, noshow_rule_enabled=True, noshow_rule_threshold=2, noshow_rule_period_months=3,
                  noshow_rule_tag="  late   guest ", noshow_rule_block_online=True, late_cancel_window_hours=48)
    assert saved["noshow_rule_tag"] == "late guest" and saved["late_cancel_window_hours"] == 48
    for bad in ({"noshow_rule_threshold": 0}, {"noshow_rule_period_months": 40}, {"noshow_rule_tag": "<b>x</b>"},
                {"noshow_rule_enabled": None}, {"late_cancel_window_hours": -1}):
        response = client.put(f"/businesses/{salon['business']['id']}", json=bad, headers=salon["headers"])
        assert response.status_code == 422, (bad, response.text)


def test_reaching_the_rule_tags_and_blocks_and_undo_lifts_the_block(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Strict")
    _rule(client, salon, noshow_rule_enabled=True, noshow_rule_threshold=2, noshow_rule_block_online=True)
    guest = _guest(client, salon)
    first = _visit(client, salon, guest["id"], -30)
    second = _visit(client, salon, guest["id"], -5)
    _no_show(client, salon, first["id"])
    assert _relation(db, salon, guest["id"]).online_booking_blocked_at is None
    _no_show(client, salon, second["id"])
    relation = _relation(db, salon, guest["id"])
    assert relation.tags == ["no-show"] and relation.online_booking_blocked_at is not None
    applied = db.query(BusinessAuditEvent).filter(BusinessAuditEvent.action == "client.no_show_rule_applied").one()
    assert applied.entity_id == guest["id"]
    assert applied.changes == {"no_show_count": 2, "tag_added": True, "online_booking_blocked": True}

    # online booking now refuses politely, in the guest's language
    start, _day = _local_utc()
    response = client.post(f"/public/booking/{salon['slug']}/bookings",
                           json=_body(salon, start, "blocked-guest"), headers={"Accept-Language": "ru"})
    assert response.status_code == 403, response.text
    assert response.json()["error"]["detail"]["code"] == "public_booking_client_blocked"
    assert "свяжитесь с салоном" in response.json()["error"]["message"]
    db.expire_all()
    assert db.query(Visit).filter(Visit.client_id == guest["id"], Visit.status == "planned").count() == 0
    # staff can still book them by hand
    _visit(client, salon, guest["id"], 48)

    _ok(client.put(f"/visits/{second['id']}", json={"status": "completed", "payment_method": "cash"},
                   headers=salon["headers"]), "undo")
    relation = _relation(db, salon, guest["id"])
    assert relation.online_booking_blocked_at is None
    assert relation.tags == ["no-show"]
    assert db.query(BusinessAuditEvent).filter(
        BusinessAuditEvent.action == "client.online_booking_unblocked").count() == 1
    _ok(_book(client, salon, _body(salon, start, "unblocked-guest")), "booking after unblock")


def test_only_no_shows_inside_the_period_count_and_tags_stay_unique(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Period")
    _rule(client, salon, noshow_rule_enabled=True, noshow_rule_threshold=2, noshow_rule_period_months=1)
    guest = _guest(client, salon)
    _ok(client.put(f"/clients/{guest['id']}/profile", json={"tags": ["VIP", "No-Show"]},
                   headers=salon["headers"]), "tags")
    old = _visit(client, salon, guest["id"], -24 * 45)
    recent = _visit(client, salon, guest["id"], -3)
    _no_show(client, salon, old["id"])
    _no_show(client, salon, recent["id"])
    relation = _relation(db, salon, guest["id"])
    assert relation.tags == ["VIP", "No-Show"]
    assert relation.online_booking_blocked_at is None  # block option is off
    third = _visit(client, salon, guest["id"], -1)
    _no_show(client, salon, third["id"])
    assert _relation(db, salon, guest["id"]).tags == ["VIP", "No-Show"]
    overview = _ok(client.get(f"/clients/{guest['id']}/overview", headers=salon["headers"]), "overview")
    assert overview["attendance"]["rule_reached"] is True
    assert overview["attendance"]["online_booking_blocked"] is False
    assert overview["attendance"]["warning"]


def test_staff_block_and_unblock_from_the_card_and_counters_everywhere(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Counters")
    guest = _guest(client, salon)
    other = _ok(client.post("/clients/", json={"full_name": "Reliable One"}, headers=salon["headers"]), "other")
    _no_show(client, salon, _visit(client, salon, guest["id"], -2)["id"])
    late = _visit(client, salon, other["id"], 2)
    assert _ok(client.put(f"/visits/{late['id']}", json={"status": "cancelled"},
                          headers=salon["headers"]), "cancel")["late_cancel"] is True

    listed = _ok(client.get("/clients/", headers=salon["headers"]), "list")
    by_id = {row["id"]: row["attendance"] for row in listed["items"]}
    assert by_id[guest["id"]]["no_show_count"] == 1 and by_id[guest["id"]]["late_cancel_count"] == 0
    assert by_id[other["id"]]["late_cancel_count"] == 1
    for kind, expected in (("no_show", [guest["id"]]), ("late_cancel", [other["id"]]), ("online_booking_blocked", [])):
        filtered = _ok(client.get("/clients/", params={"attendance": kind}, headers=salon["headers"]), kind)
        assert [row["id"] for row in filtered["items"]] == expected, kind

    profile = _ok(client.put(f"/clients/{guest['id']}/profile", json={"online_booking_blocked": True},
                             headers=salon["headers"]), "block")
    assert profile["online_booking_blocked"] is True and profile["online_booking_blocked_at"]
    filtered = _ok(client.get("/clients/", params={"attendance": "online_booking_blocked"}, headers=salon["headers"]), "f")
    assert [row["id"] for row in filtered["items"]] == [guest["id"]]
    overview = _ok(client.get(f"/clients/{guest['id']}/overview",
                              headers={**salon["headers"], "Accept-Language": "cz"}), "overview")
    assert overview["stats"]["no_show_visits"] == 1
    assert overview["attendance"]["online_booking_blocked"] is True
    assert overview["attendance"]["warning"].startswith("Tento klient")
    single = _ok(client.get(f"/clients/{guest['id']}", headers=salon["headers"]), "client")
    assert single["attendance"]["online_booking_blocked"] is True

    _ok(client.put(f"/clients/{guest['id']}/profile", json={"online_booking_blocked": False},
                   headers=salon["headers"]), "unblock")
    actions = [row.action for row in db.query(BusinessAuditEvent).filter(
        BusinessAuditEvent.entity_type == "client").order_by(BusinessAuditEvent.id)]
    assert actions[-2:] == ["client.online_booking_blocked", "client.online_booking_unblocked"]
    overview = _ok(client.get(f"/clients/{other['id']}/overview", headers=salon["headers"]), "overview")
    assert overview["stats"]["late_cancel_visits"] == 1 and overview["attendance"]["warning"] is None


def test_counters_and_block_stay_inside_their_salon(client, make_user, headers_for, db):
    home = _salon(client, make_user, headers_for, "Home")
    away = _salon(client, make_user, headers_for, "Away")
    guest = _guest(client, home)
    _ok(client.put(f"/clients/{guest['id']}/profile", json={"online_booking_blocked": True},
                   headers=home["headers"]), "block")
    _no_show(client, home, _visit(client, home, guest["id"], -2)["id"])
    start, _day = _local_utc()
    # same contact in another salon is a different card there, and it books fine
    _ok(_book(client, away, _body(away, start, "away-booking")), "booking elsewhere")
    filtered = _ok(client.get("/clients/", params={"attendance": "no_show"}, headers=away["headers"]), "away")
    assert filtered["items"] == []
    stats = _ok(client.get("/analytics/visits/statistics", headers=away["headers"]), "away stats")
    assert stats["attendance"]["no_show"] == 0


def test_analytics_attendance_block(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Numbers")
    guest = _guest(client, salon)
    _no_show(client, salon, _visit(client, salon, guest["id"], -50)["id"])
    for hours in (-40, -30, -20):
        _visit(client, salon, guest["id"], hours, status="completed", payment_method="cash")
    late = _visit(client, salon, guest["id"], 3)
    _ok(client.put(f"/visits/{late['id']}", json={"status": "cancelled"}, headers=salon["headers"]), "cancel")
    stats = _ok(client.get("/analytics/visits/statistics", headers=salon["headers"]), "stats")
    block = stats["attendance"]
    assert (block["no_show"], block["late_cancel"], block["due_visits"]) == (1, 1, 4)
    assert block["no_show_rate"] == 0.25
    assert [(row["specialist_id"], row["no_show"]) for row in block["by_specialist"]] == [(salon["owner"].id, 1)]
    assert block["by_specialist"][0]["specialist_name"] == "Numbers Owner"


def test_two_salons_can_share_a_barcode_but_not_inside_one(client, make_user, headers_for, db):
    first = _salon(client, make_user, headers_for, "Scan")
    second = _salon(client, make_user, headers_for, "Scanner")
    for salon in (first, second):
        _ok(client.post("/products/", json={"name": "Oil", "business_id": salon["business"]["id"],
                                            "barcode": "8590001112223"}, headers=salon["headers"]), "product")
    response = client.post("/products/", json={"name": "Oil 2", "business_id": first["business"]["id"],
                                               "barcode": "8590001112223"}, headers=first["headers"])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["detail"]["code"] == "product_barcode_taken"
    other = _ok(client.post("/products/", json={"name": "Cream", "business_id": first["business"]["id"],
                                                "barcode": "111"}, headers=first["headers"]), "cream")
    response = client.put(f"/products/{other['id']}", json={"barcode": "8590001112223"}, headers=first["headers"])
    assert response.status_code == 409, response.text
    # blank barcodes are "no barcode", so many products can have none
    for name in ("A", "B"):
        made = _ok(client.post("/products/", json={"name": name, "business_id": first["business"]["id"],
                                                   "barcode": "  "}, headers=first["headers"]), name)
        assert made["barcode"] is None


def test_guest_cancel_inside_a_wide_salon_window_is_late(client, make_user, headers_for, db):
    salon = _salon(client, make_user, headers_for, "Guestside")
    start, _day = _local_utc(days_ahead=5)
    booked = _ok(_book(client, salon, _body(salon, start, "guest-late")), "booking")
    early = _ok(_book(client, salon, _body(salon, _local_utc(days_ahead=5, hour=13)[0], "guest-early")), "booking")
    _ok(client.post("/public/booking-management/cancel", headers={"X-Booking-Token": early["manage_token"]}), "early")
    _rule(client, salon, late_cancel_window_hours=168)
    _ok(client.post("/public/booking-management/cancel", headers={"X-Booking-Token": booked["manage_token"]}), "late")
    db.expire_all()
    assert db.get(Visit, early["id"]).late_cancel is False
    assert db.get(Visit, booked["id"]).late_cancel is True
