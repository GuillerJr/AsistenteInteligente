from __future__ import annotations

import asyncio
import ctypes
import json
import os
import platform
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path


def _self_test() -> int:
    """Validate the frozen runtime without opening sockets or persistent stores."""
    checks = {
        "architecture_arm64": platform.machine() == "arm64",
        "daemon_importable": False,
        "frozen_runtime": bool(getattr(sys, "frozen", False)),
        "mlx_runtime_linked": False,
        "numba_openmp_linked": False,
        "pythonpath_absent": "PYTHONPATH" not in os.environ,
        "sqlite_fts5": False,
        "sqlite_vec": False,
    }
    try:
        from aegis_core.runtime.daemon import run_daemon

        checks["daemon_importable"] = callable(run_daemon)
    except Exception:
        pass
    try:
        import sqlite_vec

        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(content)")
            checks["sqlite_fts5"] = True
            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            connection.enable_load_extension(False)
            version = connection.execute("SELECT vec_version()").fetchone()
            checks["sqlite_vec"] = bool(version and isinstance(version[0], str))
        finally:
            connection.close()
    except (ImportError, OSError, sqlite3.Error):
        pass
    try:
        runtime_root = Path(str(getattr(sys, "_MEIPASS", "")))
        mlx_library = runtime_root / "mlx/lib/libmlx.dylib"
        openmp_library = runtime_root / "libomp.dylib"
        ctypes.CDLL(str(mlx_library))
        ctypes.CDLL(str(openmp_library))
        checks["mlx_runtime_linked"] = mlx_library.is_file()
        checks["numba_openmp_linked"] = openmp_library.is_file()
    except OSError:
        pass
    passed = all(checks.values())
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "profile": "jarvis_frozen_daemon_self_test",
                "status": "passed" if passed else "blocked",
                "gate_passed": passed,
                "checks": checks,
                "privacy": {
                    "contains_credentials": False,
                    "contains_paths": False,
                    "network_calls": 0,
                    "persistent_writes": 0,
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if passed else 2


def main(arguments: Sequence[str] | None = None) -> int:
    selected = tuple(sys.argv[1:] if arguments is None else arguments)
    if selected == ("--self-test",):
        return _self_test()
    if selected and selected[0] in {
        "secure-update-install",
        "secure-update-recover",
        "secure-update-verify",
    }:
        from aegis_core.cli_entrypoint import run

        return run(selected)
    if selected:
        print("status=error reason=unsupported_frozen_daemon_argument", flush=True)
        return 2
    from aegis_core.runtime.daemon import run_daemon

    return asyncio.run(run_daemon())


if __name__ == "__main__":
    raise SystemExit(main())
