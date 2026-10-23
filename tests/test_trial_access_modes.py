"""Turning off the pilot must never turn off paid-feature authorization."""
from datetime import timedelta

import pytest

from app.api.core.clock import utcnow
from app.api.core.config import settings
from app.api.core.subscription_access import get_subscription_access_state, enforce_business_access
from app.db.models.subscription import Subscription


@pytest.mark.parametrize('pilot', [False, True])
@pytest.mark.parametrize('expired_days', [1, 4])
def test_expired_trial_stops_mutations_in_every_mode(client, db, make_user, headers_for, monkeypatch, pilot, expired_days):
    monkeypatch.setattr(settings, 'pilot_mode_enabled', pilot)
    owner = make_user('business_owner')
    headers = headers_for(owner)
    business = client.post('/businesses/', json={'name': 'Trial boundary salon', 'business_type': 'salon'}, headers=headers)
    assert business.status_code == 200
    trial = db.query(Subscription).filter_by(user_id=owner.id, is_trial=True).one()
    trial.trial_end = utcnow() - timedelta(days=expired_days)
    trial.current_period_end = trial.trial_end
    original_end = trial.trial_end
    db.commit()
    status = client.get('/subscriptions/trial/status', headers=headers)
    assert status.status_code == 200 and status.json()['has_access'] is False
    response = client.post('/clients/', json={'full_name': 'Must not be created', 'contact_email': 'expired@example.com'}, headers=headers)
    assert response.status_code == 402, response.text
    assert client.get('/users/me', headers=headers).status_code == 200
    reads = client.get('/clients/', headers=headers)
    assert reads.status_code == (200 if expired_days == 1 else 402)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as denied:
        enforce_business_access(db, business.json()['id'])
    assert denied.value.status_code == 402
    db.refresh(trial)
    assert trial.trial_end == original_end


def test_nonpilot_legacy_paid_plan_keeps_monthly_assistant_access(db, make_user, monkeypatch):
    monkeypatch.setattr(settings, 'pilot_mode_enabled', False)
    owner = make_user()
    trial = db.query(Subscription).filter_by(user_id=owner.id, is_trial=True).one()
    trial.is_trial = False
    trial.trial_end = None
    trial.current_period_end = utcnow() + timedelta(days=20)
    db.commit()
    access = get_subscription_access_state(db, owner.id)
    assert access['crm_mode'] == 'full' and access['can_use_ai'] is True


@pytest.mark.parametrize('pilot', [False, True])
@pytest.mark.parametrize('expired_days', [1, 4])
def test_expired_trial_can_revoke_every_session(client, db, make_user, headers_for, monkeypatch, pilot, expired_days):
    monkeypatch.setattr(settings, 'pilot_mode_enabled', pilot)
    owner = make_user('business_owner')
    headers = headers_for(owner)
    trial = db.query(Subscription).filter_by(user_id=owner.id, is_trial=True).one()
    trial.trial_end = utcnow() - timedelta(days=expired_days)
    trial.current_period_end = trial.trial_end
    db.commit()
    response = client.post('/users/logout-all', headers=headers)
    assert response.status_code == 200, response.text
    assert client.get('/users/me', headers=headers).status_code == 401
