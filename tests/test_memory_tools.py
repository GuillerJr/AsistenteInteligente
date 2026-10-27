"""Actual local memory adapters with synthetic approval decisions and private SQLite."""

import asyncio
import json
from contextvars import ContextVar
from dataclasses import asdict, replace

import pytest

from jarvis_core.local_embeddings import EngineError
from jarvis_core.memory import Citation, MemoryError, MemoryScope, MemoryStore
from jarvis_core.memory_tools import MemoryTools
from jarvis_core.tools import ApprovalAuthority, ToolCatalog, ToolError


@pytest.fixture
def bundle(tmp_path):
    path = tmp_path.resolve() / "profile/memory.sqlite3"
    memory = MemoryStore(path)
    scopes = [MemoryScope("jarvis", "workspace")]
    tools = MemoryTools(memory, lambda: scopes[0])
    authority = ApprovalAuthority(ttl=1)
    catalog = ToolCatalog(tools.definitions(), tools.specs(), authority=authority)
    yield memory, scopes, tools, authority, catalog, path
    memory.close()


async def invoke(bundle, name, arguments, *, allow=True, revoke=False):
    _, _, _, authority, catalog, _ = bundle
    task = asyncio.create_task(catalog.execute(name, arguments, session_id="session", run_id="run"))
    async with asyncio.timeout(2):
        while not task.done():
            pending = authority.list_pending()
            if pending:
                assert authority.decide(pending[0].approval_id, allow)
                if revoke:
                    authority.revoke("session")
                break
            await asyncio.sleep(0.001)
        return await task


async def test_authorized_memory_create_recall_citation_update_and_delete(bundle):
    memory, scopes, _, authority, _, _ = bundle
    created = await invoke(bundle, "memory_store", {"key": "control", "text": "NARANJA sintética"})
    assert created.succeeded and created.verified
    record = json.loads(created.output)
    assert record["revision"] == 1 and record["source_verified"] is False
    assert record["source_uri"].startswith("jarvis://")
    recalled = await invoke(bundle, "memory_recall", {"query": "naranja"})
    assert recalled.succeeded and recalled.verified and not authority.list_pending()
    found = json.loads(recalled.output)
    assert found["mode"] in {"fts5_lexical", "lexical_scan"}
    hit = found["hits"][0]
    citation = Citation(**hit["citation"])
    assert memory.resolve_citation(scopes[0], citation) == hit["excerpt"]
    updated = await invoke(bundle, "memory_store", {
        "key": "control", "text": "LIMÓN sintético", "expected_revision": 1,
        "source_uri": "manual://updated",
    })
    assert updated.verified and json.loads(updated.output)["revision"] == 2
    with pytest.raises(MemoryError, match="stale_citation"):
        memory.resolve_citation(scopes[0], citation)
    deleted = await invoke(bundle, "memory_forget", {"key": "control", "expected_revision": 2})
    assert deleted.succeeded and deleted.verified and json.loads(deleted.output)["deleted"]
    assert memory.list_documents(scopes[0]) == ()
    await authority.close()


async def test_denial_and_revocation_never_store_text(bundle):
    memory, scopes, _, authority, _, path = bundle
    denied = await invoke(bundle, "memory_store", {"key": "denied", "text": "no"}, allow=False)
    assert denied.error == "approval_denied" and not path.parent.exists()
    revoked = await invoke(bundle, "memory_store", {"key": "revoked", "text": "no"}, revoke=True)
    assert not revoked.succeeded and not path.parent.exists()
    assert memory.list_documents(scopes[0]) == ()
    await authority.close()


async def test_semantic_engine_error_is_visible_without_lexical_fallback(bundle):
    memory, _scopes, tools, authority, _, path = bundle
    calls = []

    async def unavailable(scope, query, **options):
        calls.append((scope, query, options))
        raise EngineError("unavailable_runtime")

    tools.search = unavailable
    result = await invoke(bundle, "memory_recall", {"query": "auto", "mode": "semantic_local"})
    assert not result.succeeded and result.error == "unavailable_runtime"
    assert result.effect_status == "known" and len(calls) == 1
    assert not path.exists() and memory._disk is None
    await authority.close()


async def test_cas_rejects_missing_or_stale_revision_and_preserves_current_note(bundle):
    memory, scopes, _, authority, _, _ = bundle
    assert (await invoke(bundle, "memory_store", {"key": "note", "text": "original"})).verified
    missing = await invoke(bundle, "memory_store", {"key": "note", "text": "replacement"})
    assert not missing.succeeded and missing.error == "memory_revision_required"
    stale = await invoke(bundle, "memory_store", {
        "key": "note", "text": "replacement", "expected_revision": 2,
    })
    assert not stale.succeeded and memory.get(scopes[0], "note").text == "original"
    forgetting = await invoke(bundle, "memory_forget", {"key": "note", "expected_revision": 2})
    assert not forgetting.succeeded and forgetting.error == "revision_conflict"
    assert memory.get(scopes[0], "note").document.revision == 1
    await authority.close()


async def test_model_arguments_cannot_choose_another_memory_namespace(bundle):
    memory, scopes, _, authority, _, _ = bundle
    own = await invoke(bundle, "memory_store", {"key": "note", "text": "own namespace"})
    assert own.verified
    for extra in ("agent_id", "workspace_id", "session_id", "temporary", "scope"):
        result = await invoke(bundle, "memory_recall", {"query": "own", extra: "foreign"})
        assert not result.succeeded and result.error == "invalid_tool_arguments"
        assert not authority.list_pending()
    scopes[0] = replace(scopes[0], workspace_id="other")
    foreign = await invoke(bundle, "memory_recall", {"query": "own"})
    assert foreign.verified and json.loads(foreign.output)["hits"] == []
    assert memory.list_documents(scopes[0]) == ()
    await authority.close()


async def test_temporary_memory_tool_scope_is_ram_only_and_disappears_on_close(bundle):
    memory, scopes, _, authority, _, path = bundle
    scopes[0] = replace(scopes[0], session_id="temporary", temporary=True)
    result = await invoke(bundle, "memory_store", {"key": "temp", "text": "NARANJA temporal"})
    assert result.verified and not path.parent.exists()
    recalled = await invoke(bundle, "memory_recall", {"query": "naranja"})
    assert recalled.verified and json.loads(recalled.output)["hits"]
    assert not path.parent.exists()
    scope = scopes[0]
    memory.close()
    with MemoryStore(path) as restarted:
        assert not restarted.search(scope, "naranja").hits
    assert not path.parent.exists()
    await authority.close()


async def test_mutator_callback_cannot_execute_without_contextual_authority(bundle):
    memory, scopes, tools, _, _, path = bundle
    tool = next(tool for tool in tools.definitions() if tool.name == "memory_store")
    with pytest.raises(ToolError, match="required"):
        await tool.execute({"key": "no", "text": "no"}, asyncio.Event())
    assert not path.parent.exists()
    assert memory.list_documents(scopes[0]) == ()


async def test_cancel_during_memory_approval_cannot_create_document(bundle):
    _, _, _, authority, catalog, path = bundle
    cancelled = asyncio.Event()
    task = asyncio.create_task(catalog.execute(
        "memory_store", {"key": "cancelled", "text": "no"}, session_id="session", run_id="run",
        cancellation=cancelled,
    ))
    async with asyncio.timeout(2):
        while not authority.list_pending():
            await asyncio.sleep(0.001)
    pending = authority.list_pending()[0]
    cancelled.set()
    result = await asyncio.wait_for(task, 2)
    assert not result.succeeded and not path.parent.exists()
    assert not authority.decide(pending.approval_id, True)
    await authority.close()


@pytest.mark.parametrize("arguments", [
    {"key": ".", "text": "no"}, {"key": "..", "text": "no"},
    {"key": "../outside", "text": "no"}, {"key": "note", "text": ""},
    {"key": "note", "text": "x" * 8001}, {"key": "note", "text": "no", "expected_revision": True},
])
async def test_invalid_memory_mutations_fail_before_any_ticket_or_io(bundle, arguments):
    _, _, _, authority, _, path = bundle
    result = await invoke(bundle, "memory_store", arguments)
    assert not result.succeeded and result.error == "invalid_tool_arguments"
    assert not authority.list_pending() and not path.parent.exists()
    await authority.close()


async def test_citation_payload_is_a_byte_snapshot_not_a_claim_of_external_source(bundle):
    memory, scopes, _, authority, _, _ = bundle
    result = await invoke(bundle, "memory_store", {
        "key": "source", "text": "La palabra sintética es NARANJA.",
        "source_uri": "https://example.invalid/claim",
    })
    assert result.verified
    output = json.loads((await invoke(bundle, "memory_recall", {"query": "naranja"})).output)
    hit = output["hits"][0]
    assert hit["document"]["source_verified"] is False
    assert hit["document"]["provenance"] == "provided_text"
    citation = Citation(**hit["citation"])
    assert asdict(citation) == hit["citation"]
    assert memory.resolve_citation(scopes[0], citation) == hit["excerpt"]
    await authority.close()


async def test_overlapping_host_contexts_cannot_mix_memory_namespaces(bundle):
    memory, _, _, authority, _, _ = bundle
    active = ContextVar("synthetic_memory_scope")
    tools = MemoryTools(memory, active.get)
    catalog = ToolCatalog(tools.definitions(), tools.specs(), authority=authority)
    first, second = MemoryScope("jarvis", "first"), MemoryScope("jarvis", "second")
    active.set(first)
    one = asyncio.create_task(catalog.execute(
        "memory_store", {"key": "same", "text": "NARANJA"}, session_id="one", run_id="one",
    ))
    active.set(second)
    two = asyncio.create_task(catalog.execute(
        "memory_store", {"key": "same", "text": "LIMON"}, session_id="two", run_id="two",
    ))
    async with asyncio.timeout(2):
        while len(authority.list_pending()) < 2:
            await asyncio.sleep(0.001)
    for pending in authority.list_pending():
        assert authority.decide(pending.approval_id, True)
    outcomes = await asyncio.gather(one, two)
    assert all(result.verified for result in outcomes)
    assert memory.get(first, "same").text == "NARANJA"
    assert memory.get(second, "same").text == "LIMON"
    assert not memory.search(first, "limon").hits
    assert not memory.search(second, "naranja").hits
    await authority.close()


async def test_failed_reread_after_store_preserves_unknown_effect(bundle, monkeypatch):
    memory, scopes, _, authority, _, _ = bundle
    original = memory.get
    count = 0

    def get(scope, key):
        nonlocal count
        count += 1
        if count == 2:
            raise MemoryError("memory_unavailable")
        return original(scope, key)

    monkeypatch.setattr(memory, "get", get)
    result = await invoke(bundle, "memory_store", {"key": "written", "text": "written once"})
    assert not result.succeeded and not result.verified and result.effect_status == "unknown"
    assert original(scopes[0], "written").text == "written once"
    await authority.close()


async def test_failed_verification_after_delete_preserves_unknown_effect(bundle, monkeypatch):
    memory, scopes, _, authority, _, _ = bundle
    assert (await invoke(bundle, "memory_store", {"key": "deleted", "text": "original"})).verified
    original = memory.get

    def unavailable(*args):
        raise MemoryError("memory_unavailable")

    monkeypatch.setattr(memory, "get", unavailable)
    result = await invoke(bundle, "memory_forget", {"key": "deleted", "expected_revision": 1})
    assert not result.succeeded and not result.verified and result.effect_status == "unknown"
    with pytest.raises(MemoryError, match="document_not_found"):
        original(scopes[0], "deleted")
    await authority.close()


async def test_lost_store_acknowledgment_is_not_a_known_failure_or_retried(bundle, monkeypatch):
    memory, scopes, _, authority, _, _ = bundle
    original = memory.upsert
    calls = 0

    def lost_ack(*args, **kwargs):
        nonlocal calls
        calls += 1
        original(*args, **kwargs)
        raise MemoryError("memory_unavailable")

    monkeypatch.setattr(memory, "upsert", lost_ack)
    result = await invoke(bundle, "memory_store", {"key": "once", "text": "only once"})
    assert not result.succeeded and result.effect_status == "unknown" and calls == 1
    assert memory.get(scopes[0], "once").document.revision == 1
    await authority.close()
