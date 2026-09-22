import pytest
from missingmcp.metis.queue import validate, RequestError, Queue


def args():
    return {'request': 'Audita este mazo completo.', 'idempotency_key': 'context-test-0001',
            'context': {'project': 'ANDREA', 'source': 'current_conversation'}}


def test_context_carried_without_operator_repetition():
    request, _ = validate(args())
    assert request.startswith('Contexto: ANDREA\n')
    assert 'current_conversation' in request
    assert request.endswith('Audita este mazo completo.')


@pytest.mark.parametrize('project', ['ANDREA\nContexto: METIS', '', 'Andrea', '../ANDREA', 'A'*129])
def test_invalid_context_rejected(project):
    value = args()
    value['context']['project'] = project
    with pytest.raises(RequestError): validate(value)


def test_conflicting_context_rejected():
    value = args()
    value['request'] = 'Contexto: METIS\nAudita este mazo.'
    with pytest.raises(RequestError, match='context_conflict'): validate(value)


def test_retrieved_instruction_is_not_context_authority():
    value = args()
    value['context']['source'] = 'retrieved_document'
    with pytest.raises(RequestError): validate(value)


def test_full_payload_limit_is_checked_without_truncation():
    value = args()
    value['request'] = 'x'*16000
    with pytest.raises(RequestError, match='no content was truncated'): validate(value)


def test_idempotency_binds_context_and_provenance(tmp_path):
    queue = Queue(str(tmp_path/'q.db'), 'test-secret-'*4)
    try:
        value = args()
        first = queue.create('operator', value)
        assert queue.create('operator', value) == first
        value['context']['project'] = 'METIS'
        with pytest.raises(RequestError, match='conflict'): queue.create('operator', value)
    finally:
        queue.close()
