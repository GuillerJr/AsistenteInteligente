import pytest

from aegis_core.settings_store import read_private_json


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
