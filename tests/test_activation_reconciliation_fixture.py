"""Our own corpus construction is separate from live activation decisions."""

import importlib.util
import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_activation_fixture", Path(__file__).resolve().parents[1]
    / "script/activation_reconciliation_fixture.py")
fixture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fixture)


@pytest.fixture(scope="module")
def corpus():
    root = fixture._TEMP / ("jarvis-ar-test-" + uuid4().hex[:8])
    result = fixture.prepare(root)
    try:
        yield root, result
    finally:
        shutil.rmtree(root)


def test_new_synthetic_corpus_has_disarmed_routes_nonempty_memory_and_no_admissions(corpus):
    root, result = corpus
    assert result["activation_decisions_performed"] is False
    assert not list((root / "host").iterdir())
    for case in fixture.CASES:
        verified = fixture.verify(root, case)
        assert verified["originals_unchanged"] and not verified["admission_present"]
        assert verified["owner_decision_verified_by_this_script"] is False
        assert len(verified["sql"]["memory"]["tables"]["documents"]) == 2
        assert len(verified["sql"]["state"]["tables"]["sessions"]) == 2
        schedule = verified["sql"]["schedules"]["tables"]["schedules"][0]
        assert schedule[4] == fixture._ORIGIN_WORKSPACE_ID and len(schedule[4]) == 64
        memory_scope = verified["sql"]["memory"]["tables"]["scopes"][0][0]
        assert json.loads(memory_scope) == ["jarvis", schedule[4], None]
        journal = result["cases"][case]["telegram"]
        assert journal["bot_id"] == 424242  # Public fixture identity, never a token/login.
        assert len(journal["routes"]) == 2
        assert all(route["enabled"] is False for route in journal["routes"].values())
        assert journal["offset"] == 2 and journal["control_messages"] == {"42:99": True}
        assert journal["events"]["0:1"]["status"] == "completed"
        assert not (root / case / "provider").exists()
    assert (root / "busy/profile.lock").read_bytes() == b""


def test_fixture_refuses_existing_or_non_temporary_root(corpus, tmp_path):
    root, _ = corpus
    with pytest.raises(FileExistsError):
        fixture.prepare(root)
    with pytest.raises(ValueError, match="fixture_new_private_temp_root_required"):
        fixture.prepare(tmp_path / "jarvis-ar-personal")
    with pytest.raises(ValueError, match="fixture_new_private_temp_root_required"):
        fixture.observe(tmp_path, "allow")


def test_verifier_never_opens_original_sqlite_and_detects_changed_fixture(corpus, monkeypatch):
    root, _ = corpus
    original = fixture.sqlite3.connect

    def only_ram(path, *args, **kwargs):
        assert path == ":memory:", "the auditor opened an original SQLite path"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(fixture.sqlite3, "connect", only_ram)
    assert fixture.verify(root, "tamper")["originals_unchanged"]
    configuration = root / "tamper/configuration.json"
    raw = json.loads(configuration.read_bytes())
    raw["workspace"] = "/synthetic/different-selection"
    configuration.write_bytes(fixture.canonical(raw))
    monkeypatch.setattr(fixture.sqlite3, "connect", lambda *_args, **_kwargs: pytest.fail(
        "verification must reject changed originals before constructing SQLite"))
    with pytest.raises(ValueError, match="fixture_original_changed"):
        fixture.verify(root, "tamper")
    monkeypatch.setattr(fixture.sqlite3, "connect", only_ram)
    assert fixture.observe(root, "tamper")["changed"] == ["configuration.json"]
