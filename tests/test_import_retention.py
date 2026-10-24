import pytest
from tests.tenant_world import build_tenant
from tests import test_imports as imports

@pytest.mark.parametrize('finish', [False,True])
def test_raw_unmapped_source_is_not_retained_after_expiry_or_completion(client, db, make_user, headers_for, finish):
    import json
    from datetime import datetime
    from app.db.models.import_batch import ImportBatch,ImportRow
    tenant=build_tenant(client,make_user,headers_for,'Retention','+42070068')
    marker='UNMAPPED_PRIVATE_NOTE_MARKER'
    content=imports._csv([['Retention Person','retention@example.com',marker]],headers=['Name','Email','PrivateLegacyNotes'])
    preview=imports._prepare(client,tenant['headers'],content)
    stored=db.get(ImportBatch,preview['id'])
    if finish:
        response=imports._confirm(client,tenant['headers'],preview)
        assert response.status_code == 200,response.text
    else:
        stored.source_expires_at=datetime(2000,1,1)
        db.commit()
        response=client.get(f"/businesses/me/imports/{preview['id']}",headers=tenant['headers'])
        assert response.status_code == 200,response.text
    db.expire_all()
    stored=db.get(ImportBatch,preview['id'])
    raw=db.query(ImportRow).filter(ImportRow.batch_id==stored.id).one().raw
    print({'finish':finish,'source_blob_cleared':stored.source_blob is None,'private_raw_retained':marker in json.dumps(raw)})
    assert stored.source_blob is None
    assert marker not in json.dumps(raw)


def _expired_preview(client, db, make_user, headers_for):
    from datetime import datetime
    from app.db.models.import_batch import ImportBatch
    tenant = build_tenant(client, make_user, headers_for, 'Restore', '+42070066')
    content = imports._csv([['Restored Person', 'restore@example.com', 'UNMAPPED_RESTORE_MARKER']],
                           headers=['Name', 'Email', 'PrivateLegacyNotes'])
    preview = imports._prepare(client, tenant['headers'], content)
    batch = db.get(ImportBatch, preview['id'])
    batch.source_expires_at = datetime(2000, 1, 1)
    db.commit()
    assert client.get(f"/businesses/me/imports/{batch.id}", headers=tenant['headers']).status_code == 200
    return tenant, content, preview


def test_expired_source_keeps_approved_preview_executable(client, db, make_user, headers_for):
    from app.db.models.client import Client
    from app.db.models.import_batch import ImportRow
    tenant, _, preview = _expired_preview(client, db, make_user, headers_for)
    result = imports._confirm(client, tenant['headers'], preview)
    assert result.status_code == 200, result.text
    db.expire_all()
    assert db.query(Client).filter(Client.full_name == 'Restored Person').count() == 1
    assert db.query(ImportRow).filter(ImportRow.batch_id == preview['id']).one().raw == {}


def test_expired_source_can_be_reuploaded_without_duplicate_batch_or_old_confirmation(client, db, make_user, headers_for):
    from app.db.models.import_batch import ImportBatch, ImportRow
    tenant, content, preview = _expired_preview(client, db, make_user, headers_for)
    blocked = client.post(f"/businesses/me/imports/{preview['id']}/preview", headers=tenant['headers'],
                          json={'duplicate_policy': 'skip'})
    assert blocked.status_code == 409
    assert blocked.json()['error']['detail']['code'] == 'import_source_expired'
    restored = client.post('/businesses/me/imports', headers=tenant['headers'],
                           files={'file': ('restored.csv', content, 'text/csv')})
    assert restored.status_code == 200, restored.text
    assert restored.json()['id'] == preview['id']
    assert restored.json()['preview_version'] == 0
    assert imports._confirm(client, tenant['headers'], preview).status_code == 409
    fresh = client.post(f"/businesses/me/imports/{preview['id']}/preview", headers=tenant['headers'],
                        json={'duplicate_policy': 'skip'})
    assert fresh.status_code == 200, fresh.text
    assert imports._confirm(client, tenant['headers'], fresh.json()).status_code == 200
    db.expire_all()
    assert db.query(ImportBatch).count() == 1
    assert db.query(ImportRow).filter(ImportRow.batch_id == preview['id']).one().raw == {}


def test_background_job_purges_an_unopened_expired_source(client, db, make_user, headers_for):
    from datetime import datetime
    from app.api.core.import_service import purge_expired_sources
    from app.db.models.import_batch import ImportBatch, ImportRow
    tenant = build_tenant(client, make_user, headers_for, 'Background', '+42070065')
    content = imports._csv([['Background Person', 'background@example.com', 'UNMAPPED_BACKGROUND_MARKER']],
                           headers=['Name', 'Email', 'PrivateLegacyNotes'])
    preview = imports._prepare(client, tenant['headers'], content)
    batch = db.get(ImportBatch, preview['id'])
    batch.source_expires_at = datetime(2000, 1, 1)
    db.commit()
    result = purge_expired_sources()
    assert result['checked'] == 1
    db.expire_all()
    assert db.get(ImportBatch, preview['id']).source_blob is None
    assert db.query(ImportRow).filter(ImportRow.batch_id == preview['id']).one().raw == {}
    assert purge_expired_sources()['checked'] == 0
