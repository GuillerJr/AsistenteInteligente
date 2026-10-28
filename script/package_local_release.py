#!/usr/bin/env python3
"""Empaqueta explícitamente código propio y un bundle Jarvis, sin ejecutarlos."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_core.local_release_package import package_release
from jarvis_core.recovery import RecoveryContext


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-source", required=True, type=Path)
    parser.add_argument("--native-bundle", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--version-key", required=True)
    parser.add_argument("--state-schema-max", type=int, default=2)
    parser.add_argument("--memory-schema-max", type=int, default=2)
    parser.add_argument("--schedules-schema-max", type=int, default=2)
    args = parser.parse_args(argv)
    context = RecoveryContext(
        uuid4().hex, "local-package", 0, lambda *_: None, threading.Event(), time.monotonic() + 120
    )
    result = package_release(
        args.python_source,
        args.native_bundle,
        args.destination,
        args.version_key,
        {
            "state": [0, args.state_schema_max],
            "memory": [0, args.memory_schema_max],
            "schedules": [0, args.schedules_schema_max],
        },
        context,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
