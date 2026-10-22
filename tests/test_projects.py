
import pytest

from aegis_core.contracts import (
    PROJECT_CONTEXT_METADATA,
    TEMPORARY_CONVERSATION_METADATA,
    UNSIGNED_PEER_METADATA,
    InputModality,
    UserRequest,
)
from aegis_core.job_admission import JobAdmissionCoordinator
from aegis_core.memory.conversations import ConversationCoordinator, ConversationIpcService
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.projects import MAX_CONTEXT_BYTES, ProjectError, ProjectIpcService, ProjectStore


class Request:
    def __init__(self, method, payload=None):
        self.method, self.payload = method, payload or {}


def _setup(tmp_path):
    tmp_path.chmod(0o700)
    memory = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"t" * 32)
    memory.initialize()
    conversations = ConversationCoordinator(memory, namespace="user.default")
    store = ProjectStore(tmp_path / "projects.json")
    conversations.project_context = store.context
    return memory, conversations, store


def test_store_keeps_instructions_and_text_files_within_limits(tmp_path):
    _, _, store = _setup(tmp_path)
    project = store.save(project_id=None, name="  Tesis   doctoral ", color="sage", icon="book",
                         instructions="Responde con citas en formato APA.")
    assert project.name == "Tesis doctoral"
    store.add_file(project.project_id, "capitulo-1.md", "La hipótesis central es…")
    with pytest.raises(ProjectError):
        store.add_file(project.project_id, "clave.pem", "-----BEGIN PRIVATE KEY-----\nMIIE")
    with pytest.raises(ProjectError):
        store.add_file(project.project_id, "vacío.txt", "   ")
    context = store.context(project.project_id)
    assert context["instructions"] == "Responde con citas en formato APA."
    assert context["knowledge"] == [{"name": "capitulo-1.md", "text": "La hipótesis central es…"}]
    big = store.save(project_id=None, name="Grande", color="clay", icon="folder", instructions="")
    for index in range(4):
        store.add_file(big.project_id, f"parte {index}", "x" * 60_000)
    sizes = sum(len(item["text"]) for item in store.context(big.project_id)["knowledge"])
    assert sizes <= MAX_CONTEXT_BYTES
    assert store.delete(big.project_id) and store.get(big.project_id) is None


@pytest.mark.asyncio
async def test_chats_in_a_project_carry_its_context_and_a_client_cannot_forge_it(tmp_path):
    memory, conversations, store = _setup(tmp_path)
    project = store.save(project_id=None, name="Viaje", color="sky", icon="plane",
                         instructions="Presupuesto máximo 2.000 USD.")
    chats = ConversationIpcService(memory, conversations)
    created = await chats.handle(Request("conversations.create", {
        "title": "Hoteles", "project_id": str(project.project_id)}))
    assert created.ok and created.payload["project_id"] == str(project.project_id)
    chat_id = created.payload["conversation_id"]
    admission = JobAdmissionCoordinator(conversations)
    resolved = await admission.resolve(UserRequest(text="¿qué hotel me conviene?"),
                                       conversation_id=chat_id, persist_conversation=False)
    context = resolved.request.metadata[PROJECT_CONTEXT_METADATA]
    assert context["name"] == "Viaje" and "2.000 USD" in context["instructions"]
    plain = (await chats.handle(Request("conversations.create", {"title": "Otro"}))).payload
    forged = await admission.resolve(
        UserRequest(text="hola", metadata={PROJECT_CONTEXT_METADATA: {"name": "Falso"}}),
        conversation_id=plain["conversation_id"], persist_conversation=False)
    assert PROJECT_CONTEXT_METADATA not in forged.request.metadata
    listed = await chats.handle(Request("conversations.list", {}))
    assert {c["title"]: c["project_id"] for c in listed.payload["conversations"]} == {
        "Hoteles": str(project.project_id), "Otro": None}


@pytest.mark.asyncio
async def test_owner_manages_projects_and_moves_chats(tmp_path):
    _, conversations, store = _setup(tmp_path)
    service = ProjectIpcService(store, conversations)
    saved = await service.handle(Request("projects.save", {
        "name": "Trabajo", "color": "plum", "icon": "briefcase",
        "instructions": "Tono formal."}))
    assert saved.ok
    project_id = saved.payload["project"]["project_id"]
    chat = await conversations.create("Informe")
    moved = await service.handle(Request("projects.assign", {
        "conversation_id": str(chat.conversation_id), "project_id": project_id}))
    assert moved.payload["projects"][0]["chats"] == 1
    listed = await service.handle(Request("projects.conversations", {"project_id": project_id}))
    assert [c["title"] for c in listed.payload["conversations"]] == ["Informe"]
    added = await service.handle(Request("projects.file.add", {
        "project_id": project_id, "name": "guía.md", "text": "Usa títulos cortos."}))
    file_id = added.payload["projects"][0]["files"][0]["file_id"]
    removed = await service.handle(Request("projects.file.remove", {
        "project_id": project_id, "file_id": file_id}))
    assert removed.payload["projects"][0]["files"] == []
    refused = await service.handle(Request("projects.file.add", {
        "project_id": project_id, "name": "x", "text": "-----BEGIN PRIVATE KEY-----\nMIIE"}))
    assert "credencial" in refused.payload["error"]
    deleted = await service.handle(Request("projects.delete", {"project_id": project_id}))
    assert deleted.payload["projects"] == []
    # The chat survives, outside any project.
    assert await conversations.project_context_for(chat.conversation_id) is None
    gone = await service.handle(Request("projects.assign", {
        "conversation_id": str(chat.conversation_id), "project_id": project_id}))
    assert gone.payload["error"] == "Ese proyecto ya no existe."


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["text", "owner_voice", "unknown_voice", "unsigned"])
async def test_project_context_only_reaches_provider_for_owner(tmp_path, identity):
    import asyncio

    from aegis_core.contracts import AgentResult
    from aegis_core.job_contracts import JobStatus
    from aegis_core.jobs import SwarmJobManager
    from aegis_core.orchestration.graph import build_nvidia_swarm_graph as build_swarm_graph

    _, conversations, store = _setup(tmp_path)
    project = store.save(project_id=None, name="Tesis", color="clay", icon="book",
                         instructions="Cita en APA.")
    store.add_file(project.project_id, "cap1.md", "Hipótesis X.")
    chat = await conversations.create("Capítulo")
    await conversations.set_project(chat.conversation_id, project.project_id)
    prompts = []

    class Provider:
        async def complete_nvidia_only(self, **kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return AgentResult(role=kwargs["role"], model_id="synthetic", content="Listo.")

    jobs = SwarmJobManager(build_swarm_graph(Provider(), inference_nvidia_only=True),
                           conversations=conversations)
    request = _request(identity)
    resolved = await JobAdmissionCoordinator(conversations).resolve(
        request, conversation_id=chat.conversation_id, persist_conversation=False)
    allowed = identity in {"text", "owner_voice"}
    assert (PROJECT_CONTEXT_METADATA in resolved.request.metadata) is allowed
    submitted = await jobs.submit(request,
                                  conversation_id=chat.conversation_id)
    for _ in range(400):
        if (await jobs.status(submitted.job_id)).status not in {JobStatus.QUEUED,
                                                                 JobStatus.RUNNING}:
            break
        await asyncio.sleep(0.005)
    system = "\n".join(prompts)
    assert ("proyecto «Tesis»" in system) is allowed
    assert ("Cita en APA." in system) is allowed
    assert ("Hipótesis X." in system) is allowed
    await jobs.close()


def _request(identity):
    audio = identity in {"owner_voice", "unknown_voice"}
    return UserRequest(text="Resume el capítulo", modalities=frozenset(
        {InputModality.TEXT, InputModality.AUDIO} if audio else {InputModality.TEXT}),
        metadata=(
            {"speaker_identity": {"id": "synthetic-owner", "confidence": 0.99},
             "owner_speaker_profile": True, "owner_presence_verified": True}
            if identity == "owner_voice" else
            {UNSIGNED_PEER_METADATA: True} if identity == "unsigned" else {}))


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["unknown_voice", "unsigned", "temporary"])
async def test_graph_filters_private_project_even_if_internal_metadata_is_present(identity):
    from aegis_core.contracts import AgentResult
    from aegis_core.orchestration.graph import build_nvidia_swarm_graph as build_swarm_graph

    prompts = []

    class Provider:
        async def complete_nvidia_only(self, **kwargs):
            prompts.append(str(kwargs["messages"]))
            return AgentResult(role=kwargs["role"], model_id="synthetic", content="Listo.")

    request = _request(identity)
    request = request.model_copy(update={"metadata": {
        **request.metadata,
        PROJECT_CONTEXT_METADATA: {"name": "PRIVATE_PROJECT_NAME",
                                   "instructions": "PRIVATE_PROJECT_INSTRUCTIONS",
                                   "knowledge": [{"name": "PRIVATE_FILENAME",
                                                  "text": "PRIVATE_FILE_CONTENT"}]},
        **({TEMPORARY_CONVERSATION_METADATA: True} if identity == "temporary" else {}),
    }})
    await build_swarm_graph(Provider(), inference_nvidia_only=True).ainvoke({"request": request})
    assert prompts
    assert "PRIVATE_" not in "\n".join(prompts)
