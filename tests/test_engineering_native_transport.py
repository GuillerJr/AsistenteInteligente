"""Protocol tests run without Apple Intelligence; native model semantics are tested separately."""

import asyncio
import os
from pathlib import Path

import pytest

from aegis_core.brain.errors import ModelContextLimitError
from aegis_core.contracts import AgentRole
from aegis_core.providers.apple import AppleLocalModelClient, AppleLocalModelError


def helper(tmp_path: Path, behavior: str) -> Path:
    path = tmp_path / "brain"
    path.write_text(
        "#!/usr/bin/python3\nimport json, os, sys, time\n"
        'if sys.argv[1:] == ["--status"]:\n'
        '    print(json.dumps({"available": True, "protocol_version": "2.2"}), flush=True)\n'
        "    raise SystemExit(0)\n"
        'assert sys.argv[1:] == ["--serve-stdio"]\n'
        'print(json.dumps({"type":"ready","protocol_version":"2.2"}), flush=True)\n'
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    def emit(kind, text):\n"
        '        print(json.dumps({"request_id":request["request_id"], "type":kind, '
        '"content":text}), flush=True)\n'
        + "\n".join("    " + line for line in behavior.splitlines())
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def messages(prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "name": "aegis_engineering_dialogue", "content": "Política"},
        {"role": "user", "content": prompt},
    ]


@pytest.mark.asyncio
async def test_first_request_negotiates_without_preflight(tmp_path: Path) -> None:
    client = AppleLocalModelClient(
        helper(
            tmp_path,
            """assert request["turns"][-1]["content"] == "hola"
assert request["responseMode"] == "engineering_dialogue"
emit("completed", "listo")""",
        )
    )
    try:
        result = await client.complete(role=AgentRole.CODE_SECURITY, messages=messages("hola"))
        assert result.content == "listo"
        assert client._process is not None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cancellation_discards_process_and_next_turn_succeeds(tmp_path: Path) -> None:
    client = AppleLocalModelClient(
        helper(
            tmp_path,
            """if request["prompt"] == "lento":
    emit("snapshot", "prefijo")
    time.sleep(30)
emit("completed", "nuevo")""",
        )
    )
    started = asyncio.Event()
    task = asyncio.create_task(
        client.complete_stream(
            role=AgentRole.CODE_SECURITY,
            messages=messages("lento"),
            on_delta=lambda _: started.set(),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert client._process is not None
        pid = client._process.pid
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._process is None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        result = await client.complete(role=AgentRole.CODE_SECURITY, messages=messages("rápido"))
        assert result.content == "nuevo"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


@pytest.mark.asyncio
async def test_repository_proposal_is_not_rendered_or_executed(tmp_path: Path) -> None:
    client = AppleLocalModelClient(
        helper(
            tmp_path,
            """assert request["allowRepositoryRead"] is True
emit("snapshot", "")
emit("tool_call", json.dumps(["src/main.py"]))""",
        )
    )
    chunks = []
    try:
        result = await client.complete_stream(
            role=AgentRole.CODE_SECURITY,
            messages=messages("revisa main.py"),
            extra_body={"engineering_read_enabled": True},
            on_delta=chunks.append,
        )
        assert result.content == "" and not chunks
        assert result.tool_calls[0].tool_name == "filesystem_read_text"
        assert result.tool_calls[0].arguments == {"path": "src/main.py", "max_bytes": 4096}
        assert not (tmp_path / "src").exists()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_unsolicited_read_fails_closed(tmp_path: Path) -> None:
    client = AppleLocalModelClient(helper(tmp_path, 'emit("tool_call", json.dumps(["main.py"]))'))
    try:
        with pytest.raises(AppleLocalModelError, match="unexpected native tool"):
            await client.complete(role=AgentRole.CODE_SECURITY, messages=messages("hola"))
        assert client._process is None
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '["a", "b", "c"]',
        '["a", "a"]',
        '["../secret"]',
        '["/etc/passwd"]',
        "[{}]",
        "[null]",
        '["a\\u0000b"]',
        "{}",
        "invalid",
    ],
)
def test_invalid_read_proposals_cannot_reach_broker(tmp_path: Path, content: str) -> None:
    client = AppleLocalModelClient(tmp_path / "unused")
    with pytest.raises(AppleLocalModelError):
        client._read_proposal(AgentRole.CODE_SECURITY, content)


@pytest.mark.asyncio
async def test_oversized_request_is_explicit_not_silent_truncation(tmp_path: Path) -> None:
    client = AppleLocalModelClient(helper(tmp_path, 'raise AssertionError("must not generate")'))
    with pytest.raises(ModelContextLimitError):
        await client.complete(role=AgentRole.CODE_SECURITY, messages=messages("a" * 25_000))
    assert client._process is None
