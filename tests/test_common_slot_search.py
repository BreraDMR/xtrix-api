import json
import pytest

from datetime import datetime
@pytest.mark.parametrize("chain", [False, True])
def test_admin_and_ai_slot_search_respect_service_buffers(client,db,make_user,headers_for,monkeypatch,chain):
    from app.ai.executor import execute_plan
    from app.ai.plan import AssistantPlan
    import app.api.core.slots as legacy
    import app.api.core.availability as availability
    import app.api.v1.endpoints.public_booking as endpoint
    import app.api.v1.endpoints.visits as visits_endpoint
    now=datetime(2026,10,26,9)
    for module in (legacy,availability,endpoint,visits_endpoint):
        monkeypatch.setattr(module,'utcnow',lambda:now)
    owner=make_user('business_owner')
    h=headers_for(owner)
    b=client.post('/businesses/',json={'name':'Buffer Review','business_type':'salon','timezone':'UTC'},headers=h)
    assert b.status_code==200,b.text
    settings=client.put('/businesses/me/public-booking',json={'is_enabled':True},headers=h)
    assert settings.status_code==200,settings.text
    slug=settings.json()['slug']
    s=client.post('/services/',json={'name':'Buffered Service','duration_minutes':60,'buffer_after_minutes':30,'price':20,'specialist_id':owner.id},headers=h)
    assert s.status_code==200,s.text
    s=s.json()
    w=client.post('/schedules/work',json={'weekday':0,'start_time':'09:00','end_time':'17:00','specialist_id':owner.id},headers=h)
    assert w.status_code==200,w.text
    guest=client.post('/clients/',json={'full_name':'Buffer Guest','contact_email':'buffer@example.com'},headers=h)
    assert guest.status_code==200,guest.text
    visit_body={'client_id':guest.json()['id'],'specialist_id':owner.id,'service_id':s['id'],'date_start':'2026-10-26T09:00:00Z','date_end':'2026-10-26T10:00:00Z'}
    if chain:
        employee=client.post('/employees/create-with-user',json={'full_name':'Other Chain Master','email':'chain.staff@example.com'},headers=h)
        assert employee.status_code==200,employee.text
        visit_body['items']=[{'service_id':s['id'],'specialist_id':owner.id},
                             {'service_id':s['id'],'specialist_id':employee.json()['employee_user_id']}]
    v=client.post('/visits/',json=visit_body,headers=h)
    assert v.status_code==200,v.text
    public=client.get(f'/public/booking/{slug}/slots',params={'service_id':s['id'],'staff_id':owner.id,'date_from':'2026-10-26'})
    assert public.status_code==200,public.text
    first=public.json()['days'][0]['slots'][0]['start']
    admin=client.get('/visits/next_slot',params={'specialist_id':owner.id,'service_id':s['id']},headers=h)
    db.expire_all()
    from app.api.crud.crud_user import get_active_user
    owner=get_active_user(db,owner.id)
    result=execute_plan(db,owner,AssistantPlan.model_validate({'summary':'probe','questions':[],'actions':[{'action':'find_free_slot','service_id':s['id']}]}),tz='UTC',now=now)
    print('BUFFER_SLOT_SPLIT',json.dumps({'public_first':first,'admin_status':admin.status_code,'admin':admin.json(),'ai':result.to_dict()}))
    assert first=='2026-10-26T10:30:00Z'
    assert admin.status_code==200
    assert admin.json()['next_slot']==first
    assert result.results[0].data['start']==first


def test_internal_search_without_a_schedule_does_not_invent_working_hours(client, make_user, headers_for):
    specialist = make_user('specialist')
    response = client.get('/visits/next_slot', params={'specialist_id': specialist.id}, headers=headers_for(specialist))
    assert response.status_code == 404, response.text


def test_internal_search_refuses_another_business_service(client, make_user, headers_for):
    from tests.tenant_world import build_tenant
    own = build_tenant(client, make_user, headers_for, 'OwnSlots', '+42070091')
    other = build_tenant(client, make_user, headers_for, 'OtherSlots', '+42070092')
    response = client.get('/visits/next_slot', params={'specialist_id': own['owner'].id,
        'service_id': other['service']['id']}, headers=own['headers'])
    assert response.status_code == 403, response.text
