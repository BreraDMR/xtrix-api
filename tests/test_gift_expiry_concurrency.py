"""Concurrent directory reads must expire a certificate just once."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from tests import test_gifts as gifts
from app.api.core.clock import utcnow
from app.db.models.gift import GiftLedgerEntry

gift_world = gifts.gift_world


@pytest.mark.parametrize('directory', [False, True])
def test_parallel_reads_expire_one_certificate_once(client, db, gift_world, directory):
    _, paid = gifts._pay(client, gift_world)
    certificate = gifts._certificate(db, paid)
    certificate.expires_at = utcnow() - timedelta(minutes=1)
    certificate_id = certificate.id
    db.commit()
    db.rollback()
    gate = Barrier(8)
    url = '/businesses/me/gift-certificates'
    if not directory:
        url += f'/{certificate_id}'

    def read(_index):
        gate.wait(timeout=10)
        return client.get(url, headers=gift_world['headers'])

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(read, range(8)))
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    for response in responses:
        data = response.json()[0] if directory else response.json()
        assert data['status'] == 'expired'
        assert data['money_balance'] == 0
    db.expire_all()
    rows = db.query(GiftLedgerEntry).filter_by(certificate_id=certificate_id, entry_type='expired').all()
    assert len(rows) == 1
    assert rows[0].amount_delta == -50
