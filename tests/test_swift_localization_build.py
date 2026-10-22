"""Exercise SwiftPM's native localization outputs and explicit CI test scope."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("swift") is None,
    reason="requires the macOS Swift toolchain",
)


def swift(arguments: list[str], scratch: Path, **options: str) -> str:
    environment = dict(os.environ)
    environment.pop("AEGIS_SWIFT_TEST_SCOPE", None)
    environment.pop("AEGIS_LOCALIZATION_OUTPUT_DIR", None)
    environment.update(options)
    environment["CLANG_MODULE_CACHE_PATH"] = str(scratch / "clang-cache")
    environment["SWIFTPM_MODULECACHE_OVERRIDE"] = str(scratch / "swift-cache")
    result = subprocess.run(
        ["swift", *arguments], env=environment, text=True, capture_output=True,
        timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def manifest(scratch: Path, **options: str) -> dict:
    return json.loads(swift([
        "package", "--package-path", str(ROOT / "native/AegisAudio"),
        "--scratch-path", str(scratch), "--disable-sandbox", "dump-package",
    ], scratch, **options))


def localization_flags(package: dict) -> list[str]:
    ui = next(target for target in package["targets"] if target["name"] == "AegisMenuBar")
    return [
        flag for setting in ui["settings"] if setting["tool"] == "swift"
        and not setting.get("condition")
        for flag in setting["kind"].get("unsafeFlags", {}).get("_0", [])
    ]


def test_default_build_keeps_both_suites_without_implicit_localization_outputs(tmp_path):
    package = manifest(tmp_path)
    tests = {target["name"] for target in package["targets"] if target["type"] == "test"}
    assert tests == {"AegisAudioCoreTests", "AegisMenuBarTests"}
    assert localization_flags(package) == []


def test_core_ci_profile_preserves_all_core_tests_and_real_vad_target(tmp_path):
    package = manifest(tmp_path, AEGIS_SWIFT_TEST_SCOPE="core")
    tests = [target for target in package["targets"] if target["type"] == "test"]
    assert [target["name"] for target in tests] == ["AegisAudioCoreTests"]
    assert tests[0].get("exclude", []) == []
    assert tests[0].get("sources") is None
    targets = {target["name"] for target in package["targets"]}
    assert {"AegisAudioCore", "AegisVADRuntime", "JarvisVADHelper"} <= targets
    products = {product["name"] for product in package["products"]}
    assert "jarvis-vad-helper" in products


def test_native_swiftpm_extracts_typed_strings_only_into_requested_directory(tmp_path):
    output = tmp_path / "localized output"
    output.mkdir()
    flags = localization_flags(manifest(
        tmp_path / "manifest", AEGIS_LOCALIZATION_OUTPUT_DIR=str(output)
    ))
    assert flags == ["-emit-localized-strings", "-emit-localized-strings-path", str(output)]
    fixture = tmp_path / "fixture"
    source = fixture / "Sources/AegisMenuBar/Probe.swift"
    source.parent.mkdir(parents=True)
    source.write_text(
        'import Foundation\n'
        'func label(_ count: Int) -> String {\n'
        '    String(localized: "Prueba con \\(count) elementos")\n}\n'
    )
    (fixture / "Package.swift").write_text(
        '// swift-tools-version: 6.2\nimport PackageDescription\n'
        'let package = Package(name: "LocalizationProbe", platforms: [.macOS(.v14)], '
        'targets: [.target(name: "AegisMenuBar", swiftSettings: [.unsafeFlags('
        + json.dumps(flags) + ')])])\n'
    )
    swift([
        "build", "--package-path", str(fixture), "--scratch-path", str(tmp_path / "build"),
        "--build-system", "native", "--disable-sandbox",
    ], tmp_path)
    extracted = list(output.glob("*.stringsdata"))
    assert len(extracted) == 1
    data = json.loads(extracted[0].read_text())
    assert "/Sources/AegisMenuBar/" in data["source"]
    assert data["tables"]["Localizable"][0]["key"] == "Prueba con %lld elementos"
