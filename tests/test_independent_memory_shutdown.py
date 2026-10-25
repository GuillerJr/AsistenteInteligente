"""Own shutdown fixture; no owner app/provider/account/network."""
import time

import pytest
from test_memory_mutation_cut import prepared as prepared

from jarvis_core.maintenance import _Operation
from jarvis_core.memory import MemoryCopyContext, MemoryCopyOwner, MemoryError
from jarvis_core.memory_host_cut import MemoryMutationCut
from jarvis_core.memory_management import _receipt
from jarvis_core.tools import HostCancellation, ToolError


@pytest.mark.parametrize("fail_first_close", [False, True])
async def test_shutdown_reuses_exact_retained_cut_and_no_expired_host_token(
    prepared, monkeypatch, fail_first_close,
):
    service, frozen, review, approval, grant, digest = prepared
    cancellation = HostCancellation()
    allowed = False
    cleanup_calls = []
    original = MemoryCopyOwner.cleanup

    def cleanup(owner):
        if not allowed:
            raise MemoryError("memory_copy_cleanup_incomplete")
        # A shutdown-owned Task must not carry the original authority lease.
        with pytest.raises(ToolError, match="host_authority_required"):
            service.authority.revalidate_committed_host(approval)
        cleanup_calls.append((owner, service.profile_gate._cut, owner.connection))
        return original(owner)

    monkeypatch.setattr(MemoryCopyOwner, "cleanup", cleanup)
    with service.authority.execution_host(approval, grant):
        mutation = MemoryMutationCut(service, frozen, approval, review)

        def guard(stage, write=False):
            mutation.guard(stage, write)
            if stage == "memory_copy_document":
                raise MemoryError("memory_copy_fixture_failure")

        context = MemoryCopyContext(approval.operation_id, digest, review.digest, guard,
                                    cancellation, time.monotonic() + 20)
        outcome = await service.profile_gate.run_worker(
            mutation.cut, lambda: mutation.execute(context), cancellation)
        assert outcome.error is not None and outcome.error.owner is not None
        owner = outcome.error.owner
        mutation.retain(owner)
        receipt = _receipt(outcome.error.receipt)
    held_cut, connection = mutation.cut, owner.connection
    operation = _Operation(approval.operation_id, "memory.restore.copy_scope", frozen.scope,
                           phase="cleanup_pending", receipt=receipt,
                           review_digest=digest, cleanup_owner=mutation, memory_cut=mutation)
    service.maintenance_coordinator._operations[operation.operation_id] = operation
    if fail_first_close:
        with pytest.raises(TimeoutError, match="maintenance_cleanup_incomplete"):
            await service.aclose()
        assert service.profile_gate._cut is held_cut and not owner.closed
        assert service.profile_lease._fd >= 0 and service.memory._disk is connection
        assert operation.cleanup_owner is mutation
        assert operation.receipt == receipt and receipt["status"] == "unknown"
    allowed = True
    await service.aclose()
    assert cleanup_calls == [(owner, held_cut, connection)]
    assert owner.closed and mutation.released and service.profile_gate._cut is None
    assert service.profile_lease._fd < 0 and owner.store._disk is None
    assert operation.cleanup_owner is None and operation.memory_cut is None
    assert operation.receipt == receipt and receipt["status"] == "unknown"
