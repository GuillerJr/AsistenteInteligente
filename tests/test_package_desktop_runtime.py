"""Archive/wheel/custody contracts use inert fixtures; never build or download."""

import importlib.util
import io
import json
import stat
import struct
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "jarvis_package_desktop_runtime",
    Path(__file__).parents[1] / "script/package_desktop_runtime.py")
package = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = package
SPEC.loader.exec_module(package)
MACHO = struct.pack("<IIII", 0xFEEDFACF, 0x0100000C, 0, 2) + b"inert-not-an-executable"


def writer(tmp_path, *, maximum=1024 * 1024):
    root = tmp_path / "output"
    root.mkdir(mode=0o755)
    return package.TreeWriter(root, package.Budget(time.monotonic() + 10, maximum=maximum))


def tar(tmp_path, entries):
    path = tmp_path / "fixture.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, value in entries:
            info = tarfile.TarInfo(name)
            if isinstance(value, tuple):
                info.type, info.linkname = value
                archive.addfile(info)
            else:
                info.size = len(value)
                info.mode = 0o755 if name.endswith("python3.11") else 0o644
                archive.addfile(info, io.BytesIO(value))
    return path


def test_internal_archive_links_are_copied_as_independent_regular_files(tmp_path):
    archive = tar(tmp_path, [("python/bin/python3.11", MACHO),
                             ("python/bin/python3", (tarfile.SYMTYPE, "python3.11")),
                             ("python/bin/python", (tarfile.LNKTYPE, "python/bin/python3.11")),
                             ("python/licenses/LICENSE", b"fixture license")])
    output = writer(tmp_path)
    package.extract_tar(archive, output, "python", anchor="bin/python3.11")
    for name in ("python", "python3", "python3.11"):
        path = output.root / "python/bin" / name
        assert path.read_bytes() == MACHO and not path.is_symlink()
        assert path.stat().st_nlink == 1 and path.stat().st_mode & 0o111
    assert len({(output.root / "python/bin" / n).stat().st_ino
                for n in ("python", "python3", "python3.11")}) == 3


@pytest.mark.parametrize("entries", [
    [("../outside", b"no")], [("/absolute", b"no")], [("a\\b", b"no")],
    [("python/Foo", b"a"), ("python/foo", b"b")],
    [("python/x", (tarfile.SYMTYPE, "../../outside"))],
    [("python/x", (tarfile.SYMTYPE, "/outside"))],
    [("python/x", (tarfile.SYMTYPE, "y")), ("python/y", (tarfile.SYMTYPE, "x"))],
    [("python/x", (tarfile.FIFOTYPE, ""))],
    [("other/file", b"different root")],
])
def test_unsafe_archive_never_materializes_its_untrusted_entry(tmp_path, entries):
    archive = tar(tmp_path, [("python/bin/python3.11", MACHO), *entries])
    output = writer(tmp_path)
    with pytest.raises(ValueError):
        package.extract_tar(archive, output, "python", anchor="bin/python3.11")
    assert not (tmp_path / "outside").exists()


def test_link_materialization_counts_each_copy_towards_quota(tmp_path):
    archive = tar(tmp_path, [("bin/python3.11", MACHO),
                             ("bin/second", (tarfile.SYMTYPE, "python3.11"))])
    output = writer(tmp_path, maximum=len(MACHO))
    with pytest.raises(package.PackageError, match="runtime_stage_limit"):
        package.extract_tar(archive, output, "python", anchor="bin/python3.11")
    assert output.total == len(MACHO)


def wheel(tmp_path, entries=None, *, with_license=True):
    path = tmp_path / "fixture.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fixture-1.0.dist-info/METADATA", b"Name: fixture\nVersion: 1.0\n")
        if with_license:
            archive.writestr("fixture-1.0.dist-info/licenses/LICENSE", b"synthetic fixture license")
        archive.writestr("fixture/__init__.py", b"raise AssertionError('must not execute')")
        for name, payload in entries or []:
            archive.writestr(name, payload)
    return path


def test_wheel_installation_is_data_extraction_and_keeps_license(tmp_path):
    archive = wheel(tmp_path, [("fixture-1.0.data/purelib/namespace/member.py", b"# inert")])
    output = writer(tmp_path)
    licenses = package.extract_wheel(archive, output, name="fixture", version="1.0")
    assert licenses == ["site-packages/fixture-1.0.dist-info/licenses/LICENSE"]
    assert (output.root / "site-packages/namespace/member.py").read_bytes() == b"# inert"
    assert not list(output.root.rglob("__pycache__"))


def test_missing_wheel_license_requires_reviewed_nonempty_physical_fallback(tmp_path):
    archive = wheel(tmp_path, with_license=False)
    output = writer(tmp_path)
    with pytest.raises(package.PackageError, match="wheel_license_missing:fixture"):
        package.extract_wheel(archive, output, name="fixture", version="1.0")
    path = "licenses/wheels/fixture/LICENSE"
    with pytest.raises(package.PackageError, match="wheel_license_fallback_unavailable"):
        package.extract_wheel(archive, output, name="fixture", version="1.0",
                              license_fallbacks=[path])
    output.bytes(path, b"exact separately pinned source license fixture")
    assert package.extract_wheel(archive, output, name="fixture", version="1.0",
                                  license_fallbacks=[path]) == [path]


def test_license_pins_match_only_reviewed_package_and_exact_version():
    pins = json.loads((Path(__file__).parents[1] / "script/runtime_artifacts.json").read_bytes())
    flatbuffers = package.reviewed_license_fallbacks(pins, name="flatbuffers", version="25.12.19")
    assert [row["name"] for row in flatbuffers] == ["LICENSE"]
    assert flatbuffers[0]["url"].endswith("/google/flatbuffers/v25.12.19/LICENSE")
    assert package.reviewed_license_fallbacks(pins, name="flatbuffers", version="25.12.20") == []
    assert package.reviewed_license_fallbacks(pins, name="unreviewed", version="25.12.19") == []
    tokenizers = package.reviewed_license_fallbacks(pins, name="tokenizers", version="0.22.1")
    assert tokenizers[0]["url"].endswith("/huggingface/tokenizers/v0.22.1/LICENSE")
    assert package.reviewed_embedded_licenses(pins, name="onnxruntime", version="1.31.1") == [
        "onnxruntime/LICENSE", "onnxruntime/ThirdPartyNotices.txt"]
    assert package.reviewed_embedded_licenses(pins, name="onnxruntime", version="1.31.2") == []


def test_reviewed_embedded_legal_and_british_licence_spelling_are_preserved(tmp_path):
    archive = wheel(tmp_path, [("fixture/LICENSE", b"upstream license"),
                               ("fixture/ThirdPartyNotices.txt", b"dependency notice"),
                               ("fixture-1.0.dist-info/licenses/LICENCE", b"UK spelling")],
                    with_license=False)
    output = writer(tmp_path)
    with pytest.raises(package.PackageError, match="wheel_embedded_license_missing"):
        package.extract_wheel(archive, output, name="fixture", version="1.0",
                              embedded_legal=["fixture/missing.txt"])
    licenses = package.extract_wheel(archive, output, name="fixture", version="1.0",
                                     embedded_legal=["fixture/LICENSE",
                                                     "fixture/ThirdPartyNotices.txt"])
    assert licenses == ["site-packages/fixture-1.0.dist-info/licenses/LICENCE",
                        "site-packages/fixture/LICENSE",
                        "site-packages/fixture/ThirdPartyNotices.txt"]


@pytest.mark.parametrize("kind", ["traversal", "scripts", "symlink", "identity", "collision"])
def test_wheel_cannot_run_install_hooks_escape_or_substitute_identity(tmp_path, kind):
    extra = {"traversal": [("../outside", b"no")],
             "scripts": [("fixture-1.0.data/scripts/postinstall", b"no")],
             "collision": [("FIXTURE/__init__.py", b"different")],
             "symlink": [], "identity": []}[kind]
    archive = wheel(tmp_path, extra)
    if kind == "symlink":
        with zipfile.ZipFile(archive, "a") as output:
            info = zipfile.ZipInfo("fixture/link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(info, "../../outside")
    output = writer(tmp_path)
    with pytest.raises(ValueError):
        package.extract_wheel(archive, output, name="other" if kind == "identity" else "fixture",
                              version="1.0")
    assert not (tmp_path / "outside").exists()


def locked(name, filename, *, dependencies=None):
    return {"name": name, "version": "1.0", "source": {"registry": "https://pypi.org/simple"},
            "dependencies": dependencies or [], "wheels": [{
                "url": "https://files.pythonhosted.org/" + filename,
                "hash": "sha256:" + "a" * 64, "size": 10}]}


def test_locked_graph_uses_darwin_arm64_markers_and_never_sdist_or_dev(tmp_path):
    lock = {"package": [
        {"name": "jarvis-core", "version": "0.1", "dependencies": [{"name": "fixture"}]},
        locked("fixture", "fixture-1.0-cp311-cp311-macosx_14_0_arm64.whl", dependencies=[
            {"name": "win-only", "marker": "sys_platform == 'win32'"},
            {"name": "portable", "marker": "platform_machine == 'arm64'"}]),
        locked("portable", "portable-1.0-py3-none-any.whl"),
        locked("win-only", "win_only-1.0-cp311-cp311-win_amd64.whl"),
        locked("unrelated-dev", "unrelated_dev-1.0-py3-none-any.whl"),
    ]}
    assert [p["name"] for p in package.target_wheels(lock)] == ["fixture", "portable"]
    lock["package"][1]["wheels"][0]["url"] = (
        "https://files.pythonhosted.org/fixture-1.0-cp311-cp311-macosx_14_0_x86_64.whl")
    with pytest.raises(package.PackageError, match="lock_target_wheel_missing:fixture"):
        package.target_wheels(lock)


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def open(self, *_args, **_kwargs):
        self.calls += 1
        return Response(self.payload)


@pytest.mark.parametrize("mutation", [None, "hash", "short", "grows", "host"])
def test_public_download_is_exactly_pinned_and_finite_without_network(tmp_path, mutation):
    payload = b"public inert artifact"
    pin = {"url": "https://files.pythonhosted.org/inert.whl", "bytes": len(payload),
           "sha256": package.sha256(payload)}
    content = payload
    if mutation == "hash":
        pin["sha256"] = "0" * 64
    elif mutation == "short":
        content = payload[:-1]
    elif mutation == "grows":
        content += b"x"
    elif mutation == "host":
        pin["url"] = "https://unreviewed.example/inert.whl"
    opener = Opener(content)
    destination = tmp_path / "download"
    if mutation:
        with pytest.raises(package.PackageError):
            package.download(pin, destination, package.Budget(time.monotonic() + 1), opener=opener)
        assert opener.calls == (0 if mutation == "host" else 1)
    else:
        assert package.download(pin, destination, package.Budget(time.monotonic() + 1),
                                opener=opener).read_bytes() == payload


def test_native_signing_follows_dependency_admission_before_manifest(tmp_path):
    output = writer(tmp_path)
    output.bytes("python/bin/python3.11", MACHO, executable=True)
    native = output.root / "python/bin/python3.11"
    calls = []

    def runner(args):
        calls.append(args)
        if args[:2] == ["/usr/bin/otool", "-L"]:
            return b"fixture:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
        if args[:2] == ["/usr/bin/codesign", "--force"]:
            native.write_bytes(MACHO + b"signature fixture")
        return b""

    assert package.sign_nested(output.root, "-", output.budget, runner=runner) == 1
    inventory = package.file_inventory(output.root)
    assert inventory[0]["sha256"] == package.sha256(MACHO + b"signature fixture")
    assert calls[-1][:3] == ["/usr/bin/codesign", "--verify", "--strict"]
    with pytest.raises(package.PackageError, match="runtime_external_linkage"):
        package.check_linked_dependency("/opt/homebrew/lib/private.dylib", path=native,
                                        runtime=output.root, rpaths=[])


def test_pins_only_refer_to_exact_official_archives_and_legal_text():
    pins = json.loads((Path(__file__).parents[1] / "script/runtime_artifacts.json").read_bytes())
    assert pins["python"]["version"] == "3.11.18"
    assert pins["codex"]["version"] == "0.163.0"
    assert "codex-package-aarch64-apple-darwin.tar.gz" in pins["codex"]["url"]
    assert {row["name"] for row in pins["codex_legal"]} == {"LICENSE", "NOTICE"}
    for pin in [pins["python"], pins["codex"], *pins["codex_legal"]]:
        assert 0 < pin["bytes"] < package.MAX_FILE_BYTES and len(pin["sha256"]) == 64
        package.validate_download_url(pin["url"])


def test_builder_requires_remote_commit_and_guard_not_unpropagated_ci_flag(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    app = root / "Jarvis.app"
    app.mkdir()
    commit = "4" * 40
    monkeypatch.setattr(package.sys, "platform", "darwin")
    monkeypatch.delenv("CI", raising=False)
    for key, value in {
        "GITHUB_ACTIONS": "true", "GITHUB_SHA": commit,
        "JARVIS_TEST_GUARD_RUN_ROOT": str(root), "JARVIS_TEST_GUARD_NONCE": "fixture",
        "JARVIS_TEST_GUARD_CHILD_CHECKED": "fixture", "JARVIS_TEST_GUARD_ADMISSION": "fixture",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(package, "_run", lambda argv: (commit + "\n").encode()
                        if "rev-parse" in argv else b"")
    package._admission(app, root / "new-stage", commit)
    monkeypatch.setenv("GITHUB_SHA", "9" * 40)
    with pytest.raises(package.PackageError, match="runtime_build_remote_ci_only"):
        package._admission(app, root / "new-stage", commit)
    monkeypatch.setenv("GITHUB_SHA", commit)
    monkeypatch.delenv("JARVIS_TEST_GUARD_CHILD_CHECKED")
    with pytest.raises(package.PackageError, match="runtime_build_not_admitted"):
        package._admission(app, root / "new-stage", commit)
