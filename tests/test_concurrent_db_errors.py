import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.core.config import settings


@pytest.mark.parametrize('locale,message', [
    ('en','The record changed. Refresh the page and try again.'),
    ('ru','Запись изменилась. Обновите страницу и повторите попытку.'),
    ('cz','Záznam se změnil. Obnovte stránku a zkuste to znovu.'),
])
@pytest.mark.parametrize('code',['40P01','40001'])
@pytest.mark.parametrize('detailed',[False,True])
def test_http_concurrency_error_redaction(db,monkeypatch,locale,message,code,detailed):
    from app.main import app as production
    monkeypatch.setattr(settings,'environment','test')
    monkeypatch.setattr(settings,'detailed_errors',detailed)
    probe=FastAPI()
    probe.exception_handlers.update(production.exception_handlers)
    @probe.post('/__scratch_probe')
    async def fail(request:Request):
        await request.body()
        db.execute(text("DO $$ BEGIN RAISE EXCEPTION 'PRIVATE_SQL_SECRET' USING ERRCODE='"+code+"'; END $$"))
    with TestClient(probe,raise_server_exceptions=False) as http:
        response=http.post('/__scratch_probe?token=PRIVATE_QUERY_SECRET',
                           json={'password':'PRIVATE_BODY_SECRET'},
                           headers={'Accept-Language':locale,'Authorization':'Bearer PRIVATE_HEADER_SECRET'})
    assert response.status_code==409,response.text
    assert response.json()['error']['message']==message
    assert 'PRIVATE_' not in response.text,response.text
    db.rollback()


@pytest.mark.parametrize('code,constraint,status',[
    ('23P01','ex_visit_items_specialist_slot',409),
    ('XX000','not_known',500),
])
def test_http_exclusion_or_unknown_db_error(db,monkeypatch,code,constraint,status):
    from app.main import app as production
    monkeypatch.setattr(settings,'environment','test')
    monkeypatch.setattr(settings,'detailed_errors',False)
    probe=FastAPI()
    probe.exception_handlers.update(production.exception_handlers)
    @probe.get('/__scratch_probe')
    def fail():
        db.execute(text("DO $$ BEGIN RAISE EXCEPTION 'PRIVATE_SQL_SECRET' USING ERRCODE='"+code+"', CONSTRAINT='"+constraint+"'; END $$"))
    with TestClient(probe,raise_server_exceptions=False) as http:
        response=http.get('/__scratch_probe')
    assert response.status_code==status,response.text
    assert 'PRIVATE_SQL_SECRET' not in response.text
    if code=='23P01':
        assert response.json()['error']['message']=='This time is already booked for the specialist'
    db.rollback()


