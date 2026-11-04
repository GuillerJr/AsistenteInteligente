"""CI native-only export contracts with inert Mach-O data and a signing stub.

No fixture executable, codesign command, build, network or owner profile runs.
The archive boundary must preserve native code and exclude unrelated resources.
"""

import hashlib
import importlib.util
import json
import os
import plistlib
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jarvis_export_native_qa", ROOT / "script/export_native_qa.py",
)
assert SPEC is not None and SPEC.loader is not None
exporter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)

COMMIT = "4" * 40
RUN_ID = "123456789"
BUNDLE_ID = "local.jarvis.qa.native." + COMMIT[:12]
RUNTIME_PIN = "5" * 64
SOURCE_ID = "com.jarvis.desktop.baseline"
TEXT_OFFSET = 384
PRIVATE_MARKER = b"SYNTHETIC-PRIVATE-RESOURCE-MUST-NOT-BE-EXPORTED"
SEAL_MARKER = b"SYNTHETIC-NEW-SEAL-GENERATED-BY-SIGNING-STUB"
FILES = {
    "Jarvis.app/Contents/MacOS/Jarvis",
    "Jarvis.app/Contents/Helpers/JarvisQALauncher",
    "Jarvis.app/Contents/Info.plist",
    "Jarvis.app/Contents/_CodeSignature/CodeResources",
}


def macho():
    """Arm64 MH_EXECUTE with a text section and distinct mutable LINKEDIT tail."""
    section = struct.pack(
        "<16s16sQQIIIIIIII", b"__text", b"__TEXT", 0x100000000 + TEXT_OFFSET,
        32, TEXT_OFFSET, 2, 0, 0, 0x80000400, 0, 0, 0,
    )
    text_segment = struct.pack(
        "<II16sQQQQiiII", 0x19, 152, b"__TEXT", 0x100000000, 4096,
        0, 512, 7, 5, 1, 0,
    ) + section
    linkedit_segment = struct.pack(
        "<II16sQQQQiiII", 0x19, 72, b"__LINKEDIT", 0x100001000, 4096,
        512, 64, 7, 1, 0, 0,
    )
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 2, 224, 0, 0)
    data = bytearray((header + text_segment + linkedit_segment).ljust(576, b"\0"))
    data[TEXT_OFFSET:TEXT_OFFSET + 32] = b"\x1f\x20\x03\xd5" * 8
    data[512:] = b"synthetic-signature-placeholder".ljust(64, b"\0")
    return bytes(data)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class SigningStub:
    def __init__(self, *, mutation="linkedit", fail_verify=False, wrong_identifier=False):
        self.commands = []
        self.mutation = mutation
        self.fail_verify = fail_verify
        self.wrong_identifier = wrong_identifier

    def __call__(self, argv, *, timeout=20, stdout_limit=16384, stderr_limit=65536):
        argv = list(map(str, argv))
        self.commands.append(argv)
        assert Path(argv[0]).name == "codesign", "Fixture native code must never execute"
        assert 0 < timeout <= 20 and 0 < stdout_limit <= 65536 and 0 < stderr_limit <= 65536
        app = Path(argv[-1])
        assert app.name == "Jarvis.app"
        stdout, stderr = b"", b""
        if "--verify" in argv and self.fail_verify:
            return subprocess.CompletedProcess(argv, 37, b"", b"synthetic signature failure")
        if "--force" in argv:
            seal = app / "Contents/_CodeSignature/CodeResources"
            seal.parent.mkdir(mode=0o755)
            seal.write_bytes(SEAL_MARKER)
            main = app / "Contents/MacOS/Jarvis"
            if self.mutation in {"linkedit", "text"}:
                data = bytearray(main.read_bytes())
                index = 575 if self.mutation == "linkedit" else TEXT_OFFSET
                data[index] ^= 1
                main.write_bytes(data)
            elif self.mutation == "helper":
                helper = app / "Contents/Helpers/JarvisQALauncher"
                helper.write_bytes(helper.read_bytes() + b"unexpected-change")
            elif self.mutation == "extra":
                unexpected = app / "Contents/Resources/unexpected.txt"
                unexpected.parent.mkdir()
                unexpected.write_bytes(b"must not silently archive this file")
        if "-d" in argv or "--display" in argv:
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            identifier = info["CFBundleIdentifier"]
            if self.wrong_identifier:
                identifier = "local.jarvis.wrong"
            stderr = ("Identifier=" + identifier + "\n").encode()
        return subprocess.CompletedProcess(argv, 0, stdout, stderr)


@pytest.fixture
def corpus(tmp_path):
    runner = tmp_path.resolve() / "runner-temp"
    runner.mkdir(mode=0o700)
    source = runner / "source"
    source.mkdir(mode=0o700)
    app = source / "Jarvis.app"
    for directory in (app, app / "Contents", app / "Contents/MacOS",
                      app / "Contents/Helpers", app / "Contents/Resources",
                      app / "Contents/Resources/JarvisRuntime"):
        directory.mkdir(mode=0o755)
    for relative in ("Contents/MacOS/Jarvis", "Contents/Helpers/JarvisQALauncher"):
        target = app / relative
        target.write_bytes(macho())
        target.chmod(0o755)
    info = {
        "CFBundleIdentifier": SOURCE_ID, "CFBundleName": "Jarvis",
        "CFBundleDisplayName": "Jarvis", "CFBundlePackageType": "APPL",
        "CFBundleExecutable": "Jarvis", "LSMinimumSystemVersion": "14.0",
        "NSMicrophoneUsageDescription": "Permiso sintético de prueba.",
        "NSSpeechRecognitionUsageDescription": "Reconocimiento sintético de prueba.",
        "JarvisDistributionChannel": "self-contained",
        "JarvisRuntimeManifestSHA256": RUNTIME_PIN,
    }
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    report = {
        "schema": "jarvis.bundled-runtime-qualification.v1", "source_commit": COMMIT,
        "manifest_sha256": RUNTIME_PIN, "parent_eof_clean_shutdown": True,
        "official_codex_empty_account": True, "swift_manifest_admitted": True,
    }
    qualification = app / "Contents/Resources/JarvisRuntimeQualification.json"
    qualification.write_text(json.dumps(report))
    (app / "Contents/Resources/auth.json").write_bytes(PRIVATE_MARKER)
    environment = {"GITHUB_ACTIONS": "true", "GITHUB_SHA": COMMIT,
                   "GITHUB_RUN_ID": RUN_ID, "RUNNER_TEMP": str(runner)}
    return app, runner / "native-export", environment


def export(corpus, stub=None):
    app, output, environment = corpus
    stub = stub or SigningStub()
    value = exporter.export_native_qa(
        app, output, source_commit=COMMIT, run_id=RUN_ID,
        environment=environment, command_runner=stub,
    )
    return value, stub


@pytest.mark.parametrize("environment", [
    {}, {"GITHUB_ACTIONS": "false"},
    {"GITHUB_ACTIONS": "true", "GITHUB_SHA": "3" * 40, "GITHUB_RUN_ID": RUN_ID},
    {"GITHUB_ACTIONS": "true", "GITHUB_SHA": COMMIT, "GITHUB_RUN_ID": "another-run"},
])
def test_ci_identity_rejection_precedes_missing_inputs_or_output(tmp_path, environment):
    output = tmp_path / "must-not-exist"
    stub = SigningStub()
    with pytest.raises(exporter.ExportError):
        exporter.export_native_qa(
            tmp_path / "missing/Jarvis.app", output,
            source_commit=COMMIT, run_id=RUN_ID, environment=environment,
            command_runner=stub,
        )
    assert not output.exists()
    assert stub.commands == []


def test_native_export_contains_only_fixed_code_and_new_seal(corpus):
    app, output, _ = corpus
    original_main = (app / "Contents/MacOS/Jarvis").read_bytes()
    original_helper = (app / "Contents/Helpers/JarvisQALauncher").read_bytes()
    value, stub = export(corpus)
    archive = output / "Jarvis-native-qa-arm64.tar.gz"
    provenance = output / "Jarvis-native-qa-provenance.json"
    assert value == json.loads(provenance.read_text())
    assert set(path.name for path in output.iterdir()) == {
        "Jarvis.app", archive.name, provenance.name,
    }
    with tarfile.open(archive, "r:gz") as package:
        members = package.getmembers()
        assert {member.name for member in members if member.isfile()} == FILES
        assert all(member.isfile() or member.isdir() for member in members)
        payloads = {member.name: package.extractfile(member).read()
                    for member in members if member.isfile()}
    assert not any(PRIVATE_MARKER in raw for raw in payloads.values())
    assert all("Resources" not in name or name.endswith("CodeResources") for name in payloads)
    assert payloads["Jarvis.app/Contents/_CodeSignature/CodeResources"] == SEAL_MARKER
    assert payloads["Jarvis.app/Contents/Helpers/JarvisQALauncher"] == original_helper
    main = payloads["Jarvis.app/Contents/MacOS/Jarvis"]
    assert main[TEXT_OFFSET:TEXT_OFFSET + 32] == original_main[TEXT_OFFSET:TEXT_OFFSET + 32]
    assert main != original_main  # a signature-tail change is allowed, executable text is not
    info_bytes = payloads["Jarvis.app/Contents/Info.plist"]
    info = plistlib.loads(info_bytes)
    assert info["CFBundleIdentifier"] == BUNDLE_ID
    assert info["CFBundleName"] == "Jarvis QA Nativa"
    assert info["JarvisDistributionChannel"] == "development"
    assert "JarvisRuntimeManifestSHA256" not in info
    assert "JarvisQualificationConfigurationSHA256" not in info
    assert value["format"] == "jarvis-native-qa-ci/1"
    assert value["source_commit"] == COMMIT and str(value["run_id"]) == RUN_ID
    assert value["distribution_channel"] == "development"
    assert value["self_contained"] is False
    for component in ("python", "codex", "source", "dependencies"):
        assert value["external_" + component + "_required"] is True
    assert value["code_text_unchanged"] is True and value["helper_unchanged"] is True
    assert value["compressed_limit_bytes"] <= 32 * 1024**2
    assert value["uncompressed_limit_bytes"] <= 128 * 1024**2
    assert value["uncompressed_bytes"] == sum(map(len, payloads.values()))
    assert value["archive_bytes"] <= value["compressed_limit_bytes"]
    assert value["uncompressed_bytes"] <= value["uncompressed_limit_bytes"]
    assert value["archive_bytes"] == archive.stat().st_size
    assert value["archive_sha256"] == digest(archive.read_bytes())
    assert value["binary_sha256"] == digest(main)
    assert value["qa_launcher_sha256"] == digest(original_helper)
    assert value["info_plist_sha256"] == digest(info_bytes)
    assert value["code_resources_sha256"] == digest(SEAL_MARKER)
    sign = [command for command in stub.commands if "--force" in command]
    assert len(sign) == 1
    assert sign[0][sign[0].index("--identifier") + 1] == BUNDLE_ID
    assert sign[0][sign[0].index("--sign") + 1] == "-"
    assert "--timestamp=none" in sign[0]
    assert sign[0][-1] == str(output / "Jarvis.app")
    assert (app / "Contents/MacOS/Jarvis").read_bytes() == original_main
    assert (app / "Contents/Helpers/JarvisQALauncher").read_bytes() == original_helper
    assert (app / "Contents/Resources/auth.json").read_bytes() == PRIVATE_MARKER


def test_qualification_override_pin_is_removed_from_development_export(corpus):
    app, output, _ = corpus
    path = app / "Contents/Info.plist"
    info = plistlib.loads(path.read_bytes())
    info["JarvisQualificationConfigurationSHA256"] = "9" * 64
    path.write_bytes(plistlib.dumps(info))
    export(corpus)
    exported = plistlib.loads((output / "Jarvis.app/Contents/Info.plist").read_bytes())
    assert "JarvisQualificationConfigurationSHA256" not in exported
    assert "JarvisRuntimeManifestSHA256" not in exported


@pytest.mark.parametrize("mutation", ["text", "helper", "extra"])
def test_signing_must_preserve_original_text_and_complete_helper(corpus, mutation):
    app, output, _ = corpus
    before = (app / "Contents/MacOS/Jarvis").read_bytes()
    with pytest.raises(exporter.ExportError):
        export(corpus, SigningStub(mutation=mutation))
    assert (app / "Contents/MacOS/Jarvis").read_bytes() == before
    assert not (output / "Jarvis-native-qa-provenance.json").exists()


@pytest.mark.parametrize("kind", [
    "file_symlink", "directory_symlink", "parent_symlink", "hardlink", "writable_parent",
])
def test_input_custody_rejects_links_and_writable_components(corpus, kind):
    app, output, environment = corpus
    main = app / "Contents/MacOS/Jarvis"
    if kind == "file_symlink":
        moved = main.with_name("real-main")
        main.rename(moved)
        main.symlink_to(moved)
    elif kind == "directory_symlink":
        directory = main.parent
        moved = directory.with_name("real-macos")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif kind == "parent_symlink":
        source = app.parent
        moved = source.with_name("physical-source")
        source.rename(moved)
        source.symlink_to(moved, target_is_directory=True)
    elif kind == "hardlink":
        os.link(main, Path(environment["RUNNER_TEMP"]) / "second-main")
    else:
        main.parent.chmod(0o777)
    with pytest.raises(exporter.ExportError):
        export(corpus)
    assert not (output / "Jarvis-native-qa-provenance.json").exists()


@pytest.mark.parametrize("field,value", [
    ("source_commit", "3" * 40), ("manifest_sha256", "6" * 64),
    ("parent_eof_clean_shutdown", False), ("official_codex_empty_account", False),
    ("swift_manifest_admitted", False),
])
def test_source_qualification_must_match_exact_code_manifest_and_success(corpus, field, value):
    app, output, _ = corpus
    path = app / "Contents/Resources/JarvisRuntimeQualification.json"
    report = json.loads(path.read_text())
    report[field] = value
    path.write_text(json.dumps(report))
    with pytest.raises(exporter.ExportError):
        export(corpus)
    assert not (output / "Jarvis-native-qa-provenance.json").exists()


@pytest.mark.parametrize("option", ["fail_verify", "wrong_identifier"])
def test_signing_or_metadata_failure_cannot_publish_provenance(corpus, option):
    _app, output, _ = corpus
    with pytest.raises(exporter.ExportError):
        export(corpus, SigningStub(**{option: True}))
    assert not (output / "Jarvis-native-qa-provenance.json").exists()


def test_existing_output_is_preserved_without_overwrite(corpus):
    _app, output, _ = corpus
    output.mkdir(mode=0o700)
    marker = output / "original.txt"
    marker.write_bytes(b"preserve this original")
    with pytest.raises(exporter.ExportError):
        export(corpus)
    assert marker.read_bytes() == b"preserve this original"
    assert list(output.iterdir()) == [marker]


@pytest.mark.parametrize("limit", ["MAX_UNCOMPRESSED", "MAX_COMPRESSED"])
def test_export_budget_failure_retains_inputs_and_never_publishes_provenance(
    corpus, monkeypatch, limit,
):
    app, output, _ = corpus
    main_before = (app / "Contents/MacOS/Jarvis").read_bytes()
    monkeypatch.setattr(exporter, limit, 1)
    with pytest.raises(exporter.ExportError):
        export(corpus)
    assert (app / "Contents/MacOS/Jarvis").read_bytes() == main_before
    assert not (output / "Jarvis-native-qa-provenance.json").exists()


def test_workflow_native_qa_upload_is_narrow_separate_and_keeps_full_qualification():
    workflow = ROOT / ".github/workflows/ci.yml"
    if not workflow.is_file():
        pytest.skip("CI workflow belongs to the source checkout, not the runtime archive")
    steps = yaml.safe_load(workflow.read_text())["jobs"]["python"]["steps"]
    uploads = [step for step in steps if step.get("uses") == "actions/upload-artifact@v4"]
    qa = next(step for step in uploads
              if step["with"]["name"] == "jarvis-native-qa-${{ github.sha }}")
    paths = qa["with"]["path"].strip().splitlines()
    assert len(paths) == 2
    assert {Path(path.strip()).name for path in paths} == {
        "Jarvis-native-qa-arm64.tar.gz", "Jarvis-native-qa-provenance.json",
    }
    assert all("${{ runner.temp }}/" in path for path in paths)
    assert "*" not in qa["with"]["path"]
    assert qa["with"]["if-no-files-found"] == "error"
    assert qa["with"]["retention-days"] == 14
    full = next(step for step in uploads
                if step["with"]["name"] == "jarvis-desktop-${{ github.sha }}")
    assert full["with"]["retention-days"] == 3
    assert {Path(path.strip()).name for path in full["with"]["path"].strip().splitlines()} == {
        "Jarvis-macos-arm64.tar.gz", "Jarvis-provenance.json",
    }
    assert any("--self-contained" in step.get("run", "") for step in steps)
    assert any(step.get("id") == "native_contracts" for step in steps)
    assert any(step.get("id") == "contracts" for step in steps)
    package = next(step for step in steps if step.get("id") == "python_package")
    assert "--sdist" in package["run"] and "--wheel" in package["run"]
    assert any("--smoke" in step.get("run", "") for step in steps)
    contracts = next(step for step in steps if step.get("id") == "contracts")
    assert "--full" in contracts["run"]
