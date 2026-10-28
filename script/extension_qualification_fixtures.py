#!/usr/bin/env python3
"""Prepare tiny own data packages for native extension qualification.

This script uses only the standard library. It neither imports Jarvis nor runs
package code, connects accounts, starts servers or changes an existing profile.
The output directory must be new; its oracle records the actual archive bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

PACKAGE_ID = "jarvis.qualification"
RESOURCE_PATH = "skills/calificacion/references/marcador.txt"
PROMPT_PATH = "prompts/inicio.txt"
SKILL_RESOURCE_PATH = "references/marcador.txt"
MARKERS = {
    "1.0.0": "RECURSO PLUGIN JARVIS V1 6142",
    "2.0.0": "RECURSO PLUGIN JARVIS V2 9257",
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def skill_text(version: str) -> str:
    marker = "SKILL JARVIS V1 8316" if version == "1.0.0" else "SKILL JARVIS V2 4729"
    return (
        "---\nname: Calificación Jarvis\n"
        "description: Habilidad propia con datos sintéticos para revisar versiones\n"
        f"version: {version}\n"
        'resources: ["references/marcador.txt"]\n'
        'argument-hint: "tema sintético"\nuser-invocable: true\n'
        'arguments: [{"name":"tema","type":"string","required":true,'
        '"description":"Tema sintético para el contexto"}]\n'
        'metadata: {"license":"MIT","tags":["calificacion"]}\n'
        f"---\nExplica brevemente el tema elegido. Marcador de revisión: {marker}.\n"
    )


def archive_contents(version: str) -> dict[str, bytes]:
    files = {
        "skills/calificacion/SKILL.md": skill_text(version).encode(),
        RESOURCE_PATH: MARKERS[version].encode(),
        PROMPT_PATH: f"CONTEXTO PLUGIN JARVIS {version} 3561".encode(),
    }
    manifest = {
        "format": "jarvis-extension/1", "package_id": PACKAGE_ID,
        "name": "Calificación de extensiones Jarvis",
        "description": "Paquete propio de datos sintéticos; no contiene código ejecutable",
        "version": version, "publisher": "Pruebas Jarvis",
        "source_uri": "https://example.com/jarvis/calificacion",
        "files": [{"path": path, "bytes": len(raw), "sha256": sha256(raw),
                   "media_type": "text/markdown" if path.endswith(".md") else "text/plain"}
                  for path, raw in sorted(files.items())],
        "skills": [{"skill_id": "calificacion", "path": "skills/calificacion/SKILL.md"}],
        "prompts": [{"prompt_id": "inicio", "path": PROMPT_PATH}],
        "hooks": [{"event": "before_turn", "prompt_id": "inicio"}],
        # Inert descriptor: installation must not make a network connection.
        # The native app requires another review to configure, then to connect.
        "mcp": [{"server_id": "documentacion", "transport": "streamable_http",
                 "endpoint": "https://learn.microsoft.com/api/mcp", "allow_loopback": False,
                 "protocol_version": "2025-11-25"}],
    }
    return {"jarvis-extension.json": json.dumps(manifest, sort_keys=True,
             ensure_ascii=False, separators=(",", ":")).encode(), **files}


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        output.write(raw)


def prepare_fixture(directory: Path) -> dict:
    """Create a new corpus and byte oracle; never inspect an old directory."""
    if not directory.is_absolute():
        raise ValueError("La carpeta de salida debe ser absoluta y nueva.")
    directory.mkdir(mode=0o700)
    archives = {}
    all_hashes = {}
    for version in MARKERS:
        name = f"jarvis-calificacion-{version}.zip"
        path = directory / name
        entries = archive_contents(version)
        with path.open("xb") as output:
            os.chmod(path, 0o600)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                for relative, raw in sorted(entries.items()):
                    info = zipfile.ZipInfo(relative, date_time=(2026, 10, 28, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_STORED
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    archive.writestr(info, raw)
        raw = path.read_bytes()
        archives[version] = {"path": name, "sha256": sha256(raw), "bytes": len(raw),
                             "resource_path": RESOURCE_PATH,
                             "resource_sha256": sha256(MARKERS[version].encode()),
                             "resource_text": MARKERS[version]}
        all_hashes[name] = sha256(raw)
    skill_paths = {
        "skill-con-recursos/SKILL.md": skill_text("1.0.0").encode(),
        "skill-con-recursos/references/marcador.txt": b"RECURSO SKILL JARVIS 1804",
        "skill-v2-para-editor.txt": skill_text("2.0.0").encode(),
    }
    for name, raw in skill_paths.items():
        _write(directory / name, raw)
        all_hashes[name] = sha256(raw)
    oracle = {
        "format": "jarvis-extension-qualification-fixture/1",
        "synthetic_own_data": True, "package_id": PACKAGE_ID, "archives": archives,
        "skill_directory": "skill-con-recursos",
        "skill_update_text": "skill-v2-para-editor.txt",
        "skill_resource_path": SKILL_RESOURCE_PATH,
        "skill_resource_text": "RECURSO SKILL JARVIS 1804",
        "skill_text_update_keeps_original_resource_snapshot": True,
        "files_sha256": all_hashes,
        "code_executed": False, "network_used": False, "profiles_changed": False,
    }
    _write(directory / "oracle.json", (json.dumps(oracle, ensure_ascii=False,
                                                  indent=2) + "\n").encode())
    return oracle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    oracle = prepare_fixture(args.output_directory)
    print(json.dumps({"output_directory": str(args.output_directory),
                      "archive_sha256": {v: row["sha256"]
                                         for v, row in oracle["archives"].items()},
                      "format": oracle["format"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
