"""CRM and assistant checkout share a customer, never each other's subscription gate."""
import pytest

from tests import test_stripe_checkout as checkout_tests
from app.api.core import offer_catalog, stripe_billing
from app.api.core.config import settings

checkout = checkout_tests.checkout


@pytest.mark.parametrize('mapped_count,ready', [(0, False), (1, False), (8, True)])
def test_catalog_only_advertises_checkout_when_approved_offers_are_mapped(monkeypatch, mapped_count, ready):
    monkeypatch.setattr(settings, 'pilot_mode_enabled', False)
    monkeypatch.setattr(settings, 'stripe_api_key', 'sk_test_mock')
    codes = [offer.code for offer in offer_catalog.get_approved_offers()]
    monkeypatch.setattr(offer_catalog, 'OFFER_PLAN_CODES', {code: 'local-plan' for code in codes[:mapped_count]})
    assert offer_catalog.get_offer_catalog().checkout_available is ready


def mapped_offers(monkeypatch, checkout):
    # This configures only the local Stripe fake, never production plan mappings.
    plan = checkout['plans'][0]
    for code in ['crm_starter_czk', 'ai_starter_czk']:
        monkeypatch.setitem(offer_catalog.OFFER_PLAN_CODES, code, plan.code)


def buy(client, checkout, code):
    from app.api.core.security import create_access_token
    return client.post('/subscriptions/checkout/create-session', params={'offer_code': code},
        headers={'Authorization': 'Bearer ' + create_access_token({'sub': str(checkout['user'].id)})})


@pytest.mark.parametrize('first_product', ['crm', 'ai'])
@pytest.mark.parametrize('locally_synced', [False, True])
def test_paid_product_does_not_prevent_checkout_for_the_other(client, db, checkout, monkeypatch, first_product, locally_synced):
    mapped_offers(monkeypatch, checkout)
    first = buy(client, checkout, f'{first_product}_starter_czk')
    assert first.status_code == 200, first.text
    checkout['pay'](first.json()['session_id'])
    if locally_synced:
        remote = next(iter(checkout['remote']['subscriptions'].values()))
        stripe_billing.sync_subscription(db, remote)
        db.commit()
    other = 'ai' if first_product == 'crm' else 'crm'
    second = buy(client, checkout, f'{other}_starter_czk')
    assert second.status_code == 200, second.text
    assert second.json()['session_id'] != first.json()['session_id']
    # One active subscription of the same product must still block a duplicate.
    duplicate = buy(client, checkout, f'{first_product}_starter_czk')
    assert duplicate.status_code == 409


def test_open_checkouts_for_different_products_do_not_expire_each_other(client, checkout, monkeypatch):
    mapped_offers(monkeypatch, checkout)
    crm = buy(client, checkout, 'crm_starter_czk')
    ai = buy(client, checkout, 'ai_starter_czk')
    assert crm.status_code == ai.status_code == 200
    assert checkout['remote']['expires'] == []
    repeat = buy(client, checkout, 'crm_starter_czk')
    assert repeat.status_code == 200 and repeat.json() == crm.json()
    assert len(checkout['remote']['creates']) == 2
    assert all(row['status'] == 'open' for row in checkout['remote']['sessions'].values())


def test_lost_response_for_one_product_is_recovered_before_the_other_starts(client, checkout, monkeypatch):
    mapped_offers(monkeypatch, checkout)
    checkout['remote']['lose_create'] = True
    lost = buy(client, checkout, 'ai_starter_czk')
    assert lost.status_code == 503
    next_checkout = buy(client, checkout, 'crm_starter_czk')
    assert next_checkout.status_code == 200, next_checkout.text
    assert len(checkout['remote']['creates']) == 2
    assert checkout['remote']['expires'] == []
