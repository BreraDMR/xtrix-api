"""Private errors keep no-store without changing statuses or exception routing."""
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.api.core import error_handlers, visitor_history_views
from app.api.private_routes import NoStoreRoute
from app.db.models.visitor_account import VisitorAccount
from app.db.session import engine, SessionLocal
from app.main import app
from tests import test_visitor_history as base
from tests.test_visitor_history_api import access

world = base.world


def test_actual_private_internal_valueerror_stays_500_and_private(db, world, monkeypatch):
    headers, _, _ = access(db, world)
    def broken(*_args, **_kwargs):
        raise ValueError('synthetic-private-error-canary')
    monkeypatch.setattr(visitor_history_views, 'list_visits', broken)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/visitor/visits', headers=headers)
    assert response.status_code == 500, response.text
    assert headers['X-Visitor-Token'] not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_actual_deferred_pg_commit_failure_is_private_500_and_rolls_back(db, world, monkeypatch):
    headers, _, _ = access(db, world)
    with engine.begin() as connection:
        connection.execute(text("""CREATE FUNCTION test_private_deferred_failure() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic-private-deferred-failure'; RETURN NEW; END $$"""))
        connection.execute(text("""CREATE CONSTRAINT TRIGGER test_private_deferred_trigger
            AFTER INSERT ON visitor_accounts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            WHEN (NEW.email_canonical = 'deferred-private@example.test')
            EXECUTE FUNCTION test_private_deferred_failure()"""))
    flushed = []
    def broken(session, *_args, **_kwargs):
        session.add(VisitorAccount(email_canonical='deferred-private@example.test', email_verified_at=base.NOW))
        session.flush()
        flushed.append(True)
        session.commit()
        pytest.fail('The deferred constraint must fail at commit')
    monkeypatch.setattr(visitor_history_views, 'list_visits', broken)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get('/visitor/visits', headers=headers)
        assert flushed == [True]
        assert response.status_code == 500, response.text
        assert response.json()['error']['type'] == 'DatabaseError'
        with SessionLocal() as observer:
            assert observer.query(VisitorAccount).filter_by(email_canonical='deferred-private@example.test').count() == 0
            assert observer.execute(text('SELECT 1')).scalar_one() == 1
    finally:
        with engine.begin() as connection:
            connection.execute(text('DROP TRIGGER test_private_deferred_trigger ON visitor_accounts'))
            connection.execute(text('DROP FUNCTION test_private_deferred_failure()'))
    assert response.headers['cache-control'] == 'no-store'


def local_app():
    local = FastAPI()
    local.add_exception_handler(Exception, error_handlers.general_exception_handler)
    local.add_exception_handler(SQLAlchemyError, error_handlers.database_exception_handler)
    local.add_exception_handler(StarletteHTTPException, error_handlers.http_exception_handler)
    private = APIRouter(route_class=NoStoreRoute)
    @private.get('/private-failure')
    def private_failure():
        raise ValueError('private-canary')
    @private.get('/private-auth')
    def private_auth():
        raise HTTPException(401, 'Authentication required', headers={
            'cache-control': 'public, max-age=100', 'CACHE-CONTROL': 'max-age=200',
            'WWW-Authenticate': 'Bearer', 'Allow': 'GET', 'Retry-After': '30'})
    local.include_router(private)
    @local.get('/ordinary-failure')
    def ordinary_failure():
        raise ValueError('ordinary-canary')
    return local


def test_private_failure_retains_default_testclient_raise_semantics():
    with TestClient(local_app()) as client:
        with pytest.raises(ValueError, match='private-canary'):
            client.get('/private-failure')


def test_nonprivate_failure_does_not_gain_header_or_inherit_prior_private_flag():
    with TestClient(local_app(), raise_server_exceptions=False) as client:
        private = client.get('/private-failure')
        ordinary = client.get('/ordinary-failure', headers={'no_store_response': 'true'})
    assert private.status_code == ordinary.status_code == 500
    assert private.headers['cache-control'] == 'no-store'
    assert 'cache-control' not in ordinary.headers
    assert private.json()['error']['type'] == ordinary.json()['error']['type'] == 'ValueError'


def test_private_401_keeps_auth_allow_retry_headers_and_single_cache_directive():
    with TestClient(local_app()) as client:
        response = client.get('/private-auth')
    assert response.status_code == 401
    assert response.headers.get_list('cache-control') == ['no-store']
    assert response.headers['www-authenticate'] == 'Bearer'
    assert response.headers['allow'] == 'GET'
    assert response.headers['retry-after'] == '30'


def test_405_keeps_allow_and_prior_method_dispatch_behavior():
    with TestClient(local_app()) as client:
        response = client.post('/private-auth')
    assert response.status_code == 405
    assert response.headers['allow'] == 'GET'
    # Method rejection happens before this route's request handler.
    assert 'cache-control' not in response.headers


@pytest.mark.parametrize('flag', [False, True])
def test_shared_error_builder_preserves_input_headers_without_mutation(flag):
    request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': [], 'query_string': b''})
    if flag:
        request.state.no_store_response = True
    headers = {'CaChe-CoNtRoL': 'public', 'WWW-Authenticate': 'Bearer', 'Allow': 'GET', 'Retry-After': '9'}
    original = headers.copy()
    response = error_handlers.create_error_response(500, 'synthetic error', request=request, headers=headers)
    assert headers == original
    assert response.status_code == 500
    assert response.headers['cache-control'] == ('no-store' if flag else 'public')
    assert response.headers['www-authenticate'] == 'Bearer'
    assert response.headers['allow'] == 'GET'
    assert response.headers['retry-after'] == '9'
