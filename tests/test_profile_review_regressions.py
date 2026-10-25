import pytest

from jarvis_core.host_profile import HostProfileError, ProfileLease, freeze_sources
from jarvis_core.memory import MemoryScope, MemoryStore
from jarvis_core.service import JarvisService


class OwnedPeer:
    """Synthetic provider: no child, credentials, inference or network."""

    async def aclose(self):
        return None


class MaintenanceClosure:
    def __init__(self):
        self.calls = 0

    async def aclose(self):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("maintenance_cleanup_incomplete")


@pytest.mark.asyncio
async def test_explicit_close_can_finish_after_maintenance_worker_settles(tmp_path):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    service = JarvisService(root, OwnedPeer())
    closure = MaintenanceClosure()
    service.maintenance_coordinator = closure
    try:
        with pytest.raises(TimeoutError, match="maintenance_cleanup_incomplete"):
            await service.aclose()
        service.profile_lease.assert_current()
        # The synthetic worker has now settled; a new explicit shutdown should
        # retry cleanup without starting any service, model, job or channel.
        await service.aclose()
        assert closure.calls == 2
        lease = ProfileLease.acquire(root)
        try:
            lease.assert_current()
        finally:
            lease.close()
    finally:
        # Test-only release of the owned fixture if the regression still fails;
        # never mutate the production lifecycle or abandon an owned lock.
        if service.profile_lease._fd >= 0:
            service._close_task = None
            await service.aclose()


@pytest.mark.asyncio
async def test_captured_store_callback_rechecks_root_before_lazy_write(tmp_path):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    service = JarvisService(root, OwnedPeer())
    callback = service.store.create_session
    old = tmp_path / "old_profile"
    try:
        root.rename(old)
        root.mkdir(mode=0o700)
        with pytest.raises(HostProfileError, match="profile_identity_changed"):
            callback("Sólo fixture")
        assert not (old / "jarvis.sqlite3").exists()
        assert not (root / "jarvis.sqlite3").exists()
    finally:
        if old.exists():
            root.rmdir()
            old.rename(root)
        await service.aclose()


@pytest.mark.asyncio
async def test_existing_cold_memory_is_blocked_not_omitted_or_recovered(tmp_path):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    own = MemoryStore(root / "memory.sqlite3")
    own.upsert(
        MemoryScope("jarvis", "fixture_workspace"),
        "fixture",
        "TOPACIO 4826",
        source_uri="jarvis://synthetic",
    )
    own.close()
    before = (root / "memory.sqlite3").read_bytes()
    service = JarvisService(root, OwnedPeer())
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            metadata = {item["role"]: item for item in frozen.metadata}
            assert metadata["memory"]["disposition"] == "unavailable"
            with pytest.raises(HostProfileError, match="profile_lazy_source_unavailable"):
                frozen.bind(cut)
        assert service.memory._disk is None
        assert (root / "memory.sqlite3").read_bytes() == before
    finally:
        await service.aclose()
