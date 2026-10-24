from app.api.core import import_service
from app.db.models.client import Client
from tests.tenant_world import build_tenant
from tests import test_imports as imports

def test_import_confirm_cannot_execute_a_new_unapproved_mapping(client, db, make_user, headers_for, monkeypatch):
    tenant = build_tenant(client, make_user, headers_for, 'Import', '+42070069')
    headers = tenant['headers']
    content = imports._csv([['Approved name', 'Unapproved name', 'mapping-race@example.com']], headers=['Name','Alt','Email'])
    batch = imports._prepare(client, headers, content)
    original_execute = import_service.execute_batch
    replacement_preview = {}

    def race_execute(session, stale_batch, scope, user, **kwargs):
        mapped = client.put(f"/businesses/me/imports/{batch['id']}/mapping", headers=headers, json={'mapping': {'full_name':'Alt','email':'Email'}})
        assert mapped.status_code == 200, mapped.text
        preview = client.post(f"/businesses/me/imports/{batch['id']}/preview", headers=headers, json={'duplicate_policy':'skip'})
        assert preview.status_code == 200, preview.text
        replacement_preview.update(preview.json())
        return original_execute(session, stale_batch, scope, user, **kwargs)

    monkeypatch.setattr(import_service, 'execute_batch', race_execute)
    result = imports._confirm(client, headers, batch)
    db.expire_all()
    changed = db.query(Client).filter(Client.full_name == 'Unapproved name').count()
    print({'status':result.status_code, 'unapproved_clients':changed, 'confirmed_mapping_version':batch['mapping_version'], 'replacement_mapping_version':replacement_preview['mapping_version']})
    assert result.status_code == 409 and changed == 0, result.text



import pytest

@pytest.mark.parametrize('value', ['2026-03-29 02:30', '2026-10-25 02:30'])
def test_import_rejects_nonexistent_or_ambiguous_local_time(value):
    with pytest.raises(import_service.ImportRowError):
        import_service._parse_visit_datetime(value, 'Europe/Prague')

@pytest.mark.parametrize('value, expected', [('2026-10-25T02:30:00+02:00', '2026-10-25T00:30:00'), ('2026-10-25T02:30:00+01:00', '2026-10-25T01:30:00')])
def test_import_preserves_explicit_fold_offsets(value, expected):
    assert import_service._parse_visit_datetime(value, 'Europe/Prague').isoformat() == expected
