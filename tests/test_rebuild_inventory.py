"""The reconstruction inventory reports declarations rather than invented runtime coverage."""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "rebuild_inventory", Path(__file__).resolve().parents[1] / "script/rebuild_inventory.py",
)
assert SPEC is not None and SPEC.loader is not None
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def test_inventory_never_executes_source_and_preserves_dynamic_declarations():
    source = '''
raise RuntimeError("must not execute")
from example import ToolDefinition as TD
fixed = TD(name="read_status", requires_confirmation=False)
dynamic = ToolDefinition(name=f"mcp_{server}_{tool}", requires_confirmation=policy)
default = contracts.ToolDefinition(name="external_tool")
'''
    declarations = inventory.tool_declarations(source, "source.py")
    assert len(declarations) == 3
    assert declarations[0]["name"] == "read_status"
    assert declarations[0]["requires_confirmation"] is False
    assert declarations[1]["name"] is None
    assert declarations[1]["name_kind"] == "expression_unresolved"
    assert declarations[1]["requires_confirmation"] == "runtime"
    assert declarations[2]["requires_confirmation"] == "not_specified"


def test_invalid_source_fails_instead_of_silently_claiming_a_complete_inventory():
    with pytest.raises(SyntaxError):
        inventory.tool_declarations("def broken(", "broken.py")


def test_source_inventory_excludes_cloud_duplicates_and_never_imports_code(tmp_path):
    source = tmp_path / "src/aegis_core"
    source.mkdir(parents=True)
    (source / "main.py").write_text('raise Exception("inert")\nx=ToolDefinition(name="read")')
    (source / "main 2.py").write_text("bad syntax (")
    result = inventory.jarvis_inventory(tmp_path)
    assert result["python_source_files"] == 1 and result["tool_definition_sites"] == 1
    previous = result["source_inventory_sha256"]
    (source / "main.py").write_text('x=ToolDefinition(name="changed")')
    assert inventory.jarvis_inventory(tmp_path)["source_inventory_sha256"] != previous


def test_reference_inventory_counts_every_scope_without_auth_reads_or_git_env_leak(
    tmp_path, monkeypatch,
):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(tmp_path),
                               *args], check=True, capture_output=True, text=True, env=environment)

    git("init", "-b", "main")
    (tmp_path / "source.ts").write_text("export const value = 1")
    (tmp_path / "auth.json").write_text("not a credential, but deliberately invalid JSON")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "missing-parent-repository.git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "unrelated-index"))
    result = inventory.reference_inventory(tmp_path)
    assert result["tracked_files"] == 2 and result["source_files"] == 1
    assert result["default_review_state"] == "not_reviewed"
    assert result["reference_revision"] == git("rev-parse", "HEAD").stdout.strip()
    assert "not a credential" not in str(result)
