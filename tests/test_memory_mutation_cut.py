"""Real core SQL and sole-authority tests on private synthetic service profiles."""
import asyncio
import hashlib
import time
from dataclasses import replace
from uuid import uuid4

import pytest

from jarvis_core.doctor import DoctorContext
from jarvis_core.host_profile import HostProfileError, freeze_sources
from jarvis_core.memory import (
    MemoryCopyContext,
    MemoryCopyOwner,
    MemoryError,
    MemoryScope,
    MemoryStore,
    review_memory_scope_copy,
)
from jarvis_core.memory_host_cut import MemoryMutationCut
from jarvis_core.protocol import RpcError
from jarvis_core.recovery import RecoveryLimits, canonical
from jarvis_core.service import JarvisService
from jarvis_core.tools import (
    HostApprovalRequest,
    HostCancellation,
    HostPurpose,
    canonical_host_arguments,
    host_operation_digest,
)


class NoSdk:
    async def aclose(self):
        pass


@pytest.fixture(params=[False, True], ids=['owned', 'cold'])
async def prepared(tmp_path, request):
    root = tmp_path / 'profile'
    root.mkdir(mode=0o700)
    source = MemoryScope('jarvis', 'f'*64)
    if request.param:
        with MemoryStore(root / 'memory.sqlite3') as seed:
            seed.upsert(source, 'first', 'FARO 901 áé', source_uri='file:///DO-NOT-OPEN')
            seed.upsert(source, 'second', 'TOPACIO 4826', source_uri='jarvis://fixture')
    service = JarvisService(root, NoSdk())
    try:
        if not request.param:
            service.memory.upsert(source, 'first', 'FARO 901 áé', source_uri='file:///DO-NOT-OPEN')
            service.memory.upsert(source, 'second', 'TOPACIO 4826', source_uri='jarvis://fixture')
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            context = DoctorContext(lambda stage, write=False: frozen.assert_current(cut),
                                    HostCancellation())
            images = frozen.doctor_images(cut, context, RecoveryLimits())
            image = next(row for row in images.images if row.role == 'memory')
            row = next(row for row in frozen._sql if row.role == 'memory')
            review = review_memory_scope_copy(
                image.data, source, MemoryScope('jarvis', service._workspace_id),
                database_identity=tuple(row.fingerprint[1:3]),
                check_current=lambda: frozen.assert_current(cut), limits=service.memory.limits)
        metadata = {
            'kind':'memory.restore.copy_scope','profile_id':frozen.scope.profile_id,
            'generation':frozen.scope.generation,'agent_id':'jarvis', 'agent_revision':'1',
            'root_identity':{'device':str(frozen.root_identity.device),
                             'inode':str(frozen.root_identity.inode)},
            'destination_workspace_identity':{'device':str(service.workspace_tools.identity[0]),
                                              'inode':str(service.workspace_tools.identity[1])},
            'destination_scope_present':review.destination.exists,
            'destination_generation':str(review.destination.generation)
            if review.destination.exists else None,
            'schema_before':review.schema_before,'schema_after':review.schema_after,
            'migration_required':review.migration_required,
            'database_identity':{'device':str(review.database_identity[0]),
                                 'inode':str(review.database_identity[1])},
            'core_review_digest':review.digest,
            'destination_workspace_path':str(service._workspace_path),
            'source_generation':str(review.source.generation),
            'source_fingerprint':review.source.fingerprint,
            'source_documents':review.source.document_count,'source_bytes':review.source.byte_length,
        }
        digest = hashlib.sha256(canonical(metadata)).hexdigest()
        approval = HostApprovalRequest(
            frozen.scope,HostPurpose.MEMORY_RESTORE_COPY_SCOPE,uuid4().hex,
            canonical_host_arguments({'review_id':uuid4().hex,'review_digest':digest,
                                      'review':metadata}),'')
        approval = replace(approval,digest=host_operation_digest(approval))
        authority = service.authority
        authority.bind_host_scope(frozen.scope)
        pending = asyncio.create_task(authority.request_host(approval,HostCancellation()))
        async with asyncio.timeout(2):
            while not (rows := authority.pending_host(frozen.scope)):
                await asyncio.sleep(0)
        assert authority.decide_host(rows[0].approval_id,scope=frozen.scope,
                                      digest=approval.digest,allow=True)
        grant = await pending
        yield service,frozen,review,approval,grant,digest
    finally:
        # Test failures must never wait indefinitely on their private retained cut.
        cut = service.profile_gate._cut
        if cut is not None:
            raise AssertionError('test left its private memory cut retained')
        await service.aclose()


async def test_same_cut_accepts_its_own_memory_writes_but_generation_waits_until_release(prepared):
    service,frozen,review,approval,grant,digest = prepared
    before = service.profile_lease.scope
    cancellation = HostCancellation()
    mutation = None
    try:
        with service.authority.execution_host(approval,grant):
            mutation = MemoryMutationCut(service,frozen,approval,review)
            context = MemoryCopyContext(approval.operation_id,digest,review.digest,
                                        mutation.guard,cancellation,time.monotonic()+20)
            outcome = await service.profile_gate.run_worker(
                mutation.cut,lambda:mutation.execute(context),cancellation)
            assert outcome.error is None, repr(outcome.error)
            assert outcome.value.status == 'completed'
            assert outcome.value.effect_status == 'scope_copy_committed'
            assert service.profile_lease.scope == before
            assert mutation.connection is service.memory._disk
            assert service.profile_gate._cut is mutation.cut
        mutation.release()
        assert service.profile_gate._cut is None
        assert service.profile_lease.scope.generation == before.generation+1
        mutation.release()
        assert service.profile_lease.scope.generation == before.generation+1
        assert len(service.memory.list_documents(review.source.scope)) == 2
        assert len(service.memory.list_documents(review.destination.scope)) == 2
    finally:
        if mutation is not None and not mutation.released:
            mutation.release()


async def test_sibling_guard_remains_live_after_first_authorized_memory_write(prepared):
    service,frozen,review,approval,grant,digest = prepared
    cancellation = HostCancellation()
    mutation = None
    changed = False
    try:
        with service.authority.execution_host(approval,grant):
            mutation = MemoryMutationCut(service,frozen,approval,review)
            def guard(stage, write=False):
                nonlocal changed
                if stage == 'memory_copy_document' and not changed:
                    changed = True
                    # Adversarial raw mutation, intentionally outside the profile gate.
                    service.store._resource.create_session('SIBLING FIXTURE CHANGE')
                mutation.guard(stage,write)
            context = MemoryCopyContext(approval.operation_id,digest,review.digest,
                                        guard,cancellation,time.monotonic()+20)
            outcome = await service.profile_gate.run_worker(
                mutation.cut,lambda:mutation.execute(context),cancellation)
            assert outcome.error is not None
            assert outcome.error.receipt.effect_status == 'no_scope_copy_committed'
            assert getattr(outcome.error,'owner',None) is None
            assert changed
        mutation.release()
        assert not service.memory.list_documents(review.destination.scope)
        assert len(service.memory.list_documents(review.source.scope)) == 2
        assert service.memory._disk.execute('PRAGMA user_version').fetchone()[0] == 1
    finally:
        if mutation is not None and not mutation.released:
            mutation.release()


async def test_cleanup_from_another_task_keeps_exact_cut_without_old_authority_context(
        prepared,monkeypatch):
    service,frozen,review,approval,grant,digest = prepared
    cancellation = HostCancellation()
    mutation = None
    cleanup_allowed = False
    original_cleanup = MemoryCopyOwner.cleanup
    def cleanup(owner):
        if not cleanup_allowed:
            raise MemoryError('memory_copy_cleanup_incomplete')
        return original_cleanup(owner)
    monkeypatch.setattr(MemoryCopyOwner,'cleanup',cleanup)
    try:
        with service.authority.execution_host(approval,grant):
            mutation = MemoryMutationCut(service,frozen,approval,review)
            def guard(stage,write=False):
                mutation.guard(stage,write)
                if stage == 'memory_copy_document':
                    raise MemoryError('memory_copy_fixture_failure')
            context = MemoryCopyContext(approval.operation_id,digest,review.digest,
                                        guard,cancellation,time.monotonic()+20)
            outcome = await service.profile_gate.run_worker(
                mutation.cut,lambda:mutation.execute(context),cancellation)
            assert outcome.error is not None and outcome.error.owner is not None
            original_receipt = outcome.error.receipt
            assert original_receipt.status == 'unknown' and original_receipt.cleanup_pending
            mutation.retain(outcome.error.owner)
            held_cut = mutation.cut
            before = service.profile_lease.scope
        # The execution_host token has been reset in its ORIGINAL Task already.
        assert service.profile_gate._cut is held_cut
        with pytest.raises(HostProfileError,match='profile_memory_cleanup_pending'):
            mutation.release()
        with pytest.raises(RpcError):
            await service.handlers()['memory.list']({})
        cleanup_allowed = True
        async def another_rpc_task():
            outcome = await service.profile_gate.run_worker(
                held_cut,mutation.cleanup,cancellation)
            assert outcome.error is None
            mutation.release()
        await asyncio.create_task(another_rpc_task())
        assert service.profile_gate._cut is None and mutation.owner.closed
        assert service.profile_lease.scope.generation == before.generation+1
        assert original_receipt.status == 'unknown' and original_receipt.cleanup_pending
        assert not service.memory.list_documents(review.destination.scope)
    finally:
        cleanup_allowed = True
        if mutation is not None and not mutation.released:
            if mutation.owner is not None:
                mutation.cleanup()
            mutation.release()


@pytest.mark.parametrize('unsupported', [False, True], ids=['v2', 'v3_rejected'])
async def test_restart_cold_memory_uses_exact_ram_inspector_without_header_acceptance_change(
        prepared, monkeypatch, unsupported):
    import os
    import sqlite3

    from jarvis_core.cold_sqlite import inspect_cold_family
    from jarvis_core.recovery import RecoveryContext
    from jarvis_core.recovery_sqlite import SnapshotError

    service, frozen, review, approval, grant, digest = prepared
    cancellation = HostCancellation()
    mutation = None
    try:
        with service.authority.execution_host(approval, grant):
            mutation = MemoryMutationCut(service, frozen, approval, review)
            context = MemoryCopyContext(approval.operation_id, digest, review.digest,
                                        mutation.guard, cancellation, time.monotonic()+20)
            outcome = await service.profile_gate.run_worker(
                mutation.cut, lambda: mutation.execute(context), cancellation)
            assert outcome.error is None, repr(outcome.error)
        mutation.release()
    finally:
        if mutation is not None and not mutation.released:
            mutation.release()
    root = service.directory
    await service.aclose()
    if unsupported:
        # Only this closed synthetic source is edited to construct unsupported v3.
        connection = sqlite3.connect(root/'memory.sqlite3', isolation_level=None)
        connection.execute('PRAGMA user_version=3')
        connection.close()
    reopened = JarvisService(root, NoSdk())
    path = root/'memory.sqlite3'
    before = (path.read_bytes(),path.stat(),set(os.listdir(root)))
    real_connect = sqlite3.connect
    def ram_only(database, *args, **kwargs):
        assert database == ':memory:', 'readonly cold path opened a source through SQLite'
        return real_connect(database,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',ram_only)
    try:
        assert reopened.memory._disk is None
        # Classifier remains generic; no redundant early version allowlist added.
        family = inspect_cold_family(path,'memory')
        assert family.eligible
        with reopened.profile_gate.exclusive() as cut:
            frozen = freeze_sources(reopened,cut)
            context = RecoveryContext(uuid4().hex,cut.scope.profile_id,cut.scope.generation,
                                      lambda stage, write=False: frozen.assert_current(cut),
                                      HostCancellation(),time.monotonic()+20)
            def capture():
                return family.read_image(context,RecoveryLimits(),
                                         lambda: frozen.assert_current(cut))
            if unsupported:
                with pytest.raises(SnapshotError,match='snapshot_schema_unsupported'):
                    capture()
            else:
                image, _ = capture()
                assert int.from_bytes(image[60:64],'big') == 2
        assert reopened.memory._disk is None
        after = path.stat()
        assert path.read_bytes() == before[0]
        assert (after.st_dev,after.st_ino,after.st_mode,after.st_mtime_ns,after.st_ctime_ns) == (
            before[1].st_dev,before[1].st_ino,before[1].st_mode,
            before[1].st_mtime_ns,before[1].st_ctime_ns)
        assert set(os.listdir(root)) == before[2]
    finally:
        await reopened.aclose()
