"""Acquisition/close faults on owned descriptors in reviewed private JSON only."""

import os
import stat

import pytest

import jarvis_core.private_json as primitive
from jarvis_core.private_json import PrivateJsonDocument, PrivateJsonError


@pytest.fixture
def document(tmp_path):
    root = tmp_path / "fixture-registry"
    root.mkdir(mode=0o700)

    def validate(value):
        if set(value) != {"counter"} or type(value["counter"]) is not int:
            raise PrivateJsonError("registry_fixture_invalid")

    value = PrivateJsonDocument(root, "routes.json", initial=lambda: {"counter": 0},
                                validate=validate)
    value.update(lambda _: {"counter": 1})
    return value


@pytest.mark.parametrize("stage", ["first_ancestor", "lock", "source_read", "new_temporary"])
def test_first_fstat_failure_already_has_owner_and_never_strands_an_unregistered_fd(
    document, monkeypatch, stage,
):
    original = document.path.read_bytes()
    real_fstat = os.fstat
    failure_enabled = True
    capture = {}
    real_own = primitive.PrivateJsonPendingOwner.own

    def own(owner, descriptor):
        if owner.document is document:
            capture["owner"] = owner
        return real_own(owner, descriptor)

    def fstat(descriptor):
        info = real_fstat(descriptor)
        owner = getattr(document, "_reviewed_owner", None) or capture.get("owner")
        if owner is not None and descriptor in owner.descriptors:
            target = capture.get("descriptor")
            is_new = owner.descriptors[descriptor] is None
            matches = is_new and (
                (stage == "first_ancestor" and stat.S_ISDIR(info.st_mode))
                or (stage == "lock" and stat.S_ISREG(info.st_mode) and info.st_size == 0
                and owner.name is None)
                or (stage == "source_read" and stat.S_ISREG(info.st_mode) and info.st_size > 0
                and owner.name is None)
                or (stage == "new_temporary" and descriptor == owner.staging_descriptor))
            if target is None and matches:
                capture.update(descriptor=descriptor, owner=owner)
                target = descriptor
            if target == descriptor and failure_enabled:
                raise OSError("own first-fstat fixture failure")
        return info

    monkeypatch.setattr(primitive.os, "fstat", fstat)
    monkeypatch.setattr(primitive.PrivateJsonPendingOwner, "own", own)
    with pytest.raises(PrivateJsonError, match="registry_cleanup_pending") as caught:
        document.update(lambda _: {"counter": 2}, before_publish=lambda staging: None)
    owner = caught.value.owner
    assert capture["owner"] is owner and capture["descriptor"] in owner.descriptors
    assert not owner.closed and document.path.read_bytes() == original
    # A retry observes exactly the newly acquired fd; it does not publish again.
    failure_enabled = False
    owner.cleanup()
    assert owner.closed and owner.descriptors == {}
    with pytest.raises(OSError):
        real_fstat(capture["descriptor"])
    assert document.path.read_bytes() == original
    assert not list(document.directory.glob(".registry-*"))


def test_failure_closing_intermediate_ancestor_retains_both_acquired_directory_fds(
    document, monkeypatch,
):
    original = document.path.read_bytes()
    real_close, real_fstat = os.close, os.fstat
    failure_enabled = True
    capture = {}
    real_own = primitive.PrivateJsonPendingOwner.own

    def own(owner, descriptor):
        if owner.document is document:
            capture["owner"] = owner
        return real_own(owner, descriptor)

    def close(descriptor):
        owner = getattr(document, "_reviewed_owner", None) or capture.get("owner")
        if owner is not None and descriptor in owner.descriptors and owner.parent is None:
            capture.update(owner=owner, descriptor=descriptor)
            if failure_enabled:
                raise OSError("own directory close fixture failure")
        return real_close(descriptor)

    monkeypatch.setattr(primitive.os, "close", close)
    monkeypatch.setattr(primitive.PrivateJsonPendingOwner, "own", own)
    with pytest.raises(PrivateJsonError, match="registry_cleanup_pending") as caught:
        document.update(lambda _: {"counter": 2}, before_publish=lambda staging: None)
    owner = caught.value.owner
    assert owner is capture["owner"] and len(owner.descriptors) == 2
    assert all(stat.S_ISDIR(real_fstat(fd).st_mode) for fd in owner.descriptors)
    assert document.path.read_bytes() == original
    failure_enabled = False
    owner.cleanup()
    assert owner.closed and owner.descriptors == {}
    assert document.path.read_bytes() == original


def test_transferred_runner_fd_is_owned_before_its_first_fstat_and_released_once(
    document, monkeypatch,
):
    runner = document.directory / "runner.lock"
    descriptor = os.open(runner, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    real_fstat = os.fstat
    failure_enabled = True

    def fstat(value):
        if value == descriptor and failure_enabled:
            raise OSError("own transferred-runner fstat fixture failure")
        return real_fstat(value)

    monkeypatch.setattr(primitive.os, "fstat", fstat)
    try:
        with pytest.raises(PrivateJsonError, match="registry_cleanup_pending") as caught:
            document.update(lambda _: {"counter": 2}, before_publish=lambda staging: None,
                            _owned_descriptors=(descriptor,))
        owner = caught.value.owner
        assert descriptor in owner.descriptors and not owner.closed
        assert owner.parent is None and owner.name is None
        failure_enabled = False
        owner.cleanup()
        assert owner.closed and not owner.descriptors
        with pytest.raises(OSError):
            real_fstat(descriptor)
        assert document.read() == {"counter": 1}
    finally:
        failure_enabled = False
        if not caught.value.owner.closed:
            caught.value.owner.cleanup()
