"""Safe source reads use only newly created workspace files."""

import hashlib
import os
import threading
import time

import pytest

from jarvis_core.media_documents import MediaError, MediaLimits
from jarvis_core.media_sources import read_media_source
from jarvis_core.workspace import WorkspaceError, WorkspaceTools


@pytest.fixture
def selected(tmp_path):
    root = tmp_path.resolve() / "workspace"
    root.mkdir(mode=0o700)
    return root, WorkspaceTools(root)


def test_media_source_can_read_larger_approved_bytes_without_weakening_text_tools(selected):
    root, workspace = selected
    payload = b"MEDIA CORPUS 8723\n" * 5000
    (root / "nota.txt").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    source = read_media_source(workspace, "nota.txt", digest)
    assert source.payload == payload and source.sha256 == digest
    assert source.relative_path == "nota.txt" and source.media_type.mime == "text/plain"
    with (
        workspace._parent(()) as parent,
        pytest.raises(WorkspaceError, match="workspace_file_limit"),
    ):
        workspace._read(parent, "nota.txt")


def test_source_digest_cas_and_size_limit_are_checked_before_content_is_returned(selected):
    root, workspace = selected
    (root / "nota.txt").write_bytes(b"ACTUAL")
    with pytest.raises(MediaError, match="media_source_changed"):
        read_media_source(workspace, "nota.txt", "f" * 64)
    with pytest.raises(MediaError, match="media_file_limit"):
        read_media_source(
            workspace,
            "nota.txt",
            hashlib.sha256(b"ACTUAL").hexdigest(),
            limits=MediaLimits(max_file_bytes=2),
        )


def test_private_paths_links_and_named_pipe_are_not_importable(selected, tmp_path):
    root, workspace = selected
    external = tmp_path / "outside.txt"
    external.write_text("PRIVATE OUTSIDE")
    (root / "link.txt").symlink_to(external)
    os.link(external, root / "hard.txt")
    os.mkfifo(root / "pipe.txt")
    for name in ("link.txt", "hard.txt", "pipe.txt"):
        with pytest.raises((MediaError, OSError)):
            read_media_source(workspace, name, "f" * 64)
    for name in ("../outside.txt", "auth.json", ".jarvis-attachments/something.txt"):
        with pytest.raises((WorkspaceError, MediaError)):
            read_media_source(workspace, name, "f" * 64)


def test_source_descriptor_and_final_entry_identity_both_revalidate(selected):
    root, workspace = selected
    payload = b"ORIGINAL"
    path = root / "nota.txt"
    path.write_bytes(payload)
    count = 0

    def guard():
        nonlocal count
        count += 1
        if count == 3:
            path.rename(root / "original-retained.txt")
            path.write_bytes(payload)

    with pytest.raises(MediaError, match="media_source_changed"):
        read_media_source(workspace, "nota.txt", hashlib.sha256(payload).hexdigest(), guard=guard)
    assert (root / "original-retained.txt").read_bytes() == payload


def test_cancelled_or_expired_read_does_not_enter_the_source(selected):
    _root, workspace = selected
    stop = threading.Event()
    stop.set()
    with pytest.raises(MediaError, match="media_cancelled"):
        read_media_source(workspace, "not-created.txt", "f" * 64, cancellation=stop)
    with pytest.raises(MediaError, match="media_timeout"):
        read_media_source(workspace, "not-created.txt", "f" * 64, deadline=time.monotonic() - 1)
