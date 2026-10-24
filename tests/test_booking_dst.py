from datetime import date, datetime, time, timedelta
import json
from app.api.core.availability import collect_slots, collect_chain_slots, ChainTiming


def test_fall_slots_include_both_real_occurrences():
    kw=dict(date_from=date(2026,10,25), date_to=date(2026,10,25), timezone_name='Europe/Prague', horizon_days=365,lead_time_minutes=0, master_ids=[1],schedules={1:[(6,time(1),time(4))]},exceptions={},blocks={},busy={},now=datetime(2026,10,24))
    single=collect_slots(duration_minutes=30,**kw)
    chain=collect_chain_slots(timings=[ChainTiming(30)],**kw)
    expected={datetime(2026,10,25,0,30),datetime(2026,10,25,1,30)}
    result={'single':[s.start.isoformat() for s in single], 'chain':[s.start.isoformat() for s in chain]}
    print('FALL_SLOTS',json.dumps(result))
    assert expected <= {s.start for s in single}
    assert expected <= {s.start for s in chain}


def test_spring_single_slots_fit_actual_working_window():
    kw=dict(date_from=date(2027,3,28), date_to=date(2027,3,28), timezone_name='Europe/Prague', horizon_days=365,lead_time_minutes=0, master_ids=[1],schedules={1:[(6,time(1),time(4))]},exceptions={},blocks={},busy={},now=datetime(2026,10,24))
    slots=collect_slots(duration_minutes=120,**kw)
    bad=[s.start.isoformat() for s in slots if s.start+timedelta(minutes=120)>datetime(2027,3,28,2)]
    print('SPRING_OUTSIDE_WINDOW',bad)
    assert not bad


def salon(client,make_user,headers_for,start_time,end_time,duration=30):
    owner=make_user('business_owner')
    h=headers_for(owner)
    b=client.post('/businesses/',json={'name':'DST Review','business_type':'salon','timezone':'Europe/Prague'},headers=h)
    assert b.status_code==200,b.text
    r=client.put('/businesses/me/public-booking',json={'is_enabled':True},headers=h)
    assert r.status_code==200,r.text
    s=client.post('/services/',json={'name':'DST Service','duration_minutes':duration,'price':20,'specialist_id':owner.id},headers=h)
    assert s.status_code==200,s.text
    w=client.post('/schedules/work',json={'weekday':6,'start_time':start_time,'end_time':end_time,'specialist_id':owner.id},headers=h)
    assert w.status_code==200,w.text
    return owner,h,b.json(),r.json()['slug'],s.json()


def book(client,owner,slug,service,start,key):
    return client.post(f'/public/booking/{slug}/bookings',json={'service_id':service['id'],'staff_id':owner.id,'start':start,'full_name':'DST Guest','email':'dst@example.com','processing_consent':True,'client_request_id':key})


def test_http_fall_second_hour_offered_when_it_is_bookable(client,make_user,headers_for,monkeypatch):
    import app.api.core.availability as availability
    import app.api.v1.endpoints.public_booking as endpoint
    monkeypatch.setattr(availability,'utcnow',lambda:datetime(2026,10,24))
    monkeypatch.setattr(endpoint,'utcnow',lambda:datetime(2026,10,24))
    owner,h,b,slug,s=salon(client,make_user,headers_for,'01:00','04:00')
    offered=client.get(f'/public/booking/{slug}/slots',params={'service_id':s['id'],'staff_id':owner.id,'date_from':'2026-10-25'})
    assert offered.status_code==200,offered.text
    result=book(client,owner,slug,s,'2026-10-25T01:30:00Z','fall-second-hour')
    print('HTTP_FALL',json.dumps({'offered':offered.json(),'book_status':result.status_code,'book_body':result.json()}))
    assert result.status_code==200,result.text
    assert '2026-10-25T01:30:00Z' in {x['start'] for d in offered.json()['days'] for x in d['slots']}


def test_http_spring_offered_slot_can_be_confirmed(client,make_user,headers_for,monkeypatch):
    import app.api.core.availability as availability
    import app.api.v1.endpoints.public_booking as endpoint
    monkeypatch.setattr(availability,'utcnow',lambda:datetime(2027,3,27))
    monkeypatch.setattr(endpoint,'utcnow',lambda:datetime(2027,3,27))
    owner,h,b,slug,s=salon(client,make_user,headers_for,'01:00','04:00',120)
    offered=client.get(f'/public/booking/{slug}/slots',params={'service_id':s['id'],'staff_id':owner.id,'date_from':'2027-03-28'})
    assert offered.status_code==200,offered.text
    bad='2027-03-28T00:30:00Z'
    assert bad not in {x['start'] for d in offered.json()['days'] for x in d['slots']},offered.text
    result=book(client,owner,slug,s,bad,'spring-gap-offered')
    print('HTTP_SPRING',json.dumps({'offered':offered.json(),'book_status':result.status_code,'book_body':result.json()}))
    assert result.status_code==409,result.text

