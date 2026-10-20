import pytest

from aegis_core.contracts import TEMPORARY_CONVERSATION_METADATA, UserRequest
from aegis_core.job_admission import JobAdmissionCoordinator
from aegis_core.memory.conversations import ConversationCoordinator, is_temporary
from aegis_core.memory.sqlite import SQLiteMemoryStore


def _conversations(tmp_path):
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"t" * 32)
    store.initialize()
    return ConversationCoordinator(store, namespace="user.default")


@pytest.mark.asyncio
async def test_temporary_chat_is_marked_hidden_and_purged(tmp_path):
    conversations = _conversations(tmp_path)
    kept = await conversations.create("Normal")
    temporary = await conversations.create("Chat temporal", temporary=True)
    assert is_temporary(temporary) and not is_temporary(kept)
    assert await conversations.is_temporary(temporary.conversation_id)
    assert [r.conversation_id for r in await conversations.recent()] == [kept.conversation_id]
    assert await conversations.purge_temporary() == 1
    assert not await conversations.is_temporary(temporary.conversation_id)
    assert [r.conversation_id for r in await conversations.recent()] == [kept.conversation_id]


@pytest.mark.asyncio
async def test_admission_marks_only_daemon_known_temporary_chats(tmp_path):
    conversations = _conversations(tmp_path)
    admission = JobAdmissionCoordinator(conversations)
    temporary = await conversations.create("Chat temporal", temporary=True)
    normal = await conversations.create("Normal")
    marked = await admission.resolve(UserRequest(text="hola"),
                                     conversation_id=temporary.conversation_id,
                                     persist_conversation=False)
    assert marked.request.metadata[TEMPORARY_CONVERSATION_METADATA] is True
    # A client cannot claim the flag for an ordinary chat (or strip it from a temporary one).
    spoofed = await admission.resolve(
        UserRequest(text="hola", metadata={TEMPORARY_CONVERSATION_METADATA: True}),
        conversation_id=normal.conversation_id, persist_conversation=False)
    assert TEMPORARY_CONVERSATION_METADATA not in spoofed.request.metadata


@pytest.mark.asyncio
async def test_temporary_chat_teaches_nothing():
    from aegis_core.job_completion import JobCompletionCoordinator

    class Profile:
        def __init__(self):
            self.observed = []

        async def observe(self, request):
            self.observed.append(request)

    profile = Profile()
    completion = JobCompletionCoordinator(None, owner_profile=profile, social_memory=None,
                                          capability_learning=None)
    request = UserRequest(text="me llamo Ana",
                          metadata={TEMPORARY_CONVERSATION_METADATA: True})
    await completion._observe(request, None)
    assert profile.observed == []
