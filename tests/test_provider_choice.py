"""Provider intention fixtures do not import/launch a provider or read accounts."""

import fcntl
import json
import os
import shutil
import stat
from dataclasses import replace

import pytest
from test_recovery import bundle as bundle
from test_recovery import ctx, target

import jarvis_core.provider_choice as module
from jarvis_core.activation import (
    ADMISSION_NAME,
    activate_prepared_profile,
    review_prepared_profile,
    validate_bootstrap_admission,
)
from jarvis_core.host_profile import ProfileLease
from jarvis_core.provider_choice import NAME, ProviderChoice, ProviderChoiceError
from jarvis_core.recovery import canonical, create_backup, restore_new, verify_backup
from jarvis_core.recovery_fs import Directory, FileIdentity
from jarvis_core.tools import HostScope


class RestoredFixtureLease:
    """Synthetic exclusive physical lease, never bypasses a productive bootstrap."""
    def __init__(self, root):
        self.root = root
        self.identity = FileIdentity.of(root.stat())
        self.scope = HostScope("fixture_host", 0)
        self.fd = os.open(root / "profile.lock", os.O_RDWR)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lock_identity = FileIdentity.of(os.fstat(self.fd))

    def assert_current(self):
        with Directory(self.root, expected=self.identity):
            if FileIdentity.of((self.root / "profile.lock").lstat()) != self.lock_identity:
                raise ProviderChoiceError("fixture_lease_changed")
        if self.fd < 0:
            raise ProviderChoiceError("fixture_lease_closed")

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@pytest.fixture
def restored(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    verified = verify_backup(tmp_path / "backup", ctx())
    restore_new(tmp_path / "backup", verified, target(tmp_path, "restored"), ctx())
    root = tmp_path / "restored"
    activate_prepared_profile(review_prepared_profile(root, ctx()), ctx())
    admission = validate_bootstrap_admission(root)
    lease = RestoredFixtureLease(root)
    try:
        yield root, lease, admission
    finally:
        lease.close()


@pytest.fixture
def ordinary(tmp_path):
    root = tmp_path / "normal"
    lease = ProfileLease.acquire(root)
    try:
        yield root, lease, None
    finally:
        lease.close()


def state(root):
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        data = (path.read_bytes() if stat.S_ISREG(info.st_mode)
                else os.readlink(path) if stat.S_ISLNK(info.st_mode) else None)
        result[str(path.relative_to(root))] = (info.st_dev, info.st_ino, info.st_mode,
                                             info.st_mtime_ns, info.st_ctime_ns, data)
    return result


def changed_json(path, **kwargs):
    value = json.loads(path.read_bytes())
    value.update(kwargs)
    path.write_bytes(canonical(value))


def test_restored_missing_is_disarmed_read_only_and_local_status_is_metadata_only(restored):
    root, lease, admission = restored
    before = state(root)
    choice = ProviderChoice(*restored)
    assert choice.required and not choice.allowed
    assert choice.local_status() == {"required": True, "selected": False,
                                     "query_status_allowed": False, "revision": 0}
    token = choice.epoch()
    choice.assert_current(token, require_selected=False)
    with pytest.raises(ProviderChoiceError, match="provider_choice_required"):
        choice.assert_current(token)
    assert state(root) == before and not (root / NAME).exists()
    assert lease.identity == admission.root_identity


def test_normal_profile_keeps_legacy_allowed_with_no_constructor_writes(ordinary):
    root, _, _ = ordinary
    before = state(root)
    choice = ProviderChoice(*ordinary)
    assert not choice.required and choice.allowed
    assert choice.local_status()["query_status_allowed"]
    choice.assert_current(choice.epoch())
    assert state(root) == before and not (root / NAME).exists()


@pytest.mark.parametrize("kind", ["restored", "ordinary"])
def test_explicit_select_then_disable_persist_before_caller_sdk_and_survive_reconstruction(
    request, kind,
):
    data = request.getfixturevalue(kind)
    root, lease, admission = data
    choice = ProviderChoice(*data)
    first = choice.epoch()
    selected = choice.select()
    assert selected["selected"] and selected["revision"] == 1
    assert json.loads((root / NAME).read_bytes())["provider"] == "codex_chatgpt"
    assert choice.allowed and ProviderChoice(root, lease, admission).allowed
    with pytest.raises(ProviderChoiceError, match="provider_choice_epoch_stale"):
        choice.assert_current(first)
    assert choice.allowed  # Stale callers cannot revoke this newer selection.
    token = choice.epoch()
    disabled = choice.disable()
    assert not disabled["selected"] and disabled["revision"] == 2
    value = json.loads((root / NAME).read_bytes())
    assert value["provider"] is None and value["allowed"] is False
    assert not ProviderChoice(root, lease, admission).allowed
    with pytest.raises(ProviderChoiceError):
        choice.assert_current(token)
    # A logout can check the exact disabled epoch without granting query access.
    choice.assert_current(choice.epoch(), require_selected=False)
    assert not choice.local_status()["query_status_allowed"]
    assert not list(root.glob(".jarvis-provider-choice-*"))


def test_epoch_ignores_unrelated_profile_generations_but_not_provider_choice(restored):
    _, lease, _ = restored
    choice = ProviderChoice(*restored)
    choice.select()
    epoch = choice.epoch()
    lease.scope = replace(lease.scope, generation=lease.scope.generation + 1)
    choice.assert_current(epoch)
    choice.disable()
    choice.select()
    with pytest.raises(ProviderChoiceError, match="provider_choice_epoch_stale"):
        choice.assert_current(epoch)
    assert choice.allowed


@pytest.mark.parametrize("change", ["remove", "valid_other_revision", "corrupt", "permissions",
                                    "same_bytes_replaced", "hardlink", "symlink"])
def test_control_changes_latch_closed_with_no_fallback_or_automatic_rearm(
    restored, tmp_path, change,
):
    root, _, _ = restored
    choice = ProviderChoice(*restored)
    choice.select()
    path = root / NAME
    original = path.read_bytes()
    if change == "remove":
        path.unlink()
    elif change == "valid_other_revision":
        changed_json(path, revision=8)
    elif change == "corrupt":
        path.write_bytes(b"{bad json")
    elif change == "permissions":
        path.chmod(0o644)
    elif change == "same_bytes_replaced":
        path.unlink()
        path.write_bytes(original)
        path.chmod(0o600)
    elif change == "hardlink":
        os.link(path, tmp_path / "extra")
    else:
        saved = tmp_path / "saved"
        path.rename(saved)
        path.symlink_to(saved)
    assert not choice.allowed
    assert not choice.local_status()["query_status_allowed"]
    with pytest.raises(ProviderChoiceError):
        choice.select()
    with pytest.raises(ProviderChoiceError):
        choice.assert_current()
    # Fixing bytes alone must not revive the faulted in-memory object.
    if path.is_symlink():
        path.unlink()
    if path.exists() and path.stat().st_nlink > 1:
        path.unlink()
    path.write_bytes(original)
    path.chmod(0o600)
    assert not choice.allowed


def test_unexpected_control_appearing_in_legacy_profile_never_changes_mode_silently(ordinary):
    root, lease, _ = ordinary
    initial = ProviderChoice(*ordinary)
    another = ProviderChoice(*ordinary)
    another.disable()
    assert not initial.allowed
    assert not ProviderChoice(root, lease, None).allowed


@pytest.mark.parametrize("change", ["allowed_number", "provider_mismatch", "revision_bool",
                                    "revision_too_large", "extra_field", "duplicate_key",
                                    "noncanonical"])
def test_invalid_control_never_allows_queries_or_fallback(restored, change):
    root, lease, admission = restored
    choice = ProviderChoice(*restored)
    choice.select()
    path = root / NAME
    if change == "allowed_number":
        changed_json(path, allowed=1)
    elif change == "provider_mismatch":
        changed_json(path, provider=None)
    elif change == "revision_bool":
        changed_json(path, revision=True)
    elif change == "revision_too_large":
        changed_json(path, revision=2**52)
    elif change == "extra_field":
        changed_json(path, account="never read credentials")
    elif change == "duplicate_key":
        path.write_bytes(b'{"allowed":true,"allowed":false}')
    else:
        path.write_bytes(json.dumps(json.loads(path.read_bytes()), indent=2).encode())
    rebuilt = ProviderChoice(root, lease, admission)
    assert not rebuilt.allowed and not rebuilt.local_status()["query_status_allowed"]
    with pytest.raises(ProviderChoiceError):
        rebuilt.select()


def test_control_is_tied_to_exact_root_marker_and_admission(restored, tmp_path):
    root, _, _ = restored
    choice = ProviderChoice(*restored)
    choice.select()
    other = tmp_path / "copy"
    shutil.copytree(root, other)
    lease = RestoredFixtureLease(other)
    try:
        copied = ProviderChoice(other, lease, restored[2])
        assert not copied.allowed
        with pytest.raises(ProviderChoiceError):
            copied.assert_current()
    finally:
        lease.close()
    changed_json(root / ADMISSION_NAME, created_ns=3)
    assert not choice.allowed


def test_missing_or_wrong_admission_argument_cannot_choose_legacy_mode_for_restore(restored):
    root, lease, admission = restored
    assert not ProviderChoice(root, lease, None).allowed
    wrong = replace(admission, admission_sha256="0" * 64)
    assert not ProviderChoice(root, lease, wrong).allowed


def test_ram_closed_through_file_and_directory_fsync_until_commit_confirmed(restored, monkeypatch):
    choice = ProviderChoice(*restored)
    original = os.fsync
    observations = []

    def fsync(fd):
        observations.append(choice.allowed)
        return original(fd)

    monkeypatch.setattr(module.os, "fsync", fsync)
    choice.select()
    assert observations and not any(observations) and choice.allowed
    observations.clear()
    choice.disable()
    assert observations and not any(observations) and not choice.allowed


@pytest.mark.parametrize("when", ["write", "file_fsync", "directory_fsync"])
def test_io_failures_latch_closed_and_never_allow_a_caller_sdk(restored, monkeypatch, when):
    root, _, _ = restored
    choice = ProviderChoice(*restored)
    if when == "write":
        def failed(*args):
            raise OSError("test failure contains no public error payload")
        monkeypatch.setattr(module.os, "write", failed)
    else:
        real = os.fsync

        def failed(fd):
            is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
            if is_directory == (when == "directory_fsync"):
                raise OSError("test fsync failure")
            return real(fd)
        monkeypatch.setattr(module.os, "fsync", failed)
    with pytest.raises(ProviderChoiceError) as caught:
        choice.select()
    assert not choice.allowed and not choice.local_status()["query_status_allowed"]
    assert caught.value.effect_status == ("unknown" if when == "directory_fsync"
                                          else "not_committed")
    with pytest.raises(ProviderChoiceError):
        choice.select()
    assert not list(root.glob(".jarvis-provider-choice-*"))


def test_compare_and_swap_rejects_external_change_during_write(restored, monkeypatch):
    root, _, _ = restored
    choice = ProviderChoice(*restored)
    choice.select()
    real = os.fsync
    changed = [False]

    def mutate(fd):
        if not stat.S_ISDIR(os.fstat(fd).st_mode) and not changed[0]:
            changed[0] = True
            changed_json(root / NAME, revision=99)
        return real(fd)

    monkeypatch.setattr(module.os, "fsync", mutate)
    with pytest.raises(ProviderChoiceError, match="provider_choice_changed"):
        choice.disable()
    assert json.loads((root / NAME).read_bytes())["revision"] == 99
    assert not choice.allowed


def test_unknown_temporary_replacement_is_not_deleted(restored, tmp_path, monkeypatch):
    root, _, _ = restored
    choice = ProviderChoice(*restored)
    real = os.fsync
    saved = tmp_path / "our-pending"
    foreign = [None]

    def mutate(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode) and foreign[0] is None:
            path = next(root.glob(".jarvis-provider-choice-*"))
            path.rename(saved)
            path.write_bytes(b"UNKNOWN FILE")
            path.chmod(0o600)
            foreign[0] = path
        return real(fd)

    monkeypatch.setattr(module.os, "fsync", mutate)
    with pytest.raises(ProviderChoiceError) as caught:
        choice.select()
    assert caught.value.cleanup_pending and not choice.allowed
    assert foreign[0].read_bytes() == b"UNKNOWN FILE"
    assert not (root / NAME).exists()


def test_profile_identity_or_lease_loss_latches_closed(restored, monkeypatch):
    _, lease, _ = restored
    choice = ProviderChoice(*restored)
    choice.select()

    def closed():
        raise ProviderChoiceError("fixture_lease_closed")

    monkeypatch.setattr(lease, "assert_current", closed)
    assert not choice.allowed
    with pytest.raises(ProviderChoiceError, match="fixture_lease_closed"):
        choice.select()


def test_existing_control_replace_ack_loss_never_rearms_ram(restored, monkeypatch):
    root, _, _ = restored
    choice = ProviderChoice(*restored)
    choice.select()
    real = os.replace

    def lost(*args, **kwargs):
        real(*args, **kwargs)
        raise OSError("synthetic ACK loss after actual replace")

    monkeypatch.setattr(module.os, "replace", lost)
    with pytest.raises(ProviderChoiceError) as caught:
        choice.disable()
    assert caught.value.effect_status == "unknown"
    assert not choice.allowed
    value = json.loads((root / NAME).read_bytes())
    assert value["allowed"] is False and value["revision"] == 2
    assert not list(root.glob(".jarvis-provider-choice-*"))


def test_choice_file_from_another_ordinary_root_never_enables_sdk(ordinary, tmp_path):
    root, _, _ = ordinary
    ProviderChoice(*ordinary).select()
    lease = ProfileLease.acquire(tmp_path / "second-normal")
    try:
        shutil.copy2(root / NAME, lease.root / NAME)
        other = ProviderChoice(lease.root, lease, None)
        assert not other.allowed
        with pytest.raises(ProviderChoiceError, match="provider_choice_invalid"):
            other.assert_current()
    finally:
        lease.close()


def test_valid_new_admission_does_not_adopt_an_old_choice_file(restored):
    root, lease, _ = restored
    choice = ProviderChoice(*restored)
    choice.select()
    changed_json(root / ADMISSION_NAME, created_ns=3)
    fresh = validate_bootstrap_admission(root)
    rebuilt = ProviderChoice(root, lease, fresh)
    assert not rebuilt.allowed
    with pytest.raises(ProviderChoiceError, match="provider_choice_invalid"):
        rebuilt.select()


def test_revision_exhaustion_disarms_ram_without_overwriting_control(restored):
    root, lease, admission = restored
    ProviderChoice(*restored).select()
    changed_json(root / NAME, revision=2**52 - 1)
    choice = ProviderChoice(root, lease, admission)
    before = (root / NAME).read_bytes()
    assert choice.allowed
    with pytest.raises(ProviderChoiceError, match="provider_choice_revision_exhausted"):
        choice.disable()
    assert not choice.allowed and (root / NAME).read_bytes() == before


def test_first_fstat_failure_closes_owned_fd_and_preserves_unidentified_temporary(
    restored, monkeypatch,
):
    import errno

    root, _, _ = restored
    choice = ProviderChoice(*restored)
    real_open, real_fstat = os.open, os.fstat
    captured = {}

    def opened(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if os.fspath(path).startswith(".jarvis-provider-choice-"):
            captured.update(fd=fd, name=os.fspath(path), failed=False)
        return fd

    def failed(fd):
        if fd == captured.get("fd") and not captured["failed"]:
            captured["failed"] = True
            raise OSError(errno.EIO, "synthetic first pending fstat failure")
        return real_fstat(fd)

    monkeypatch.setattr(module.os, "open", opened)
    monkeypatch.setattr(module.os, "fstat", failed)
    with pytest.raises(ProviderChoiceError) as caught:
        choice.select()
    monkeypatch.setattr(module.os, "fstat", real_fstat)
    with pytest.raises(OSError) as closed:
        real_fstat(captured["fd"])
    assert closed.value.errno == errno.EBADF
    assert caught.value.cleanup_pending and caught.value.effect_status == "not_committed"
    assert not choice.allowed and not (root / NAME).exists()
    pending = root / captured["name"]
    assert pending.exists() and pending.read_bytes() == b""
    with pytest.raises(ProviderChoiceError):
        choice.select()
    assert pending.exists()  # No automatic retry or unknown-name removal.
