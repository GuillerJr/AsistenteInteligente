from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.contracts import DocumentInput
from aegis_core.feedback import OwnerFeedback
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.memory.conversations import ConversationCoordinator, ConversationIpcService
from aegis_core.memory.sqlite import SQLiteMemoryStore

AUTH = IpcAuthenticator(b'a' * 32)
NS = 'user.default'


def components(path: Path):
    path.chmod(0o700)
    store = SQLiteMemoryStore(path / 'chat.sqlite3', encryption_secret=b'm' * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace=NS)
    return store, coordinator, ConversationIpcService(store, coordinator)


async def call(service, method, **payload):
    return await service.handle(AUTH.create_request('conversations.' + method, payload))


@pytest.mark.asyncio
async def test_replay_branches_at_user_turn_preserving_documents_and_project(tmp_path):
    store, coordinator, service = components(tmp_path)
    source = await coordinator.create('Trabajo')
    project = uuid4()
    await coordinator.set_project(source.conversation_id, project)
    store.mutate_conversation_context(namespace=NS, conversation_id=source.conversation_id,
        transform=lambda c: {**c, 'old_summary': 'future information'})
    for i in range(3):
        await coordinator.record_exchange(source.conversation_id, user_content=f'Pregunta {i}',
                                          assistant_content=f'Respuesta {i}',
            documents=(DocumentInput(name='nota.txt', content='Dato'),))
    original = store.conversation_history(namespace=NS, conversation_id=source.conversation_id)
    result = await call(service, 'fork', conversation_id=str(source.conversation_id),
                        before_turn_id=str(original[2].turn_id))
    assert result.ok
    assert result.payload['project_id'] == str(project)
    assert result.payload['replay']['text'] == 'Pregunta 1'
    assert result.payload['replay']['documents'] == [{'name': 'nota.txt', 'content': 'Dato'}]
    from uuid import UUID
    branch_id = UUID(result.payload['conversation_id'])
    branch = store.get_conversation(namespace=NS, conversation_id=branch_id)
    assert branch.task_checkpoint is None
    assert 'old_summary' not in branch.context_checkpoints
    turns = store.conversation_history(namespace=NS, conversation_id=branch_id)
    assert [t.content for t in turns] == ['Pregunta 0', 'Respuesta 0']
    assert {t.turn_id for t in turns}.isdisjoint(t.turn_id for t in original)
    unchanged = store.conversation_history(namespace=NS, conversation_id=source.conversation_id)
    assert unchanged == original


@pytest.mark.asyncio
async def test_replay_first_turn_keeps_temporary_and_reports_missing_image(tmp_path):
    store, coordinator, service = components(tmp_path)
    source = await coordinator.create(temporary=True)
    await coordinator.record_exchange(source.conversation_id, user_content='Mira',
                                      assistant_content='Imagen', image_name='foto.png')
    turns = store.conversation_history(namespace=NS, conversation_id=source.conversation_id)
    result = await call(service, 'fork', conversation_id=str(source.conversation_id),
                        before_turn_id=str(turns[0].turn_id))
    assert result.ok
    assert result.payload['replay']['image_name'] == 'foto.png'
    from uuid import UUID
    branch = UUID(result.payload['conversation_id'])
    assert await coordinator.is_temporary(branch)
    assert store.conversation_history(namespace=NS, conversation_id=branch) == ()


@pytest.mark.asyncio
async def test_replay_rejects_assistant_or_foreign_turn_without_creating_branch(tmp_path):
    store, coordinator, service = components(tmp_path)
    source = await coordinator.create()
    other = await coordinator.create()
    for record in [source, other]:
        await coordinator.record_exchange(record.conversation_id, user_content='Hola',
                                          assistant_content='Respuesta')
    own = store.conversation_history(namespace=NS, conversation_id=source.conversation_id)
    foreign = store.conversation_history(namespace=NS, conversation_id=other.conversation_id)
    for target in [own[1].turn_id, foreign[0].turn_id, uuid4()]:
        result = await call(service, 'fork', conversation_id=str(source.conversation_id),
                            before_turn_id=str(target))
        assert not result.ok
    assert len(store.list_conversations(namespace=NS)[0]) == 2


@pytest.mark.asyncio
async def test_feedback_is_persistent_scoped_toggleable_and_forwarded(tmp_path):
    store, coordinator, _ = components(tmp_path)
    captured = []
    async def capture(request, value):
        captured.append((request, value))

    service = ConversationIpcService(store, coordinator, on_feedback=capture)
    source = await coordinator.create()
    request_id = uuid4()
    await coordinator.record_exchange(source.conversation_id, user_content='Hola',
                                      assistant_content='Respuesta', request_id=request_id)
    user, assistant = store.conversation_history(
        namespace=NS, conversation_id=source.conversation_id)
    for value in ['helpful', 'unhelpful', None]:
        result = await call(service, 'feedback', conversation_id=str(source.conversation_id),
                            turn_id=str(assistant.turn_id), value=value)
        assert result.ok and result.payload['value'] == value
        restarted, _, restarted_service = components(tmp_path)
        message = restarted.conversation_turn(namespace=NS, conversation_id=source.conversation_id,
                                              turn_id=assistant.turn_id)
        assert message.owner_feedback == value
        history = await call(restarted_service, 'messages',
                             conversation_id=str(source.conversation_id))
        assert history.payload['turns'][1]['owner_feedback'] == value
    assert captured == [(request_id, OwnerFeedback.HELPFUL),
                        (request_id, OwnerFeedback.UNHELPFUL), (request_id, None)]
    invalid = await call(service, 'feedback', conversation_id=str(source.conversation_id),
                         turn_id=str(user.turn_id), value='helpful')
    assert invalid.error_code == 'invalid_payload'
    other = await coordinator.create()
    foreign = await call(service, 'feedback', conversation_id=str(other.conversation_id),
                         turn_id=str(assistant.turn_id), value='helpful')
    assert not foreign.ok
    assert b'Respuesta' not in (tmp_path / 'chat.sqlite3').read_bytes()


@pytest.mark.asyncio
async def test_archiving_is_distinct_from_trash_and_restorable(tmp_path):
    _, coordinator, service = components(tmp_path)
    source = await coordinator.create('Archivado')
    ident = str(source.conversation_id)
    assert (await call(service, 'file', conversation_id=ident, filed=True)).ok
    assert not (await call(service, 'list')).payload['conversations']
    archived = await call(service, 'list', filed=True)
    assert archived.payload['conversations'][0]['filed'] is True
    assert not archived.payload['conversations'][0]['archived']
    assert (await call(service, 'messages', conversation_id=ident)).ok
    assert (await call(service, 'empty_trash')).payload['deleted_count'] == 0
    assert (await call(service, 'file', conversation_id=ident, filed=False)).ok
    assert len((await call(service, 'list')).payload['conversations']) == 1
    assert (await call(service, 'trash', conversation_id=ident)).ok
    assert not (await call(service, 'list', filed=True)).payload['conversations']
    assert len((await call(service, 'list', archived=True)).payload['conversations']) == 1
