"""Retain the exact profile cut while a private WAL reader still owns resources.

The manager has no authorization ContextVar. Its original task always exits
execution_host; a later task may retire only this same private reader and cut.
"""
from __future__ import annotations

from jarvis_core.host_profile import HostProfileError


class CapturedProfileCut:
    def __init__(self, gate, *, expected_scope=None, expected_fingerprint=None):
        self.gate = gate
        self.owner = None
        self.released = False
        manager = gate.exclusive(expected_scope=expected_scope,
                                 expected_fingerprint=expected_fingerprint)
        self.cut = manager.__enter__()
        self._manager = manager

    @property
    def closed(self):
        return self.released

    def retain(self, owner):
        from jarvis_core.cold_wal import WalCaptureOwner
        from jarvis_core.local_release_update import ReleaseOwner
        from jarvis_core.private_json import PrivateJsonPendingOwner
        from jarvis_core.workspace_recovery import WorkspaceRecoveryOwner
        self.cut.assert_current()
        if not isinstance(owner, (WalCaptureOwner, ReleaseOwner, WorkspaceRecoveryOwner,
                                  PrivateJsonPendingOwner)):
            raise HostProfileError("profile_wal_owner_mismatch")
        if self.owner is not None and self.owner is not owner:
            raise HostProfileError("profile_wal_owner_mismatch")
        self.owner = owner

    def cleanup(self):
        self.cut.assert_current()
        if self.owner is not None:
            self.owner.cleanup()
            if not self.owner.closed:
                raise HostProfileError("profile_wal_cleanup_pending")

    def release(self):
        self.gate._on_loop()
        if self.released:
            return
        if self.owner is not None and not self.owner.closed:
            raise HostProfileError("profile_wal_cleanup_pending")
        if self.cut.worker_count:
            raise HostProfileError("profile_worker_cleanup_pending")
        self._manager.__exit__(None, None, None)
        self._manager = None
        self.released = True
