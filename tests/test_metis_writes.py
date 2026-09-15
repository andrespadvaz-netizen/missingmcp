import copy
import json

import pytest

from missingmcp.metis.queue import Queue
from missingmcp.metis.transport import Worker
from missingmcp.metis.writes import WriteWorker, receipts


SECRET = 'synthetic-storage-secret-' * 3


def plan(count=1):
    return {"status": "COMPLETED", "cost_known": True, "cost_usd": 0,
            "resolved_context": "PROJECT/SUBPROJECT", "write_plan_verified": True,
            "final_answer": "Plan pendiente.", "write_plan": [
        {"ordinal": n, "action": {"context": "PROJECT/SUBPROJECT", "destination": "notes", "provider": "NOTION",
          "object_id": f"root-{n}", "operation": "create", "payload": {"title": "Private title", "text": "PRIVATE BUSINESS CONTENT"},
          "snapshot": {}, "policy_revision": "one", "policy_hash": "a" * 64}}
        for n in range(1, count+1)]}


@pytest.fixture
def q(tmp_path):
    queue = Queue(str(tmp_path/'db.sqlite'), SECRET)
    yield queue
    queue.close()


def schedule(q, count=1, key='write-request-0001'):
    value = q.create('operator', {'request': 'Create a note', 'idempotency_key': key})
    row = q.next()
    q.claim(row)
    q.finish(row, plan(count))
    return value['execution_id'], q.next()


class FakeBridge:
    def __init__(self, q, replies=None):
        self.q, self.calls = q, []
        self.replies = replies or []

    async def call_write(self, kind, wire, action):
        row = self.q.db.execute('SELECT * FROM metis_write_actions WHERE dispatch_seq=?', (wire['seq'],)).fetchone()
        assert row['status'] == 'SENT'
        assert not self.q.db.in_transaction
        self.calls.append((kind, wire['seq']))
        if self.replies:
            result = self.replies.pop(0)
            if isinstance(result, Exception):
                raise result
        else:
            result = {'status': 'CONFIRMED', 'verified': True, 'provider_object_id': f"object-{wire['seq']}", 'url': 'https://www.notion.so/test'}
        return {'state': 'DONE', 'result': result}


def test_plan_is_durable_encrypted_and_not_reported_complete(q):
    eid, row = schedule(q, 2)
    view = q.get('operator', eid)
    assert view['status'] == 'APPLYING'
    assert 'write_plan' not in view
    assert view['write_plan_count'] == 2
    rows = list(q.db.execute('SELECT * FROM metis_write_actions'))
    assert len(rows) == 2 and all(x['status'] == 'PREPARED' for x in rows)
    assert all('PRIVATE BUSINESS CONTENT' not in x['action_enc'] for x in rows)
    q.finish(row, plan(2))  # duplicate delivery cannot schedule twice
    assert q.db.execute('SELECT count(*) FROM metis_write_actions').fetchone()[0] == 2


@pytest.mark.parametrize('damage', ['scope', 'verified', 'operation', 'duplicate', 'ordinal'])
def test_invalid_whole_plan_records_no_intents(q, damage):
    q.create('operator', {'request': 'Create note', 'idempotency_key': 'write-request-0001'})
    row = q.next(); q.claim(row)
    result = plan(2)
    if damage == 'scope': result['write_plan'][1]['action']['context'] = 'OTHER'
    if damage == 'verified': result['write_plan_verified'] = False
    if damage == 'operation': result['write_plan'][1]['action']['operation'] = 'delete'
    if damage == 'duplicate': result['write_plan'][1]['action']['object_id'] = 'root-1'
    if damage == 'ordinal': result['write_plan'][0]['ordinal'] = True
    q.finish(row, result)
    assert q.get('operator', row['id'])['status'] == 'REQUIRES_ANDRES'
    assert q.next() is None
    assert q.db.execute('SELECT count(*) FROM metis_write_actions').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_all_writes_verify_and_retry_create_returns_same_receipts(q):
    eid, row = schedule(q, 2)
    bridge = FakeBridge(q)
    worker = Worker(q, bridge)
    await worker.step()
    assert q.get('operator', eid)['status'] == 'APPLYING'
    await worker.step()
    view = q.get('operator', eid)
    assert view['status'] == 'COMPLETED'
    assert all(x['verified'] for x in view['write_receipts'])
    assert bridge.calls == [('write', 1), ('write', 2)]
    assert q.create('operator', {'request': 'Create a note', 'idempotency_key': 'write-request-0001'}) == view


@pytest.mark.asyncio
async def test_network_cut_recovers_by_status_only_after_reopening_database(q):
    eid, row = schedule(q)
    bridge = FakeBridge(q, [RuntimeError('connection lost')])
    await WriteWorker(q, bridge).step(row)
    assert receipts(q, eid)[0]['status'] == 'SENT'
    path = q.db.execute('PRAGMA database_list').fetchone()[2]
    reopened = Queue(path, SECRET)
    try:
        recovered = FakeBridge(reopened)
        await WriteWorker(reopened, recovered).step(reopened.next())
        assert recovered.calls == [('write_status', 1)]
        assert reopened.get('operator', eid)['status'] == 'COMPLETED'
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_uncertain_write_stops_rest_pauses_writes_but_not_reads(q):
    eid, row = schedule(q, 2)
    bridge = FakeBridge(q, [{'status': 'UNCERTAIN', 'verified': False, 'error': 'cut'}])
    await WriteWorker(q, bridge).step(row)
    view = q.get('operator', eid)
    assert view['status'] == 'REQUIRES_ANDRES' and view['writes_paused']
    assert [x['status'] for x in receipts(q, eid)] == ['UNCERTAIN', 'SKIPPED']
    read = q.create('operator', {'request': 'Read project', 'idempotency_key': 'read-request-0001'})
    read_row = q.next(); q.claim(read_row)
    q.finish(read_row, {'status': 'COMPLETED', 'cost_known': True, 'final_answer': 'Read result'})
    assert q.get('operator', read['execution_id'])['status'] == 'COMPLETED'
    assert bridge.calls == [('write', 1)]


@pytest.mark.asyncio
async def test_skipped_actions_do_not_leave_gaps_in_bridge_sequence(q):
    _, row = schedule(q, 2)
    bridge = FakeBridge(q, [{'status': 'REJECTED', 'verified': False, 'error': 'EDIT_CONFLICT'}])
    await WriteWorker(q, bridge).step(row)
    eid, next_row = schedule(q, key='write-request-0002')
    await WriteWorker(q, bridge).step(next_row)
    assert bridge.calls == [('write', 1), ('write', 2)]
    assert q.get('operator', eid)['status'] == 'COMPLETED'


@pytest.mark.asyncio
async def test_unverified_success_never_claims_completion(q):
    eid, row = schedule(q)
    bridge = FakeBridge(q, [{'status': 'CONFIRMED', 'verified': False, 'provider_object_id': 'x'}])
    await WriteWorker(q, bridge).step(row)
    assert q.get('operator', eid)['status'] == 'REQUIRES_ANDRES'
    assert receipts(q, eid)[0]['status'] == 'UNCERTAIN'


def test_origin_is_persisted_by_client_not_request(q):
    value = q.create('operator', {'request': 'Read', 'idempotency_key': 'read-request-0001'}, 'OPENAI')
    assert q.next()['origin_model'] == 'OPENAI'
    duplicate = q.create('operator', {'request': 'Read', 'idempotency_key': 'read-request-0001'}, 'ANTHROPIC')
    assert duplicate['execution_id'] == value['execution_id']
    assert q.next()['origin_model'] == 'OPENAI'
