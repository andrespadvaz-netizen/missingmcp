import asyncio
import json
import httpx
import pytest
from missingmcp.config import load_config
from missingmcp.metis.queue import Queue, RequestError
from missingmcp.metis.transport import Worker
from missingmcp.adapters.metis import MetisAdapter
from missingmcp.adapters.base import LoginError, SessionExpired


@pytest.mark.asyncio
async def test_bridge_redirect_logs_are_private_and_context_resets(monkeypatch, capsys):
    import logging
    from missingmcp.log import _StructuredHandler, private_transport
    from missingmcp.metis.transport import Bridge
    real_client = httpx.AsyncClient
    handler = _StructuredHandler()
    logger = logging.getLogger('httpx')
    prior = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    def response(request):
        if request.url.host == 'bridge.test':
            return httpx.Response(302, headers={'location':'https://result.test/?user_content_key=PRIVATE_RESULT'})
        return httpx.Response(200,json={'id':'id-1','seq':1,'state':'DONE'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(response),**kw))
    try:
        await Bridge('https://bridge.test/', 'secret').call('status',{'id':'id-1','seq':1,'fingerprint':'fp'})
        assert not private_transport.get()
        logger.info('ordinary request remains visible')
        output = capsys.readouterr().out
        assert 'PRIVATE_RESULT' not in output
        assert 'ordinary request remains visible' in output
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prior)

SECRET = 'storage-secret-' * 4
REQUEST = {'request':'Consulta Metis: ¿cuál es el estado del proyecto?', 'idempotency_key':'test-request-0001'}


@pytest.fixture
def queue(tmp_path):
    q = Queue(str(tmp_path/'gateway.db'), SECRET)
    yield q
    q.close()


def test_identity_idempotency_and_conflict(queue):
    one = queue.create('operator', REQUEST)
    assert one == queue.create('operator', REQUEST)
    assert one['execution_id'] == one['correlation_id']
    assert one['status'] == 'QUEUED'
    with pytest.raises(RequestError, match='conflict'):
        queue.create('operator', {**REQUEST,'request':'Otra necesidad'})
    with pytest.raises(RequestError, match='not_found'):
        queue.get('intruder', one['execution_id'])


@pytest.mark.parametrize('args', [None, {}, [], {**REQUEST,'model':'OPENAI'},
    {**REQUEST,'request':' '}, {**REQUEST,'request':'ñ'*8001},
    {**REQUEST,'request':42}, {**REQUEST,'idempotency_key':'short'}])
def test_invalid_payload(queue, args):
    with pytest.raises(RequestError):
        queue.create('operator', args)
    assert queue.next() is None


def test_durable_encrypted_queue(tmp_path):
    path = str(tmp_path/'gateway.db')
    q = Queue(path, SECRET)
    one = q.create('operator', REQUEST)
    assert q.claim(q.next())
    assert not q.claim(q.next())
    q.close()
    q = Queue(path, SECRET)
    assert q.get('operator',one['execution_id'])['status'] == 'DISPATCHED'
    assert q.request(q.next()) == REQUEST['request']
    assert REQUEST['request'] not in q.next()['request_enc']
    q.close()


class FakeBridge:
    def __init__(self, status='COMPLETED', lose=False):
        self.calls = []
        self.lose = lose
        self.result = {'status':status,'engine_execution_id':'engine-original',
                       'final_answer':'Respuesta íntegra á漢🙂\n'*3000, 'route':'CROSS_AUDIT',
                       'cost_usd':0.12,'cost_known':True,'limits':{'max_run_budget_usd':1}}

    async def call(self, action, row, request):
        self.calls.append(action)
        if self.lose and action == 'run':
            raise httpx.ReadTimeout('contains secret: must never be logged')
        return {'state':'DONE','seq':row['seq'],'id':row['id'],'result':self.result}


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['COMPLETED','FAILED','REQUIRES_ANDRES'])
async def test_lifecycle_exact_result_and_paid_retry(queue, status):
    one = queue.create('operator',REQUEST)
    bridge = FakeBridge(status, lose=True)
    worker = Worker(queue,bridge)
    await worker.step()
    assert queue.get('operator',one['execution_id'])['status'] == 'DISPATCHED'
    # New worker simulates service restart, and client resubmission reuses ID.
    worker = Worker(queue, bridge)
    assert queue.create('operator',REQUEST)['execution_id'] == one['execution_id']
    await worker.step()
    await worker.step()
    answer = queue.get('operator',one['execution_id'])
    assert answer['status'] == status
    for key, value in bridge.result.items():
        assert answer[key] == value
    assert bridge.calls == ['run','status']


@pytest.mark.asyncio
async def test_client_disconnect_does_not_own_worker(queue):
    one = queue.create('operator',REQUEST)
    stop = asyncio.Event()
    worker = Worker(queue,FakeBridge())
    task = asyncio.create_task(worker.run(stop))
    await asyncio.sleep(.02)
    stop.set()
    await task
    assert queue.get('operator',one['execution_id'])['status'] == 'COMPLETED'


def test_capacity_fail_closed(queue):
    for i in range(5):
        queue.create('operator',{**REQUEST,'idempotency_key':f'request-number-{i:04d}'})
    with pytest.raises(RequestError,match='queue_full'):
        queue.create('operator',REQUEST)


@pytest.mark.parametrize('env', [
    {'METIS_BRIDGE_URL':'http://evil/exec'},
    {'METIS_OPERATOR_KEY':'short'},
    {'METIS_BRIDGE_URL':'https://script.google.com/macros/s/abc/exec',
     'METIS_OPERATOR_KEY':'x'*32,'METIS_BRIDGE_SECRET':'x'*32}
])
def test_incomplete_config_fails_closed(env):
    with pytest.raises(ValueError):
        load_config({'GATEWAY_SECRET':SECRET, **env})


@pytest.mark.asyncio
async def test_mcp_authorization_and_contract(tmp_path):
    cfg = load_config({'GATEWAY_SECRET':SECRET,'DATA_DIR':str(tmp_path),
        'METIS_BRIDGE_URL':'https://script.google.com/macros/s/abc/exec',
        'METIS_BRIDGE_SECRET':'b'*32,'METIS_OPERATOR_KEY':'o'*32})
    adapter = MetisAdapter(cfg)
    with pytest.raises(LoginError):
        adapter.start_login({'metis_key':'wrong'})
    login = adapter.start_login({'metis_key':'o'*32})
    assert 'o'*32 not in login.blob
    body = json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call',
                       'params':{'name':'metis_create_execution','arguments':REQUEST}}).encode()
    with pytest.raises(SessionExpired):
        await adapter.handle(None,'intruder',login.blob,body)
    with pytest.raises(SessionExpired):
        await adapter.handle(None,login.account_key,'wrong',body)
    status, headers, data = await adapter.handle(None,login.account_key,login.blob,body)
    assert status == 200
    result = json.loads(data)['result']
    assert not result['isError']
    assert json.loads(result['content'][0]['text'])['status'] == 'QUEUED'
    assert 'o'*32 not in data.decode() and 'b'*32 not in data.decode()
    adapter.queue.close()


@pytest.mark.asyncio
async def test_uncertain_transport_pauses_new_spend(queue):
    one = queue.create('operator',REQUEST)
    queue.claim(queue.next())
    queue.db.execute("UPDATE metis_executions SET dispatched=1")
    bridge = FakeBridge()
    async def unavailable(*args):
        raise httpx.ConnectError('secret')
    bridge.call = unavailable
    await Worker(queue,bridge).step()
    answer = queue.get('operator',one['execution_id'])
    assert answer['status'] == 'FAILED' and answer['requires_review']
    assert answer['cost_usd'] is None
    assert queue.next() is None
    with pytest.raises(RequestError,match='paused'):
        queue.create('operator',{**REQUEST,'idempotency_key':'new-request-0002'})
    assert queue.create('operator',REQUEST)['execution_id'] == one['execution_id']


def test_http_auth_isolation_and_malformed_request(tmp_path):
    from starlette.testclient import TestClient
    from missingmcp.app import build_app
    from missingmcp import store
    cfg = load_config({'GATEWAY_SECRET':SECRET,'DATA_DIR':str(tmp_path),
        'METIS_BRIDGE_URL':'https://script.google.com/macros/s/abc/exec',
        'METIS_BRIDGE_SECRET':'b'*32,'METIS_OPERATOR_KEY':'o'*32})
    app = build_app(cfg)
    conn = store.init_db(cfg.db_path)
    store.upsert_account(conn,'metis','metis-operator',__import__('hashlib').sha256(('o'*32).encode()).hexdigest(),SECRET)
    store.create_access_token(conn,store.hash_token('valid'),'metis','metis-operator','client')
    store.create_access_token(conn,store.hash_token('other'),'garmin','metis-operator','client')
    # No lifespan: this test must never contact the remote bridge.
    client = TestClient(app)
    body={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'metis_create_execution','arguments':REQUEST}}
    assert client.post('/metis/mcp',json=body).status_code == 401
    assert client.post('/metis/mcp',json=body,headers={'Authorization':'Bearer other'}).status_code == 401
    good=client.post('/metis/mcp',json=body,headers={'Authorization':'Bearer valid'})
    assert good.status_code == 200 and not good.json()['result']['isError']
    bad=client.post('/metis/mcp',json={**body,'params':42},headers={'Authorization':'Bearer valid'})
    assert bad.status_code == 200 and bad.json()['error']['code'] == -32602
    assert client.get('/metis').status_code == 200
    assert client.get('/.well-known/oauth-protected-resource/metis/mcp').status_code == 200
    conn.close()
