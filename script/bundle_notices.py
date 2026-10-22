#!/usr/bin/env python3
"""Bundle notices and the same locked dependency inventory used by release evidence."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from datetime import UTC, datetime
from pathlib import Path

from release_evidence import _locked_components, build_spdx_document


def generate(root: Path, resources: Path, revision: str) -> None:
    components = _locked_components(root)
    document = build_spdx_document(
        revision=revision, created_at=datetime.now(UTC).isoformat(), version="1.0.0",
        components=components,
    )
    notices = [
        "Jarvis · Licencias y avisos de terceros\n",
        "Inventario SPDX generado desde los mismos archivos de dependencias que la publicación. "
        "La inclusión en el runtime depende del perfil de compilación.\n",
        (root / "native/AegisAudio/Sources/AegisVADRuntime/Resources/SILERO_NOTICE.txt")
        .read_text(),
        "\nSilero VAD y ONNX Runtime se distribuyen bajo licencia MIT.\n"
        "ONNX Runtime · Copyright (c) Microsoft Corporation. All rights reserved.\n"
        "https://github.com/microsoft/onnxruntime\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy "
        "of this software and associated documentation files (the Software), to deal "
        "in the Software without restriction, including without limitation the rights "
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell "
        "copies of the Software, and to permit persons to whom the Software is "
        "furnished to do so, subject to the following conditions:\n\n"
        "The above copyright notice and this permission notice shall be included in "
        "all copies or substantial portions of the Software.\n\n"
        "THE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR "
        "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, "
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL "
        "THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER "
        "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING "
        "FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER "
        "DEALINGS IN THE SOFTWARE.\n",
    ]
    for component in components:
        notices.append(f"\n{'=' * 72}\n{component['name']} {component['version']}\n")
        notices.append(f"Origen: {component['location']}\n")
        if component["ecosystem"] != "pypi":
            continue
        try:
            package = importlib.metadata.distribution(component["name"])
        except importlib.metadata.PackageNotFoundError:
            notices.append("Dependencia registrada; no instalada en este perfil.\n")
            continue
        declared = package.metadata.get("License-Expression") or package.metadata.get("License")
        if declared:
            notices.append(f"Licencia declarada: {declared}\n")
        for file in package.files or ():
            if (file.name.lower().startswith(("license", "copying", "notice"))
                    and ".dist-info" in str(file)):
                path = package.locate_file(file)
                if path.is_file() and path.stat().st_size <= 256 * 1024:
                    notices.append(path.read_text(errors="replace") + "\n")
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "Jarvis.spdx.json").write_text(json.dumps(document, indent=2) + "\n")
    (resources / "ThirdPartyNotices.txt").write_text("".join(notices))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    generate(args.root, args.resources, args.revision)
