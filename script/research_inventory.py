#!/usr/bin/env python3
"""Inventory explicitly selected source trees without executing their code.

Historical snapshots in docs/rebuild are inputs to research, never output names.
New reports use generic names and refuse to replace any existing destination.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
from collections import Counter
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".java", ".js", ".jsx", ".kt",
    ".m", ".mjs", ".mm", ".mts", ".py", ".rs", ".sh", ".swift", ".ts", ".tsx",
}


def tool_declarations(source: str, path: str) -> list[dict]:
    tree = ast.parse(source, filename=path)
    aliases = {"ToolDefinition"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.update(item.asname or item.name for item in node.names
                           if item.name == "ToolDefinition")
    declarations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.id if isinstance(function, ast.Name) else (
            function.attr if isinstance(function, ast.Attribute) else None
        )
        if name not in aliases:
            continue
        values = {item.arg: item.value for item in node.keywords}
        tool_name = values.get("name")
        if tool_name is None and node.args:
            tool_name = node.args[0]
        confirmation = values.get("requires_confirmation")
        fixed = isinstance(tool_name, ast.Constant) and isinstance(tool_name.value, str)
        declarations.append({
            "path": path, "line": node.lineno,
            "name": tool_name.value if fixed else None,
            "name_expression": ast.unparse(tool_name) if tool_name is not None else None,
            "name_kind": "literal" if fixed else "expression_unresolved",
            "requires_confirmation": (
                confirmation.value
                if isinstance(confirmation, ast.Constant) and type(confirmation.value) is bool
                else "runtime" if confirmation is not None else "not_specified"
            ),
        })
    return sorted(declarations, key=lambda item: item["line"])


def python_inventory(root: Path) -> dict:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Python source root must be an existing ordinary directory")
    root = root.resolve()
    declarations, files = [], []
    for path in sorted(root.rglob("*.py")):
        if " 2." in path.name or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        files.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest()})
        declarations.extend(tool_declarations(data.decode("utf-8"), relative))
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {
        "schema_version": 2, "source_kind": "explicit_python_tree",
        "source_root": str(root), "python_source_files": len(files),
        "source_inventory_sha256": digest, "source_files": files,
        "tool_definition_sites": len(declarations), "declarations": declarations,
        "limitations": [
            "Declaration sites are not the number of runtime tools.",
            "Factories, plugins, MCP servers and configuration expand the runtime catalogue.",
            "Unresolved expressions include named constants as well as dynamic factories.",
            "No source was imported, no tool was executed and no user state was read.",
            "Source presence does not prove a feature is active or verified in the app.",
        ],
    }


def reference_inventory(root: Path) -> dict:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*arguments: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *arguments], text=True,
                                       env=environment).strip()

    raw = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "-z", "HEAD"],
                                  env=environment)
    entries = [row.split(b"\t", 1) for row in raw.split(b"\0") if row]
    paths = [item[1].decode("utf-8") for item in entries]
    # The Git tree records every tracked path/blob, including scopes not examined by REA.
    return {
        "schema_version": 1, "reference_revision": git("rev-parse", "HEAD"),
        "reference_tree": git("rev-parse", "HEAD^{tree}"),
        "tracked_inventory_sha256": hashlib.sha256(raw).hexdigest(),
        "tracked_files": len(paths),
        "source_files": sum(PurePosixPath(p).suffix in SOURCE_SUFFIXES for p in paths),
        "top_level_scopes": dict(sorted(Counter(p.split("/")[0] for p in paths).items())),
        "extensions": dict(sorted(Counter(PurePosixPath(p).suffix for p in paths).items())),
        "default_review_state": "not_reviewed",
        "limitations": [
            "This is an inventory of the pinned Git tree, not a claim of code review.",
            "Per-file reviewed scopes and Evidence IDs are recorded in REA_COVERAGE.json.",
            "Fixtures, tests, assets, documentation and platform implementations are included.",
            "Only Git metadata is read; auth.json contents and user data are never opened.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path,
                        help="directorio Python explícito; no se importa ni ejecuta")
    parser.add_argument("--reference-root", type=Path,
                        help="repositorio de referencia; sólo se lee su metadata Git")
    parser.add_argument("--output-directory", type=Path, required=True,
                        help="directorio para informes nuevos, sin sobrescribir archivos")
    arguments = parser.parse_args(argv)
    if arguments.source_root is None and arguments.reference_root is None:
        parser.error("Indica --source-root o --reference-root; no se presupone un árbol legado.")

    documents = {}
    try:
        if arguments.source_root is not None:
            documents["PYTHON_DECLARATIONS.json"] = python_inventory(arguments.source_root)
        if arguments.reference_root is not None:
            documents["REFERENCE_TREE_INVENTORY.json"] = reference_inventory(
                arguments.reference_root,
            )
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as error:
        parser.error(f"No se pudo inventariar el origen seleccionado ({type(error).__name__}).")

    output = arguments.output_directory
    try:
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Check the whole set before writing, including dangling symlinks. The
        # exclusive open below also prevents a later collision from clobbering it.
        if any(os.path.lexists(output / name) for name in documents):
            parser.error("Ya existe un destino del informe; elige otro directorio de salida.")
        for name, payload in documents.items():
            with (output / name).open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            print(f"Inventario generado: {name}")
    except OSError as error:
        parser.error(f"No se pudo crear el informe sin sobrescribir ({type(error).__name__}).")


if __name__ == "__main__":
    main()
