#!/usr/bin/env python3
"""Explicit acquisition of the owner's pinned public CPU model, without notes."""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from jarvis_core.model_artifacts import ArtifactError, prepare_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("El plazo debe estar entre uno y 600 segundos.")
    try:
        result = prepare_model(args.directory, cancellation=threading.Event(),
                               deadline=time.monotonic() + args.timeout)
    except ArtifactError as error:
        print(json.dumps({"status": "failed", "error": error.code}))
        return 1
    print(json.dumps({"status": "ready", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
