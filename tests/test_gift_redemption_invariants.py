"""Certificate payments obey visit permissions and the unpaid remainder."""
import pytest
from tests import test_gifts
from tests.test_gifts import _pay, _new_visit, _ok

gift_world = test_gifts.gift_world
from app.db.models.employee import Employee
from app.db.models.user import User
from app.db.models.gift import GiftLedgerEntry




@pytest.mark.parametrize("kind", ["money", "package"])
@pytest.mark.parametrize("another_certificate", [False, True])
def test_gift_cannot_pay_a_visit_twice(client, db, gift_world, kind, another_certificate):
    world = gift_world
    offer_id = world["money_offer" if kind == "money" else "package_offer"]["id"]
    _, first = _pay(client, world, offer_id=offer_id, request_key="paid-first")
    code = first["certificate"]["code"]
    second_code = code
    if another_certificate:
        _, second = _pay(client, world, offer_id=offer_id, request_key="paid-second")
        second_code = second["certificate"]["code"]
    visit = _new_visit(client, world, price=30)
    url = "/businesses/me/gift-certificates/redeem"
    payload = {"code": code, "visit_id": visit["id"], "request_key": "redeem-first"}
    if kind == "money": payload["amount"] = 20
    _ok(client.post(url, json=payload, headers=world["headers"]), "first redemption")
    response = client.post(url, json={**payload, "code": second_code, "request_key": "redeem-second"},
                           headers=world["headers"])
    assert response.status_code == 409, response.text
    assert db.query(GiftLedgerEntry).filter_by(visit_id=visit["id"], entry_type="redeemed").count() == 1


def test_gift_redemption_obeys_the_same_visit_scope_as_crm(client, db, gift_world, headers_for):
    world = gift_world
    _, paid = _pay(client, world)
    other = _ok(client.post("/clients/", json={"full_name": "Only assigned client", "contact_phone": "+420778800321"},
                            headers=world["headers"]), "assigned client")
    employee = db.get(Employee, world["employee"]["id"])
    employee.permissions = {"can_create_visits": True, "assigned_client_ids": [other["id"]]}
    db.commit()
    staff = db.get(User, world["employee_user_id"])
    headers = headers_for(staff)
    visit_id = world["visit"]["id"]
    assert client.get(f"/visits/{visit_id}", headers=headers).status_code == 403
    before = db.query(GiftLedgerEntry).count()
    response = client.post("/businesses/me/gift-certificates/redeem", headers=headers, json={
        "code": paid["certificate"]["code"], "visit_id": visit_id, "amount": 10, "request_key": "forbidden-redeem",
    })
    assert response.status_code in (403, 404), response.text
    assert db.query(GiftLedgerEntry).count() == before



def test_partial_payment_and_reversal_release_only_the_unpaid_visit_balance(client, db, gift_world):
    world = gift_world
    _, paid = _pay(client, world)
    code = paid["certificate"]["code"]
    visit = _new_visit(client, world, price=30)
    url = "/businesses/me/gift-certificates/redeem"
    base = {"code": code, "visit_id": visit["id"], "amount": 20, "request_key": "first-part"}
    first = _ok(client.post(url, json=base, headers=world["headers"]), "first part")
    _ok(client.post(url, json={**base, "amount": 10, "request_key": "last-part"}, headers=world["headers"]), "last part")
    _ok(client.post(f"/businesses/me/gift-certificates/{paid['certificate']['id']}/reversals",
        json={"redemption_entry_id": first["entry_id"], "request_key": "undo-first"},
        headers=world["headers"]), "reverse first part")
    result = _ok(client.post(url, json={**base, "request_key": "pay-again"}, headers=world["headers"]), "pay remaining again")
    assert result["applied_amount"] == 20


def test_two_certificates_cannot_concurrently_overpay_one_visit(client, db, gift_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    world = gift_world
    codes = [_pay(client, world, request_key=f"parallel-purchase-{i}")[1]["certificate"]["code"] for i in range(2)]
    visit = _new_visit(client, world, price=30)
    gate = Barrier(2)
    db.rollback()

    def pay(index):
        gate.wait(timeout=10)
        return client.post("/businesses/me/gift-certificates/redeem", headers=world["headers"], json={
            "code": codes[index], "visit_id": visit["id"], "amount": 20,
            "request_key": f"parallel-redemption-{index}",
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(pay, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    db.expire_all()
    entries = db.query(GiftLedgerEntry).filter_by(visit_id=visit["id"], entry_type="redeemed").all()
    assert sum(-float(entry.amount_delta) for entry in entries) == 20


def test_automatic_remainder_replays_after_other_certificate_partial_payment(client, db, gift_world):
    world = gift_world
    first = _pay(client, world, request_key="partial-certificate")[1]["certificate"]["code"]
    second = _pay(client, world, request_key="remainder-certificate")[1]["certificate"]["code"]
    visit = _new_visit(client, world, price=30)
    url = "/businesses/me/gift-certificates/redeem"
    _ok(client.post(url, headers=world["headers"], json={"code": first, "visit_id": visit["id"],
        "amount": 20, "request_key": "initial-partial"}), "partial")
    payload = {"code": second, "visit_id": visit["id"], "request_key": "automatic-remainder"}
    paid = _ok(client.post(url, headers=world["headers"], json=payload), "automatic remainder")
    assert paid["applied_amount"] == 10
    replay = _ok(client.post(url, headers=world["headers"], json=payload), "replay automatic remainder")
    assert replay["entry_id"] == paid["entry_id"]
    assert replay["applied_amount"] == 10
