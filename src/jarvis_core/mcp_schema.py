"""Offline MCP JSON Schema validation with finite owned worker CPU/wall limits.

The small common schema remains inline. Complex draft 7/2020-12 schemas use the
pinned validator in a fresh data-only worker. No URI/file retrieval is allowed;
a timeout is a visible refusal, never permission to skip input validation.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


class SchemaError(ValueError):
    pass


def _reviewed_dependency_roots():
    """Carry the selected runtime's packages, never ambient search paths or .pth."""
    packages = ("jsonschema", "referencing", "jsonschema_specifications", "attrs", "attr", "rpds")
    names = packages + (("typing_extensions",) if sys.version_info < (3, 13) else ())
    roots = set()
    for name in names:
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin:
            raise ValueError("schema_dependency_unavailable")
        origin = Path(spec.origin).resolve(strict=True)
        if not origin.is_file():
            raise ValueError("schema_dependency_unavailable")
        if name in packages:
            if origin.name != "__init__.py" or origin.parent.name != name:
                raise ValueError("schema_dependency_unavailable")
            roots.add(str(origin.parent.parent))
        else:
            if origin.name != "typing_extensions.py":
                raise ValueError("schema_dependency_unavailable")
            roots.add(str(origin.parent))
    if len(roots) > 8 or any(len(root) > 4096 for root in roots):
        raise ValueError("schema_dependency_unavailable")
    return sorted(roots)


def _encoded(schema, value=None, *, check=False):
    payload = json.dumps({"schema": schema, "value": value, "check": check},
                         ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(payload) > 262144:
        raise SchemaError("invalid_tool_schema" if check else "invalid_tool_arguments")
    return payload


def _worker(payload):
    try:
        roots = json.dumps(_reviewed_dependency_roots(), separators=(",", ":"))
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(Path(__file__).with_name(
            "mcp_schema_worker.py")), "--reviewed-roots", roots], input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=4, close_fds=True,
            env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, check=False)
    except (OSError, ValueError, ImportError, subprocess.SubprocessError):
        raise SchemaError("schema_validation_budget_exceeded") from None
    codes = {0: None, 2: "invalid_tool_schema", 3: "invalid_tool_arguments",
             4: "unsupported_tool_schema"}
    if result.returncode not in codes or result.stdout != b"":
        raise SchemaError("schema_validation_budget_exceeded")
    if codes[result.returncode]:
        raise SchemaError(codes[result.returncode])


@functools.lru_cache(maxsize=32)
def _checked(payload):
    _worker(payload)


def check_schema(schema):
    _checked(_encoded(schema, check=True))


def validate(schema, value):
    _worker(_encoded(schema, value))
