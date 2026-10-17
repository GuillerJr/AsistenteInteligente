from __future__ import annotations

import io
import stat
import zipfile

import pytest

from aegis_core.distribution_archive import (
    DistributionArchiveError,
    safe_member_path,
    validate_archive_namespace,
)


def _member(name: str, mode: int = stat.S_IFREG | 0o600) -> zipfile.ZipInfo:
    item = zipfile.ZipInfo(name)
    item.create_system = 3
    item.external_attr = mode << 16
    return item


def _validate(*entries: tuple[zipfile.ZipInfo | str, bytes]) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    with zipfile.ZipFile(buffer) as archive:
        validate_archive_namespace(archive)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/Jarvis.app/a",
        "../Jarvis.app/a",
        "Jarvis.app/../a",
        "Jarvis.app/./a",
        "Jarvis.app//a",
        "Jarvis.app/a//",
        "Jarvis.app/a\\b",
        "Jarvis.app/a:b",
        "Jarvis.app/a\n",
        "Jarvis.app/a\0b",
        "Other.app/a",
    ],
)
def test_rejects_noncanonical_or_unsafe_names(name: str) -> None:
    with pytest.raises(DistributionArchiveError, match="unsafe path"):
        safe_member_path(name)


@pytest.mark.parametrize(
    "first,second",
    [
        ("A", "a"),
        ("café", "cafe\u0301"),
        ("Straße", "STRASSE"),
        ("Folder/a", "folder/b"),
    ],
)
def test_rejects_macos_namespace_collisions(first: str, second: str) -> None:
    with pytest.raises(DistributionArchiveError, match="collide"):
        _validate((f"Jarvis.app/{first}", b"1"), (f"Jarvis.app/{second}", b"2"))


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFREG])
@pytest.mark.parametrize("reverse", [False, True])
def test_no_member_can_be_extracted_through_a_link_or_file_parent(kind: int, reverse: bool) -> None:
    entries = [
        (_member("Jarvis.app/alias", kind | 0o700), b"target"),
        ("Jarvis.app/alias/data", b"overwrite"),
        ("Jarvis.app/target/data", b"original"),
    ]
    with pytest.raises(DistributionArchiveError, match="non-directory parent"):
        _validate(*(list(reversed(entries)) if reverse else entries))


@pytest.mark.parametrize(
    "mode",
    [
        stat.S_IFIFO | 0o600,
        stat.S_IFSOCK | 0o600,
        stat.S_IFCHR | 0o600,
        stat.S_IFREG | 0o4755,
        stat.S_IFREG | 0o2755,
    ],
)
def test_rejects_special_files_and_privileged_modes(mode: int) -> None:
    with pytest.raises(DistributionArchiveError, match="unsafe file metadata"):
        _validate((_member("Jarvis.app/special", mode), b""))


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".ENV",
        ".git/config",
        "credentials.json",
        "aegis.db",
        "JarvisSpeakerIdentity.mlmodelc/data",
        "jarviswakeword.mlmodelc/data",
    ],
)
def test_private_assets_are_never_distributed(name: str) -> None:
    with pytest.raises(DistributionArchiveError, match="private"):
        _validate((f"Jarvis.app/Contents/Resources/{name}", b"private"))


def test_framework_links_can_resolve_through_parent_aliases() -> None:
    _validate(
        ("Jarvis.app/Contents/Frameworks/Python.framework/Versions/A/Python", b"macho"),
        (
            _member(
                "Jarvis.app/Contents/Frameworks/Python.framework/Versions/Current",
                stat.S_IFLNK | 0o777,
            ),
            b"A",
        ),
        (
            _member("Jarvis.app/Contents/Frameworks/Python.framework/Python", stat.S_IFLNK | 0o777),
            b"Versions/Current/Python",
        ),
    )


@pytest.mark.parametrize(
    "target,reason",
    [
        (b"../../outside", "escapes"),
        (b"/etc/passwd", "unsafe symbolic"),
        (b"missing", "target is missing"),
        (b"link", "cycle"),
        (b"x" * 4097, "safety budget"),
        (b"\xff", "invalid"),
    ],
)
def test_rejects_unsafe_or_unbounded_links(target: bytes, reason: str) -> None:
    with pytest.raises(DistributionArchiveError, match=reason):
        _validate((_member("Jarvis.app/link", stat.S_IFLNK | 0o777), target))


def test_duplicate_file_directory_names_are_not_distinct_objects() -> None:
    with pytest.raises(DistributionArchiveError, match="duplicate"):
        _validate(("Jarvis.app/a", b""), (_member("Jarvis.app/a/", stat.S_IFDIR | 0o700), b""))


def test_directory_payloads_are_rejected() -> None:
    with pytest.raises(DistributionArchiveError, match="directory metadata"):
        _validate((_member("Jarvis.app/a/", stat.S_IFDIR | 0o700), b"hidden data"))


def test_size_budgets_are_checked_before_reading_link_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aegis_core.distribution_archive as module

    monkeypatch.setattr(module, "MAX_MEMBER_BYTES", 4)
    with pytest.raises(DistributionArchiveError, match="safety budget"):
        _validate((_member("Jarvis.app/a", stat.S_IFLNK | 0o777), b"oversized"))


def test_root_must_be_a_directory() -> None:
    with pytest.raises(DistributionArchiveError, match="root is not a directory"):
        _validate(("Jarvis.app", b"not an app"))
