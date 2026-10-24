from concurrent.futures import ThreadPoolExecutor
from threading import Event
from app.api.core import campaign_service
from app.db.models.campaign import Campaign
from tests import test_campaigns as campaigns
pytest_plugins = ["tests.test_campaigns"]

def test_campaign_concurrent_confirm_then_edit_requires_new_confirmation(client, db, world, monkeypatch):
    from concurrent.futures import TimeoutError
    row = campaigns._add_client(client, world, 'Consent User', 'consent@example.com')
    campaigns._age_relation(db, world['business']['id'], row['id'], 120)
    campaigns._consent(db, world, row)
    campaign = campaigns._create(client, world, body='Approved old body')
    preview = campaigns._preview(client, world, campaign['id'])
    original_segment = campaign_service.segment_recipients
    entered = Event()
    release = Event()
    edit_started = Event()
    def segment_with_gate(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original_segment(*args, **kwargs)
    monkeypatch.setattr(campaign_service, 'segment_recipients', segment_with_gate)
    def edit():
        edit_started.set()
        return client.patch(f"/businesses/me/campaigns/{campaign['id']}", headers=world['headers'], json={'body':'Unapproved new body'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmation = pool.submit(campaigns._confirm, client, world, campaign['id'], preview)
        assert entered.wait(10)
        update = pool.submit(edit)
        assert edit_started.wait(10)
        try:
            # An unlocked implementation finishes the edit while confirmation is paused.
            # A row-locked implementation waits; release it before joining either future.
            update.result(timeout=0.5)
        except TimeoutError:
            pass
        finally:
            release.set()
        confirmed = confirmation.result(10)
        updated = update.result(10)
    assert confirmed.status_code in (200,409), confirmed.text
    assert updated.status_code == 200, updated.text
    db.expire_all()
    saved = db.get(Campaign,campaign['id'])
    send = client.post(f"/businesses/me/campaigns/{campaign['id']}/send", headers=world['headers'])
    print({'confirmed':confirmed.status_code,'edit':updated.status_code,'final_status':saved.status,'body':saved.body,'send':send.status_code})
    assert saved.body == 'Unapproved new body'
    assert saved.status == 'draft' and send.status_code == 409


def test_stale_campaign_object_is_refreshed_before_confirm(client, db, world):
    import pytest
    from fastapi import HTTPException
    from app.db.session import SessionLocal
    row = campaigns._add_client(client, world, 'Consent User', 'consent@example.com')
    campaigns._age_relation(db, world['business']['id'], row['id'], 120)
    campaigns._consent(db, world, row)
    campaign = campaigns._create(client, world, body='Approved old body')
    preview = campaigns._preview(client, world, campaign['id'])
    with SessionLocal() as stale_session:
        stale = stale_session.get(Campaign,campaign['id'])
        assert stale.body == 'Approved old body'
        update = client.patch(f"/businesses/me/campaigns/{campaign['id']}",headers=world['headers'],json={'body':'Unapproved new body'})
        assert update.status_code == 200, update.text
        try:
            with pytest.raises(HTTPException) as error:
                campaign_service.confirm_campaign(stale_session,stale,world['owner'],'en',seen=(preview['audience_hash'],preview['content_hash']))
            assert error.value.status_code == 409
        finally:
            stale_session.rollback()

