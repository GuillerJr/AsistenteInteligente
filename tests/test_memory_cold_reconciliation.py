"""Actual cold WAL families and SQL copies on own disconnected restored profiles."""

import asyncio
import importlib.util
import json
import shutil
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from test_memory_management import NoSdk, decide, finish, review

from jarvis_core.memory import MemoryScope
from jarvis_core.service import JarvisService

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_memory_fixture", Path(__file__).resolve().parents[1]
    / "script/prepare_memory_reconciliation_fixture.py")
fixture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fixture)


@pytest.fixture
async def cold():
    parent = Path(tempfile.mkdtemp(prefix="jarvis-memory-", dir=fixture.TEMP))
    value = fixture.prepare(parent)
    provider = NoSdk()
    service = JarvisService(Path(value["profile"]), provider)
    try:
        yield service, service.maintenance_coordinator, provider, parent, value
    finally:
        await service.aclose()
        shutil.rmtree(parent)


def copy_params(prepared):
    return {key: prepared[key] for key in ("review_id", "review_digest")}


async def pending(coordinator):
    async with asyncio.timeout(2):
        while not (rows := (await coordinator.pending({}))["pending"]):
            await asyncio.sleep(.001)
    return rows[0]


async def test_wal_readonly_inventory_review_preserve_every_original_physical_member(cold):
    service, coordinator, provider, parent, value = cold
    before = fixture.physical_family(service.directory)
    listing, prepared = await review(coordinator)
    assert listing["source_kind"] == "cold_wal_snapshot"
    assert listing["sources"][0]["documents"] == 3
    assert listing["session_bound_scopes_excluded"] == 1
    assert prepared["metadata"]["source_kind"] == "cold_wal_snapshot"
    assert prepared["metadata"]["cold_sqlite_recovery_required"] is True
    assert service.memory._disk is None and provider.calls == 0
    assert fixture.physical_family(service.directory) == before == value["source_family"]
    assert not (await coordinator.pending({}))["pending"]
    observed = fixture.observe(parent)
    assert observed["source_documents_intact"] and not observed["receipts"]
    payload = json.dumps([listing, prepared])
    assert "NOTA CONFIRMADA" not in payload and "file:///" not in payload


async def test_wal_copy_retains_originals_and_provenance_then_observes_without_replay(cold):
    service, coordinator, provider, parent, _ = cold
    _, prepared = await review(coordinator)
    operation = await coordinator.memory_copy(copy_params(prepared))
    card = await decide(coordinator)
    assert json.loads(card["arguments_json"])["review"] == prepared["metadata"]
    result = await finish(coordinator, operation)
    assert result["phase"] == "complete", result
    assert result["receipt"]["effect_status"] == "scope_copy_committed"
    assert result["receipt"]["document_count"] == 3 and result["receipt_durable"]
    assert not result["cleanup_owner_pending"] and service.profile_gate._cut is None
    assert len(service.memory.list_documents(fixture.SOURCE)) == 3
    destination = MemoryScope("jarvis", service._workspace_id)
    assert len(service.memory.list_documents(destination)) == 3
    original = service.memory.get(fixture.SOURCE, "wal")
    copied = service.memory.get(destination, "wal")
    assert original.text == copied.text == "NOTA CONFIRMADA EN WAL Ω 6421"
    assert original.document.document_id != copied.document.document_id
    observed = fixture.observe(parent)
    assert observed["source_documents_intact"] and observed["current_copies_match"]
    assert len(observed["receipts"]) == 1
    assert len(observed["mappings"]) == 3 and not observed["original_physical_family_unchanged"]
    lookup = await finish(coordinator, await coordinator.memory_receipt({
        "operation_id": result["operation_id"], "review_digest": prepared["review_digest"]}))
    assert lookup["result"]["observation"]["current_documents_match"]
    assert lookup["result"]["permits_replay"] is False
    assert (await coordinator.memory_copy(copy_params(prepared)))["operation_id"] == result[
        "operation_id"]
    assert len(fixture.observe(parent)["receipts"]) == 1 and provider.calls == 0


@pytest.mark.parametrize("decision", ["deny", "cancel", "expire"])
async def test_wal_negative_before_authority_never_opens_or_changes_any_source(
        cold, decision, monkeypatch):
    service, coordinator, provider, parent, value = cold
    clock = [coordinator.clock()]
    if decision == "expire":
        monkeypatch.setattr(coordinator, "clock", lambda: clock[0])
    _, prepared = await review(coordinator)
    if decision == "expire":
        current = coordinator._reviews[prepared["review_id"]]
    operation = await coordinator.memory_copy(copy_params(prepared))
    card = await pending(coordinator)
    if decision == "deny":
        await decide(coordinator, False)
    elif decision == "cancel":
        await coordinator.cancel({"operation_id": operation["operation_id"]})
    else:
        # Publish the actual authority ticket first, then expire that exact
        # ticket and its review. A 100ms wall-clock race could hide the card
        # before pending() observed it on a busy CI runner.
        clock[0] = current.expires + 1
        ticket = coordinator.authority._host_pending[card["approval_id"]]
        assert ticket.operation.operation_id == operation["operation_id"]
        ticket.deadline = 0
        assert (await coordinator.pending({}))["pending"] == []
    result = await finish(coordinator, operation)
    assert result["phase"] == {"deny": "denied", "cancel": "cancelled", "expire": "failed"}[
        decision], result
    assert result["receipt"] is None and service.memory._disk is None
    if decision == "expire":
        assert result["error_code"] == "memory_restore_review_expired"
    assert fixture.physical_family(service.directory) == value["source_family"]
    assert fixture.observe(parent)["receipts"] == [] and provider.calls == 0


@pytest.mark.parametrize("mutation", ["wal_bytes", "wal_inode", "new_journal"])
async def test_wal_mutation_while_approval_is_visible_invalidates_the_exact_cut(cold, mutation):
    service, coordinator, provider, parent, _ = cold
    _, prepared = await review(coordinator)
    operation = await coordinator.memory_copy(copy_params(prepared))
    await pending(coordinator)
    path = service.directory / "memory.sqlite3-wal"
    if mutation == "wal_bytes":
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
    elif mutation == "wal_inode":
        raw = path.read_bytes()
        replacement = parent / "replacement"
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        replacement.replace(path)
    else:
        path = service.directory / "memory.sqlite3-journal"
        path.write_bytes(b"AMBIGUOUS OWN TEST JOURNAL")
        path.chmod(0o600)
    after_mutation = fixture.physical_family(service.directory)
    await decide(coordinator)
    result = await finish(coordinator, operation)
    assert result["phase"] == "failed" and result["receipt"] is None, result
    assert service.memory._disk is None
    assert fixture.physical_family(service.directory) == after_mutation
    assert provider.calls == 0


@pytest.mark.parametrize("damage", ["truncated", "checksum", "mixed_journal"])
async def test_ambiguous_or_damaged_wal_is_rejected_before_any_approval_or_owned_open(cold, damage):
    service, coordinator, provider, _, _ = cold
    path = service.directory / "memory.sqlite3-wal"
    raw = bytearray(path.read_bytes())
    if damage == "truncated":
        path.write_bytes(raw[:-1])
    elif damage == "checksum":
        raw[-1] ^= 1
        path.write_bytes(raw)
    else:
        path = service.directory / "memory.sqlite3-journal"
        path.write_bytes(bytes(28))
        path.chmod(0o600)
    before = fixture.physical_family(service.directory)
    result = await finish(coordinator, await coordinator.memory_inventory({"agent_id": "jarvis"}))
    assert result["phase"] == "failed", result
    assert not (await coordinator.pending({}))["pending"] and service.memory._disk is None
    assert fixture.physical_family(service.directory) == before and provider.calls == 0


async def test_explicit_wal_open_then_cancel_before_commit_preserves_notes_and_no_sql_copy(
    cold, monkeypatch,
):
    from jarvis_core.memory_host_cut import MemoryMutationCut

    service, coordinator, provider, parent, _ = cold
    _, prepared = await review(coordinator)
    real_execute = MemoryMutationCut.execute

    def execute(mutation, context, **kwargs):
        old = context.check_current

        def cancel(stage, write):
            old(stage, write)
            if stage == "memory_copy_document":
                context.cancellation.set()
        return real_execute(mutation, replace(context, check_current=cancel), **kwargs)

    monkeypatch.setattr(MemoryMutationCut, "execute", execute)
    operation = await coordinator.memory_copy(copy_params(prepared))
    await decide(coordinator)
    result = await finish(coordinator, operation)
    assert result["phase"] == "cancelled", result
    assert result["receipt"]["effect_status"] == "no_scope_copy_committed"
    observed = fixture.observe(parent)
    assert observed["source_documents_intact"] and not observed["receipts"]
    assert not service.memory.list_documents(MemoryScope("jarvis", service._workspace_id))
    assert service.memory._disk.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not service.memory._disk.in_transaction and service.profile_gate._cut is None
    assert provider.calls == 0


async def test_commit_ack_loss_on_wal_copy_keeps_unknown_but_sql_observation_never_retries(
    cold, monkeypatch,
):
    from jarvis_core.memory import MemoryError
    from jarvis_core.memory_host_cut import MemoryMutationCut

    service, coordinator, provider, parent, _ = cold
    _, prepared = await review(coordinator)
    real_execute = MemoryMutationCut.execute

    def execute(mutation, context, **kwargs):
        old = context.check_current

        def lose_ack(stage, write):
            old(stage, write)
            if stage == "memory_copy_committed":
                raise MemoryError("memory_copy_fixture_ack_lost")
        return real_execute(mutation, replace(context, check_current=lose_ack), **kwargs)

    monkeypatch.setattr(MemoryMutationCut, "execute", execute)
    operation = await coordinator.memory_copy(copy_params(prepared))
    await decide(coordinator)
    result = await finish(coordinator, operation)
    assert result["phase"] == "publication_unknown", result
    receipt = result["receipt"].copy()
    assert receipt["status"] == "unknown" and result["receipt_durable"]
    assert service.profile_gate._cut is None and provider.calls == 0
    observed = fixture.observe(parent)
    assert observed["source_documents_intact"] and len(observed["receipts"]) == 1
    lookup = await finish(coordinator, await coordinator.memory_receipt({
        "operation_id": result["operation_id"], "review_digest": prepared["review_digest"]}))
    assert lookup["result"]["observation"]["current_documents_match"]
    assert lookup["result"]["observation"]["prior_acknowledgement"] == "not_established"
    assert not lookup["result"]["permits_replay"]
    previous = await coordinator.memory_copy(copy_params(prepared))
    assert previous["operation_id"] == result["operation_id"] and previous["receipt"] == receipt
    assert len(fixture.observe(parent)["receipts"]) == 1


@pytest.mark.parametrize("mode", ["wal_absent", "wal_empty", "wal_header", "journal_hot",
                                  "journal_inactive"])
async def test_other_valid_cold_families_copy_only_after_review_and_exact_authority(mode):
    parent = Path(tempfile.mkdtemp(prefix="jarvis-memory-", dir=fixture.TEMP))
    family_mode = "wal" if mode.startswith("wal_") else mode
    value = fixture.prepare(parent, mode=family_mode)
    root = Path(value["profile"])
    if mode.startswith("wal_"):
        path = root / "memory.sqlite3-wal"
        header = path.read_bytes()[:32]
        db = sqlite3.connect(root / "memory.sqlite3", isolation_level=None)
        try:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            db.close()
        if mode != "wal_absent":
            path.write_bytes(header if mode == "wal_header" else b"")
            path.chmod(0o600)
        for suffix in ("-shm",):
            (root / ("memory.sqlite3" + suffix)).unlink(missing_ok=True)
    provider = NoSdk()
    service = JarvisService(root, provider)
    coordinator = service.maintenance_coordinator
    try:
        before = fixture.physical_family(root)
        listing, prepared = await review(coordinator)
        kind = "cold_wal_snapshot" if mode.startswith("wal_") else "cold_journal_snapshot"
        assert listing["source_kind"] == prepared["metadata"]["source_kind"] == kind
        assert prepared["metadata"]["cold_sqlite_recovery_required"]
        assert service.memory._disk is None and fixture.physical_family(root) == before
        operation = await coordinator.memory_copy(copy_params(prepared))
        await pending(coordinator)
        assert service.memory._disk is None and fixture.physical_family(root) == before
        await decide(coordinator)
        result = await finish(coordinator, operation)
        assert result["phase"] == "complete", result
        observed = fixture.observe(parent)
        assert observed["source_documents_intact"] and observed["current_copies_match"]
        assert len(observed["receipts"]) == 1
        assert result["receipt"]["document_count"] == (3 if mode.startswith("wal_") else 2)
        assert provider.calls == 0
    finally:
        await service.aclose()
        shutil.rmtree(parent)


@pytest.mark.parametrize("stage", ["memory_copy_document", "memory_copy_committed"])
async def test_wal_uncertain_cleanup_retains_same_cut_until_explicit_settlement(
    cold, monkeypatch, stage,
):
    from jarvis_core.memory import MemoryCopyOwner, MemoryError
    from jarvis_core.memory_host_cut import MemoryMutationCut
    from jarvis_core.protocol import RpcError

    service, coordinator, provider, parent, _ = cold
    _, prepared = await review(coordinator)
    real_guard = MemoryMutationCut.guard
    real_cleanup = MemoryCopyOwner.cleanup
    cleanup_allowed = False

    def guard(mutation, current, write=False):
        real_guard(mutation, current, write)
        if current == stage:
            raise MemoryError("memory_copy_fixture_interrupted")

    def cleanup(owner):
        if not cleanup_allowed:
            raise MemoryError("memory_copy_cleanup_incomplete")
        return real_cleanup(owner)

    monkeypatch.setattr(MemoryMutationCut, "guard", guard)
    monkeypatch.setattr(MemoryCopyOwner, "cleanup", cleanup)
    try:
        operation = await coordinator.memory_copy(copy_params(prepared))
        await decide(coordinator)
        result = await finish(coordinator, operation)
        assert result["phase"] == "cleanup_pending" and result["cleanup_owner_pending"], result
        original_receipt = result["receipt"].copy()
        live = coordinator._operations[result["operation_id"]]
        mutation, cut = live.memory_cut, service.profile_gate._cut
        owner, connection = mutation.owner, service.memory._disk
        assert cut is mutation.cut and owner.connection is connection and not owner.closed
        with pytest.raises(RpcError):
            await service.handlers()["memory.list"]({})
        previous = await coordinator.memory_copy(copy_params(prepared))
        assert previous["operation_id"] == result["operation_id"]
        cleanup_allowed = True
        result = await finish(coordinator, await coordinator.cleanup({
            "operation_id": result["operation_id"]}))
        assert result["phase"] == "publication_unknown" and not result["cleanup_owner_pending"]
        assert result["receipt"] == original_receipt and original_receipt["status"] == "unknown"
        assert mutation.cut is cut and owner.connection is connection and owner.closed
        assert service.profile_gate._cut is None and mutation.released
        observed = fixture.observe(parent)
        assert observed["source_documents_intact"]
        assert len(observed["receipts"]) == int(stage == "memory_copy_committed")
        assert provider.calls == 0
    finally:
        cleanup_allowed = True
        for operation in coordinator._operations.values():
            if operation.cleanup_owner is not None:
                await finish(coordinator, await coordinator.cleanup({
                    "operation_id": operation.operation_id}))


def test_fixture_is_not_a_general_profile_reader_or_initializer(tmp_path):
    with pytest.raises(ValueError, match="fixture_fresh_temporary_root_required"):
        fixture.prepare(tmp_path)
    root = Path(tempfile.mkdtemp(prefix="jarvis-memory-", dir=fixture.TEMP))
    try:
        (root / "unrelated").write_text("DO NOT OVERWRITE")
        with pytest.raises(ValueError, match="fixture_root_must_be_empty"):
            fixture.prepare(root)
        assert (root / "unrelated").read_text() == "DO NOT OVERWRITE"
    finally:
        shutil.rmtree(root)


def test_fixture_delete_mode_has_same_logical_notes_without_wal():
    root = Path(tempfile.mkdtemp(prefix="jarvis-memory-", dir=fixture.TEMP))
    try:
        value = fixture.prepare(root, mode="delete")
        assert value["source_family"]["memory.sqlite3-wal"] is None
        with sqlite3.connect(":memory:") as db:
            db.deserialize((root / "profile/memory.sqlite3").read_bytes())
            assert len(db.execute("SELECT * FROM documents").fetchall()) == 3
        assert fixture.observe(root)["source_documents_intact"]
    finally:
        shutil.rmtree(root)
