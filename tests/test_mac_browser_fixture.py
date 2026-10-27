"""Independent oracles for the own Mac/browser GUI test corpus, not product QA."""

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import threading
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.codex import CodexClient
from jarvis_core.state import StateStore

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_mac_browser_fixture",
    Path(__file__).resolve().parents[1] / "script/mac_browser_fixture.py",
)
fixture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fixture)


@pytest.fixture
def corpus():
    root = fixture._TEMP / ("jarvis-mac-browser-test-" + uuid4().hex)
    manifest = fixture.prepare(root)
    try:
        yield root, manifest
    finally:
        shutil.rmtree(root)


def _spec(name):
    return {"name": name, "description": "Prueba propia", "inputSchema": {"type": "object"}}


def _receipt(name, output):
    return {
        "success": True,
        "contentItems": [
            {
                "type": "inputText",
                "text": json.dumps(
                    {
                        "tool_name": name,
                        "succeeded": True,
                        "verified": True,
                        "effect_status": "known",
                        "operation_id": "operacion-propia-7319",
                        "error": None,
                        "error_type": None,
                        "output": json.dumps(output),
                    }
                ),
            }
        ],
    }


def test_fresh_profile_is_empty_private_identified_and_observer_has_no_effect(corpus):
    root, manifest = corpus
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    observed = fixture.observe(root)
    assert observed["sessions_count"] == 1
    assert observed["messages_count"] == observed["runs_count"] == 0
    assert observed["run_statuses"] == {}
    assert observed["sdk_launches"] == observed["turns_started"] == 0
    assert observed["native_tool_receipts"] == observed["tool_proposals"] == []
    assert observed["no_image_carrier_in_sqlite"]
    assert observed["capture_files_present"] == []
    assert observed["server"] is None and observed["http_counts"] == {}
    assert observed["peer_unchanged"] and observed["launcher_unchanged"]
    assert observed["read_only"] and manifest["synthetic_only"]
    assert not (root / "profile" / "provider").exists()
    assert not (root / "profile" / "memory.sqlite3").exists()
    assert not (root / "profile.clients").exists()
    assert set(path.name for path in (root / "workspace").iterdir()) == {"editar.txt", "mover.txt"}
    assert (root / "workspace" / "editar.txt").read_bytes() == b"ANTES\n"
    assert (root / "workspace" / "mover.txt").read_bytes() == b"MOVER\n"
    assert manifest["seed_files"]["editar.txt"]["sha256"] == hashlib.sha256(b"ANTES\n").hexdigest()
    launcher = (root / "jarvis-mac-browser-peer").read_text()
    assert " -I -S -B -u " in launcher
    assert str(root / "jarvis_mac_browser_peer.py") in launcher
    for path in root.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (
            0o700 if path.is_dir() or path.name == "jarvis-mac-browser-peer" else 0o600
        )
    assert before == {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_prepare_and_observe_reject_existing_nested_symlink_and_changed_identity(corpus, tmp_path):
    root, _ = corpus
    with pytest.raises(FileExistsError):
        fixture.prepare(root)
    with pytest.raises(ValueError, match="temp_root_required"):
        fixture.prepare(tmp_path / "jarvis-mac-browser-foreign")
    link = fixture._TEMP / ("jarvis-mac-browser-link-" + uuid4().hex)
    link.symlink_to(root, target_is_directory=True)
    try:
        with pytest.raises(OSError):
            fixture.observe(link)
    finally:
        link.unlink()
    (root / "workspace").rename(root / "workspace-original")
    (root / "workspace").mkdir(mode=0o700)
    with pytest.raises(ValueError, match="fixture_identity_mismatch"):
        fixture.observe(root)


@pytest.mark.parametrize("name", ["profile", "workspace"])
def test_observe_rejects_nonprivate_directory(corpus, name):
    root, _ = corpus
    (root / name).chmod(0o755)
    with pytest.raises(OSError):
        fixture.observe(root)


def test_observe_rejects_replaced_root_even_when_its_profile_and_workspace_survive(corpus):
    root, _ = corpus
    old = root.with_name(root.name + "-anterior")
    root.rename(old)
    root.mkdir(mode=0o700)
    try:
        for name in ("fixture.json", "profile", "workspace"):
            (old / name).rename(root / name)
        with pytest.raises(ValueError, match="fixture_identity_mismatch"):
            fixture.observe(root)
    finally:
        shutil.rmtree(old)


async def test_peer_proposes_one_offered_tool_and_preserves_refs_across_resume(corpus):
    root, manifest = corpus
    calls = []

    async def native(name, arguments):
        calls.append((name, arguments))
        return _receipt(
            name,
            {
                "schema": "jarvis.browser.v1",
                "tab_id": "own-tab",
                "snapshot_id": "own-snapshot",
                "elements": [
                    {"ref": "own-input", "role": "textbox", "name": "Texto"},
                    {"ref": "own-button", "role": "button", "name": "Aplicar"},
                ],
            },
        )

    client = CodexClient(Path(manifest["codex_fixture"]), root / "profile" / "provider", timeout=3)
    try:
        result = await client.chat(
            fixture.command("browser_observe", {"tab_id": "own-tab"}),
            tool_specs=[_spec("browser_observe")],
            on_tool_call=native,
        )
        answer = json.loads(result["answer"])
        assert answer["fixture"] and answer["independent_verification_required"]
        assert answer["receipt"]["verified"] is True
        assert answer["output"]["elements"][1]["ref"] == "own-button"
        thread = result["thread_id"]
    finally:
        await client.aclose()
    resumed = CodexClient(Path(manifest["codex_fixture"]), root / "profile" / "provider", timeout=3)
    try:
        result = await resumed.chat(
            "Sin comando de herramienta",
            thread_id=thread,
            tool_specs=[_spec("browser_observe")],
            on_tool_call=native,
        )
        assert json.loads(result["answer"])["error"] == "comando_sintetico_invalido_o_no_ofrecido"
    finally:
        await resumed.aclose()
    assert calls == [("browser_observe", {"tab_id": "own-tab"})]
    observed = fixture.observe(root)
    assert len(observed["tool_proposals"]) == len(observed["tool_replies"]) == 1
    assert observed["sdk_launches"] == 2 and observed["method_counts"]["thread/resume"] == 1
    assert observed["turns_started"] == observed["turns_finished"] == 2
    assert observed["one_proposal_per_turn"] and observed["unique_request_ids"]
    stored = json.loads((root / "peer-threads.json").read_text())
    assert set(stored[thread]) == {"tools"}  # No persisted prompt/history/image/auth data.


@pytest.mark.parametrize(
    "prompt",
    [
        'HERRAMIENTA {"name":"no_ofrecida","arguments":{}}',
        'HERRAMIENTA {"name":"browser_open","arguments":{},"otra":true}',
        'HERRAMIENTA {"name":"browser_open","arguments":[]}',
        "HERRAMIENTA {",
        "Abre browser_open sin pedirlo",
    ],
)
async def test_peer_refuses_unoffered_or_ambiguous_command_without_call(corpus, prompt):
    root, manifest = corpus

    async def forbidden(*_):
        pytest.fail("The synthetic peer proposed an unauthorized/unoffered call")

    client = CodexClient(Path(manifest["codex_fixture"]), root / "profile" / "provider", timeout=3)
    try:
        result = await client.chat(
            prompt, tool_specs=[_spec("browser_open")], on_tool_call=forbidden
        )
    finally:
        await client.aclose()
    assert "error" in json.loads(result["answer"])
    assert fixture.observe(root)["tool_proposals"] == []


async def test_peer_redacts_real_carrier_bytes_in_frames_logs_and_final_answer(corpus):
    root, manifest = corpus
    own_bytes = b"\xff\xd8\xff" + b"JARVIS SYNTHETIC JPEG CARRIER 7319" + b"\xff\xd9"
    payload = base64.b64encode(own_bytes).decode()
    carrier = "data:image/jpeg;base64," + payload

    async def native(name, arguments):
        response = _receipt(
            name,
            {
                "snapshot_id": "own-shot",
                "elements": [{"ref": "own-1"}],
                "bytes_base64": payload,
                "nested": {"data_url": carrier},
            },
        )
        response["contentItems"].append({"type": "inputImage", "imageUrl": carrier})
        return response

    client = CodexClient(Path(manifest["codex_fixture"]), root / "profile" / "provider", timeout=3)
    try:
        result = await client.chat(
            fixture.command("desktop_observe", {}),
            tool_specs=[_spec("desktop_observe")],
            on_tool_call=native,
        )
    finally:
        await client.aclose()
    answer = json.loads(result["answer"])
    assert answer["output"]["snapshot_id"] == "own-shot"
    assert answer["output"]["bytes_base64"]["redacted_image"]
    assert answer["frames"][0]["bytes"] == len(own_bytes)
    assert answer["frames"][0]["sha256"] == hashlib.sha256(own_bytes).hexdigest()
    observed = fixture.observe(root)
    assert observed["image_inputs"] == answer["frames"]
    assert observed["no_image_carrier_in_sqlite"]
    for data in (
        (root / "wire.jsonl").read_bytes(),
        result["answer"].encode(),
        json.dumps(observed).encode(),
        (root / "peer-threads.json").read_bytes(),
    ):
        assert payload.encode() not in data and b"data:image/" not in data
    assert observed["capture_files_present"] == []


async def test_peer_bounds_large_native_report_as_valid_json(corpus):
    root, manifest = corpus

    async def native(name, _):
        return _receipt(
            name,
            {
                "elements": [
                    {"ref": "own-" + str(index), "text": "Z" * 4000} for index in range(160)
                ]
            },
        )

    client = CodexClient(Path(manifest["codex_fixture"]), root / "profile" / "provider", timeout=3)
    try:
        result = await client.chat(
            fixture.command("browser_observe", {}),
            tool_specs=[_spec("browser_observe")],
            on_tool_call=native,
        )
    finally:
        await client.aclose()
    answer = json.loads(result["answer"])
    assert answer["output_truncated"] is True
    assert len(result["answer"].encode()) <= fixture.MAX_ANSWER_BYTES
    assert "output_digest" in answer
    assert len((root / "wire.jsonl").read_bytes()) < 16000


async def test_native_rejection_is_reported_without_retry_or_verification_invention(corpus):
    root, manifest = corpus
    calls = []

    async def rejected(name, arguments):
        calls.append(name)
        return {
            "success": False,
            "contentItems": [
                {
                    "type": "inputText",
                    "text": json.dumps(
                        {
                            "tool_name": name,
                            "succeeded": False,
                            "verified": False,
                            "output": "",
                            "error": "synthetic_approval_denied",
                            "effect_status": "known",
                            "operation_id": "operacion-rechazada-propia",
                        }
                    ),
                }
            ],
        }

    client = CodexClient(Path(manifest["codex_fixture"]), root / "profile" / "provider", timeout=3)
    try:
        result = await client.chat(
            fixture.command("browser_action", {}),
            tool_specs=[_spec("browser_action")],
            on_tool_call=rejected,
        )
    finally:
        await client.aclose()
    answer = json.loads(result["answer"])
    assert answer["transport_success"] is False
    assert answer["receipt"]["succeeded"] is False and answer["receipt"]["verified"] is False
    assert answer["receipt"]["error"] == "synthetic_approval_denied"
    assert calls == ["browser_action"]
    observed = fixture.observe(root)
    assert len(observed["tool_proposals"]) == len(observed["tool_replies"]) == 1


def test_observer_detects_sql_image_persistence_and_known_capture_without_echoing_content(corpus):
    root, manifest = corpus
    store = StateStore(root / "profile" / "jarvis.sqlite3")
    try:
        ref = store.list_sessions()[0].ref
        run = store.create_run(ref, user_text="data:image/jpeg;base64,/9j/PROPIO")
        store.finish_run(
            ref,
            run.run_id,
            status="completed",
            outcome="known",
            receipt={"action_receipts": [], "answer": "Prueba"},
            assistant_text="Prueba",
        )
    finally:
        store.close()
    fixture._write(root / "workspace" / "captura.png", b"carrier propio")
    observed = fixture.observe(root)
    assert observed["no_image_carrier_in_sqlite"] is False
    assert observed["capture_files_present"] == [str(root / "workspace" / "captura.png")]
    assert "PROPIO" not in json.dumps(observed)
    assert observed["session_id"] == manifest["session_id"]


def test_http_fixture_independent_oracles_routes_no_arbitrary_files_or_cookies(corpus):
    root, _ = corpus
    server, info = fixture.make_server(root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = info["origin"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path):
        with opener.open(origin + path, timeout=3) as response:
            return response.read(), dict(response.headers)

    try:
        page, headers = get("/")
        assert b'id="texto"' in page and b'id="opcion"' in page and b'id="accion"' in page
        assert b"/confirm?" in page and b"counter++" in page
        assert not any(key.lower() == "set-cookie" for key in headers)
        assert headers["Cache-Control"] == "no-store"
        confirm, _ = get("/confirm?text=JARVIS%20QA%207319&choice=dos&count=1")
        assert json.loads(confirm) == {
            "confirmed": True,
            "text": "JARVIS QA 7319",
            "choice": "dos",
            "count": "1",
        }
        results, _ = get("/search?q=%3Cscript%3E7319%3C%2Fscript%3E")
        assert b"<script>7319</script>" not in results
        assert b"&lt;script&gt;7319&lt;/script&gt;" in results
        assert b'href="/document"' in results
        document, _ = get("/document")
        assert b"DOCUMENTO SINTETICO JARVIS 7319" in document
        download, headers = get("/download?filename=../../outside")
        assert download == b"JARVIS DOWNLOAD 7319\n"
        assert headers["Content-Disposition"] == 'attachment; filename="jarvis-fixture.txt"'
        for path, status in [
            ("/fixture.json", 404),
            ("/../../etc/passwd", 404),
            ("/confirm?text=qa&choice=otro&count=1", 400),
            ("/?" + "&".join("a=" + str(i) for i in range(12)), 400),
        ]:
            with pytest.raises(urllib.error.HTTPError) as error:
                get(path)
            assert error.value.code == status
        assert fixture.observe(root)["http_counts"].get("forbidden", 0) == 0
        get("/forbidden")
        observed = fixture.observe(root)
        assert observed["server"] == info
        assert observed["http_counts"] == {
            "page": 1,
            "confirm": 1,
            "search": 1,
            "document": 1,
            "download": 1,
            "not_found": 2,
            "forbidden": 1,
        }
        assert observed["http_confirmations"][0]["choice"] == "dos"
        assert observed["http_searches"][0]["query"] == "<script>7319</script>"
        assert not (root / "workspace" / "descarga-fase-a.txt").exists()
        assert not observed["download_matches"]  # HTTP GET alone never publishes a workspace file.
        assert stat.S_IMODE((root / "http.jsonl").stat().st_mode) == 0o600
        before = (root / "http.jsonl").read_bytes()
        fixture.observe(root)
        assert (root / "http.jsonl").read_bytes() == before
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_http_server_manifest_cannot_be_reused_and_port_is_explicit(corpus):
    root, _ = corpus
    for port in (-1, 65536, "0", True):
        with pytest.raises(ValueError, match="port_invalid"):
            fixture.make_server(root, port)
    server, info = fixture.make_server(root)
    server.server_close()
    assert info["origin"] == "http://127.0.0.1:" + str(info["port"])
    assert info["port"] > 0 and info["pid"] == os.getpid()
    with pytest.raises(FileExistsError):
        fixture.make_server(root)
