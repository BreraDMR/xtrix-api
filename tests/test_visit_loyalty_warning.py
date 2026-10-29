"""A saved visit remains successful while an unconfirmed bonus is explained."""
import pytest

from app.db.models.business import Business
from app.db.models.loyalty_program import LoyaltyTransaction
from app.db.models.visit import Visit
from tests import test_completed_payment_guard as base

salon=base.salon


@pytest.mark.parametrize('locale',['en','ru','cz'])
def test_old_currency_completion_keeps_quote_and_returns_a_human_bonus_warning(client,db,salon,locale):
    from app.api.core.i18n import _
    visit_id,_items=base.create_quote(client,salon)
    business=db.get(Business,salon['business']['id']);business.currency='EUR';db.commit()
    response=client.put(f'/visits/{visit_id}',headers={**salon['headers'],'Accept-Language':locale},
        json={'status':'completed','payment_method':'cash'})
    assert response.status_code==200,response.text
    assert response.json()['loyalty_warning']==_('visit_loyalty_review_required',locale)+' '+_('loyalty_points_currency_unavailable',locale)
    db.expire_all();visit=db.get(Visit,visit_id)
    assert visit.status=='completed' and visit.currency_snapshot=='CZK'
    assert visit.manual_payment_recorded_at is not None
    assert db.query(LoyaltyTransaction).filter_by(visit_id=visit_id).count()==0


def test_unexpected_bonus_failure_is_not_reported_as_failed_visit_or_raw_error(client,db,salon,monkeypatch):
    from app.api.core import loyalty_logic
    from app.api.core.i18n import _
    visit_id,_items=base.create_quote(client,salon)
    def fail(*args):raise RuntimeError('secret provider diagnostic must stay private')
    monkeypatch.setattr(loyalty_logic,'award_bonus_for_visit',fail)
    response=client.put(f'/visits/{visit_id}',headers={**salon['headers'],'Accept-Language':'ru'},
        json={'status':'completed','payment_method':'cash'})
    assert response.status_code==200,response.text
    assert response.json()['loyalty_warning']==_('visit_loyalty_review_required','ru')
    assert 'secret provider' not in response.text
    db.expire_all();assert db.get(Visit,visit_id).status=='completed'


def test_successful_bonus_has_no_warning(client,db,salon):
    visit_id,_items=base.create_quote(client,salon)
    response=client.put(f'/visits/{visit_id}',headers=salon['headers'],json={'status':'completed','payment_method':'cash'})
    assert response.status_code==200,response.text
    assert response.json()['loyalty_warning'] is None
    assert db.query(LoyaltyTransaction).filter_by(visit_id=visit_id).count()==1
