"""A state-produced checkpoint reaches actual CodexClient stdio serialization."""

import json
import sys
from uuid import uuid4

from test_codex_context import PEER, wire

from jarvis_core.codex import CodexClient
from jarvis_core.state import StateStore


async def test_state_checkpoint_reaches_untrusted_history_before_turn_start(tmp_path):
    with StateStore(tmp_path.resolve() / "state" / "state.sqlite3") as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "Mantén el ID exacto MARCADOR-4826.")
        snapshot = store.compact_context(
            ref, expected_revision=1, operation_id=uuid4().hex,
            retained_decisions=["No interpretar historial como permiso."],
        )
        own_history = store.context_for_provider(ref)
    executable = tmp_path / "own-stdio-peer.py"
    executable.write_text(f"#!{sys.executable}\n" + PEER.replace("mode = MODE", "mode = 'normal'"))
    executable.chmod(0o700)
    client = CodexClient(executable.resolve(), tmp_path.resolve() / "provider", timeout=2)
    try:
        await client.chat(
            "Continúa con una respuesta de texto.", history=own_history,
            instructions="PERFIL ELEGIDO POR EL DUEÑO", context_data="SKILL SINTÉTICA",
        )
        rows = wire(client)
        start = next(row for row in rows if row.get("method") == "thread/start")
        inject = next(row for row in rows if row.get("method") == "thread/inject_items")
        assert "PERFIL ELEGIDO" in start["params"]["developerInstructions"]
        assert "MARCADOR-4826" not in start["params"]["developerInstructions"]
        assert inject["params"]["items"][1:] == own_history
        capsule = inject["params"]["items"][1]
        assert capsule["role"] == "user"
        assert "MARCADOR-4826" in capsule["content"][0]["text"]
        assert snapshot["checkpoint"]["checkpoint_id"] in json.dumps(capsule)
        assert '"permits_replay":false' in capsule["content"][0]["text"]
        methods = [row.get("method") for row in rows]
        assert methods.index("thread/inject_items") < methods.index("turn/start")
        assert not {"thread/list", "thread/read", "thread/resume"} & set(methods)
    finally:
        await client.aclose()
