import pytest

from aegis_core.settings_store import (
    DEFAULT_PRIVATE_JSON_BYTES,
    MAX_PRIVATE_JSON_BYTES,
    read_private_json,
    write_private_json,
)


def test_deep_private_json_is_validation_error_without_modifying_file(tmp_path):
    path = tmp_path / "private.json"
    content = b"[" * 1000 + b"0" + b"]" * 1000
    path.write_bytes(content)
    path.chmod(0o600)
    with pytest.raises(ValueError, match="nesting limit"):
        read_private_json(path)
    assert path.read_bytes() == content


def test_private_json_permission_failures_are_not_reclassified_as_parse_failures(tmp_path):
    path = tmp_path / "private.json"
    path.write_text('{"value":1}')
    path.chmod(0o644)
    with pytest.raises(ValueError, match="Invalid private configuration"):
        read_private_json(path)


def test_custom_json_budget_is_explicit_and_default_remains_bounded(tmp_path):
    path = tmp_path / "outbox.json"
    value = {"result": "🧪" * 30_000}
    with pytest.raises(ValueError, match="size limit"):
        write_private_json(path, value)
    assert not path.exists()
    write_private_json(path, value, max_bytes=1_048_576)
    assert path.stat().st_size > DEFAULT_PRIVATE_JSON_BYTES
    assert read_private_json(path, max_bytes=1_048_576) == value
    with pytest.raises(ValueError, match="Invalid private configuration"):
        read_private_json(path)
    write_private_json(path, {"result": "actualizado"}, max_bytes=1_048_576)
    assert read_private_json(path) == {"result": "actualizado"}


def test_oversized_write_preserves_existing_file_and_does_not_create_parent(tmp_path):
    path = tmp_path / "settings.json"
    write_private_json(path, {"original": True})
    before = path.read_bytes()
    oversized = {"result": "x" * DEFAULT_PRIVATE_JSON_BYTES}
    with pytest.raises(ValueError, match="size limit"):
        write_private_json(path, oversized)
    assert path.read_bytes() == before and read_private_json(path) == {"original": True}
    with pytest.raises(ValueError, match="size limit"):
        write_private_json(tmp_path / "absent" / "settings.json", oversized)
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, MAX_PRIVATE_JSON_BYTES + 1])
def test_invalid_size_budget_is_rejected_without_touching_files(tmp_path, budget):
    path = tmp_path / "absent.json"
    with pytest.raises(ValueError, match="size limit"):
        write_private_json(path, {}, max_bytes=budget)
    with pytest.raises(ValueError, match="size limit"):
        read_private_json(path, max_bytes=budget)
    assert not path.exists()
