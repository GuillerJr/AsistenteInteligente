#!/usr/bin/env python3
"""Merge Swift compiler extraction into the Spanish catalog and its SwiftPM resource.

Run a native build first, then pass its scratch directory with --build-root. The
compiler preserves interpolation types and ignores user data and protocol strings.
Without --build-root this only regenerates the Spanish .strings resource.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "native/AegisAudio/Sources/AegisMenuBar/Resources"
CATALOG = RESOURCES / "Localizable.xcstrings"
STRINGS = RESOURCES / "es.lproj/Localizable.strings"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(CATALOG.read_text())
    if catalog.get("sourceLanguage") != "es":
        raise ValueError("The source language must remain Spanish")
    entries = catalog["strings"]
    extracted: set[str] = set()
    if args.build_root:
        for path in args.build_root.rglob("*.stringsdata"):
            data = json.loads(path.read_text())
            if "/Sources/AegisMenuBar/" not in data.get("source", ""):
                continue
            for record in data.get("tables", {}).get("Localizable", []):
                extracted.add(record["key"])
        if not extracted:
            raise ValueError("No AegisMenuBar strings found; run the native build first")
        for key in sorted(extracted):
            entries.setdefault(key, {
                "extractionState": "manual",
                "localizations": {"es": {"stringUnit": {"state": "translated", "value": key}}},
            })
    catalog["strings"] = dict(sorted(entries.items()))
    for entry in entries.values():
        # Keys are Spanish display text, not Swift identifiers; punctuation can
        # otherwise collapse distinct labels into duplicate generated symbols.
        entry.pop("shouldGenerateSymbol", None)
        entry["generatesSymbol"] = False
    encoded = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    rows = ["/* Generated from Localizable.xcstrings by script/sync_localizations.py. */"]
    for key, entry in catalog["strings"].items():
        value = entry.get("localizations", {}).get("es", {}).get("stringUnit", {}).get("value", key)
        rows.append(f"{json.dumps(key, ensure_ascii=False)} = {json.dumps(value, ensure_ascii=False)};")
    strings = "\n".join(rows) + "\n"
    changed = encoded != CATALOG.read_text() or strings != STRINGS.read_text()
    if args.check:
        if changed:
            print("Catálogo o recurso de español desactualizado. Ejecuta script/sync_localizations.py.")
            return 1
    else:
        CATALOG.write_text(encoded)
        STRINGS.write_text(strings)
    print(f"localizations={len(entries)} compiler_keys={len(extracted)} status=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
