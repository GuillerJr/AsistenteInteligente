"""Local provider intention only: no SDK, credentials, accounts or inference.

The host calls select/disable from the owner's explicit native operation under
its profile gate. This object is no second authority. A restored profile starts
closed until a valid local choice exists; ordinary profiles keep legacy access
without an initial write. Epochs do not depend on unrelated profile generations.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from uuid import uuid4

from jarvis_core.activation import LocalAdmission, validate_bootstrap_admission
from jarvis_core.recovery import _decode, canonical
from jarvis_core.recovery_fs import (
    Directory,
    FileIdentity,
    PinnedFile,
    RecoveryFsError,
    digest,
    no_replace,
    private_file,
)

NAME = "provider_choice.json"
FORMAT = "jarvis-provider-choice/1"
_MAX_REVISION = (1 << 52) - 1
_MAX_BYTES = 16_384


class ProviderChoiceError(ValueError):
    def __init__(self, code, *, effect_status="not_committed", cleanup_pending=False):
        super().__init__(code)
        self.code = code
        self.effect_status = effect_status
        self.cleanup_pending = cleanup_pending


def _code(error):
    code = getattr(error, "code", None)
    return (code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code)
            else "provider_choice_unavailable")


def _metadata(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _exists(parent, name):
    try:
        return os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


class ProviderChoice:
    def __init__(self, root, lease, admission: LocalAdmission | None):
        self.root = Path(root)
        self.lease = lease
        self.admission = admission
        self._required = admission is not None
        self._identity = lease.identity
        self._profile_id = lease.scope.profile_id
        self._mutex = threading.RLock()
        self._fault = None
        self._counter = 0
        self._changing = False
        self._revision = 0
        self._selected = not self._required
        self._expected = None
        self._instance_id = uuid4().hex
        try:
            with Directory(self.root, expected=self._identity) as parent:
                self._lease_current()
                self._admission_current(parent)
                value, fingerprint = self._load(parent)
                if value is not None:
                    self._selected, self._revision = value["allowed"], value["revision"]
                self._expected = fingerprint
        except BaseException as error:
            self._latch(error)

    @property
    def required(self):
        return self._required

    def _latch(self, error, *, effect_status=None, cleanup_pending=False):
        self._selected = False
        if self._fault is None:
            self._counter += 1
            self._fault = ProviderChoiceError(
                _code(error), effect_status=(effect_status or getattr(error, "effect_status",
                                                                    "not_committed")),
                cleanup_pending=cleanup_pending or getattr(error, "cleanup_pending", False))
        return self._fault

    def _lease_current(self):
        self.lease.assert_current()
        if (self.lease.root != self.root or self.lease.identity != self._identity
                or self.lease.scope.profile_id != self._profile_id):
            raise ProviderChoiceError("provider_choice_profile_changed")

    def _admission_current(self, parent):
        actual = validate_bootstrap_admission(parent)
        if actual != self.admission:
            raise ProviderChoiceError("provider_choice_admission_changed")

    def _fields(self):
        return {
            "format": FORMAT,
            "root_identity": {"device": str(self._identity.device),
                              "inode": str(self._identity.inode)},
            "marker_sha256": None if self.admission is None else self.admission.marker_sha256,
            "admission_sha256": None if self.admission is None else self.admission.admission_sha256,
        }

    def _validate(self, value, raw):
        base = self._fields()
        if (not isinstance(value, dict)
                or set(value) != set(base) | {"provider", "allowed", "revision"}
                or any(value[key] != expected for key, expected in base.items())
                or type(value["allowed"]) is not bool
                or value["provider"] != ("codex_chatgpt" if value["allowed"] else None)
                or type(value["revision"]) is not int
                or not 1 <= value["revision"] <= _MAX_REVISION or canonical(value) != raw):
            raise ProviderChoiceError("provider_choice_invalid")

    def _load(self, parent):
        parent.check()
        if _exists(parent, NAME) is None:
            return None, None
        with PinnedFile(parent, NAME, _MAX_BYTES) as source:
            raw = source.read(self._lease_current)
            value = _decode(raw, _MAX_BYTES)
            self._validate(value, raw)
            source.check()
            return value, (_metadata(source.info), digest(raw))

    def _verify(self, parent):
        self._lease_current()
        parent.check()
        self._admission_current(parent)
        value, fingerprint = self._load(parent)
        if fingerprint != self._expected:
            raise ProviderChoiceError("provider_choice_changed")
        return value

    def _epoch(self):
        return (self._instance_id, self._profile_id, self._identity, self._expected,
                self._revision, self._counter, self._selected)

    def assert_current(self, epoch=None, require_selected=True):
        with self._mutex:
            if self._fault is not None:
                raise self._fault
            if self._changing:
                raise ProviderChoiceError("provider_choice_changing")
            try:
                with Directory(self.root, expected=self._identity) as parent:
                    self._verify(parent)
            except BaseException as error:
                raise self._latch(error) from None
            # A stale caller must not revoke a more recent explicit selection.
            if epoch is not None and epoch != self._epoch():
                raise ProviderChoiceError("provider_choice_epoch_stale")
            if require_selected and not self._selected:
                raise ProviderChoiceError("provider_choice_required")

    def epoch(self):
        with self._mutex:
            self.assert_current(require_selected=False)
            return self._epoch()

    @property
    def allowed(self):
        with self._mutex:
            try:
                self.assert_current(require_selected=False)
                return self._selected
            except ProviderChoiceError:
                return False

    def local_status(self):
        with self._mutex:
            selected = self.allowed
            return {"required": self.required, "selected": selected,
                    "query_status_allowed": selected, "revision": self._revision}

    def _cleanup_pending(self, parent, name, identity):
        info = _exists(parent, name)
        if info is None:
            return
        private_file(info, _MAX_BYTES)
        if identity is None or FileIdentity.of(info) != identity:
            raise ProviderChoiceError("provider_choice_cleanup_identity_changed")
        self._lease_current()
        parent.check()
        os.unlink(name, dir_fd=parent.fd)
        os.fsync(parent.fd)

    def _set(self, selected):
        with self._mutex:
            self.assert_current(require_selected=False)
            # Immediately revoke every older await, even if the write later fails.
            self._selected = False
            self._counter += 1
            self._changing = True
            name = ".jarvis-provider-choice-" + uuid4().hex + ".pending"
            identity, attempted, created = None, False, False
            try:
                with Directory(self.root, expected=self._identity) as parent:
                    self._verify(parent)
                    if self._revision == _MAX_REVISION:
                        raise ProviderChoiceError("provider_choice_revision_exhausted")
                    value = {**self._fields(), "provider": "codex_chatgpt" if selected else None,
                             "allowed": selected, "revision": self._revision + 1}
                    raw = canonical(value)
                    self._validate(value, raw)
                    self._lease_current()
                    parent.check()
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                                 os.O_CLOEXEC, 0o600, dir_fd=parent.fd)
                    created = True
                    try:
                        identity = FileIdentity.of(os.fstat(fd))
                        position = 0
                        while position < len(raw):
                            self._lease_current()
                            parent.check()
                            info = _exists(parent, name)
                            if info is None or FileIdentity.of(info) != identity:
                                raise ProviderChoiceError("provider_choice_pending_changed")
                            written = os.write(fd, raw[position:])
                            if written <= 0:
                                raise ProviderChoiceError("provider_choice_write_failed")
                            position += written
                        self._lease_current()
                        try:
                            os.fsync(fd)
                        except OSError:
                            raise ProviderChoiceError(
                                "provider_choice_pending_fsync_failed") from None
                    finally:
                        os.close(fd)
                    with PinnedFile(parent, name, _MAX_BYTES) as source:
                        if source.identity != identity or source.read(self._lease_current) != raw:
                            raise ProviderChoiceError("provider_choice_pending_changed")
                        self._verify(parent)
                        source.check()
                        self._lease_current()
                        attempted = True
                        try:
                            if self._expected is None:
                                no_replace(parent, name, NAME)
                            else:
                                os.replace(name, NAME, src_dir_fd=parent.fd, dst_dir_fd=parent.fd)
                        except RecoveryFsError as error:
                            if error.code in {"recovery_destination_exists",
                                              "recovery_exclusive_publish_unavailable"}:
                                attempted = False
                            raise
                    current, fingerprint = self._load(parent)
                    info = _exists(parent, NAME)
                    if (current != value or info is None or FileIdentity.of(info) != identity):
                        raise ProviderChoiceError("provider_choice_commit_changed")
                    self._lease_current()
                    parent.check()
                    try:
                        os.fsync(parent.fd)
                    except OSError:
                        raise ProviderChoiceError("provider_choice_durability_uncertain") from None
                    # Recheck control/admission after any fsync callback before arming RAM.
                    self._admission_current(parent)
                    latest, latest_fingerprint = self._load(parent)
                    if latest != value or latest_fingerprint != fingerprint:
                        raise ProviderChoiceError("provider_choice_commit_changed")
                    self._expected, self._revision = fingerprint, value["revision"]
                    self._selected = selected
            except BaseException as error:
                # Exclusive creation is an effect even if the first metadata
                # read failed. Close is covered by finally; a name without an
                # established identity must be preserved, never blindly removed.
                cleanup_pending = created and identity is None
                if identity is not None:
                    try:
                        with Directory(self.root, expected=self._identity) as parent:
                            self._cleanup_pending(parent, name, identity)
                    except BaseException:
                        cleanup_pending = True
                raise self._latch(error, effect_status="unknown" if attempted else "not_committed",
                                  cleanup_pending=cleanup_pending) from None
            finally:
                self._changing = False
            return self.local_status()

    def select(self):
        """Owner-selected Codex/ChatGPT intention; caller starts SDK only after return."""
        return self._set(True)

    def disable(self):
        """Persist closed intention before caller attempts any logout SDK action."""
        return self._set(False)
