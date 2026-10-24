"""The reconstruction inventory reports declarations rather than invented runtime coverage."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "research_inventory", Path(__file__).resolve().parents[1] / "script/research_inventory.py",
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
    result = inventory.python_inventory(source)
    assert result["python_source_files"] == 1 and result["tool_definition_sites"] == 1
    previous = result["source_inventory_sha256"]
    (source / "main.py").write_text('x=ToolDefinition(name="changed")')
    assert inventory.python_inventory(source)["source_inventory_sha256"] != previous


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


def test_cli_requires_an_explicit_origin_without_creating_output(tmp_path):
    output = tmp_path / "new-output"
    with pytest.raises(SystemExit) as error:
        inventory.main(["--output-directory", str(output)])
    assert error.value.code == 2 and not output.exists()


def test_cli_uses_explicit_sources_and_preserves_historical_snapshots(tmp_path):
    source = tmp_path / "selected"
    source.mkdir()
    (source / "module.py").write_text('raise Exception("never execute")')
    output = tmp_path / "reports"
    output.mkdir()
    snapshots = {
        name: (output / name)
        for name in ("JARVIS_DECLARATIONS.json", "OPENCLAW_TREE_INVENTORY.json")
    }
    for path in snapshots.values():
        path.write_bytes(b"frozen historical snapshot")

    inventory.main(["--source-root", str(source), "--output-directory", str(output)])

    report = json.loads((output / "PYTHON_DECLARATIONS.json").read_text())
    assert report["source_kind"] == "explicit_python_tree"
    assert report["source_root"] == str(source)
    assert report["python_source_files"] == 1 and report["tool_definition_sites"] == 0
    assert report["source_files"][0]["path"] == "module.py"
    assert all(path.read_bytes() == b"frozen historical snapshot" for path in snapshots.values())
    assert not (output / "REFERENCE_TREE_INVENTORY.json").exists()


@pytest.mark.parametrize("symlink", [False, True])
def test_cli_refuses_existing_output_including_symlinks_to_history(tmp_path, symlink):
    source, output = tmp_path / "selected", tmp_path / "reports"
    source.mkdir()
    (source / "module.py").write_text("x = 1")
    output.mkdir()
    snapshot = output / "JARVIS_DECLARATIONS.json"
    snapshot.write_bytes(b"frozen historical snapshot")
    destination = output / "PYTHON_DECLARATIONS.json"
    if symlink:
        destination.symlink_to(snapshot)
    else:
        destination.write_bytes(b"existing report")

    with pytest.raises(SystemExit) as error:
        inventory.main(["--source-root", str(source), "--output-directory", str(output)])

    assert error.value.code == 2 and snapshot.read_bytes() == b"frozen historical snapshot"
    assert destination.is_symlink() if symlink else destination.read_bytes() == b"existing report"


def test_reference_only_cli_never_infers_python_sources(tmp_path, monkeypatch):
    origin = tmp_path / "reference"
    output = tmp_path / "reports"
    calls = []

    def reference(root):
        calls.append(root)
        return {"reference_revision": "synthetic", "tracked_files": 3}

    def forbidden_python(root):
        pytest.fail("Python source selection must be explicit")

    monkeypatch.setattr(inventory, "reference_inventory", reference)
    monkeypatch.setattr(inventory, "python_inventory", forbidden_python)
    inventory.main(["--reference-root", str(origin), "--output-directory", str(output)])
    assert calls == [origin]
    assert json.loads((output / "REFERENCE_TREE_INVENTORY.json").read_text()) == {
        "reference_revision": "synthetic", "tracked_files": 3,
    }
    assert not (output / "PYTHON_DECLARATIONS.json").exists()
    assert not (output / "OPENCLAW_TREE_INVENTORY.json").exists()


def test_all_destinations_are_checked_before_any_report_is_written(tmp_path, monkeypatch):
    source, output = tmp_path / "selected", tmp_path / "reports"
    source.mkdir()
    (source / "module.py").write_text("x = 1")
    output.mkdir()
    (output / "REFERENCE_TREE_INVENTORY.json").write_bytes(b"prior reference")
    monkeypatch.setattr(inventory, "reference_inventory", lambda root: {"tracked_files": 1})
    with pytest.raises(SystemExit) as error:
        inventory.main(["--source-root", str(source), "--reference-root", str(source),
                        "--output-directory", str(output)])
    assert error.value.code == 2 and not (output / "PYTHON_DECLARATIONS.json").exists()
    assert (output / "REFERENCE_TREE_INVENTORY.json").read_bytes() == b"prior reference"
