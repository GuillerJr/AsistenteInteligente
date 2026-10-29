"""Actual own runtime/tool catalog; synthetic provider never supplies authority."""

import asyncio
import base64
import hashlib
import json
import struct
import zlib
from uuid import uuid4

import pytest
from test_service import session, settled
from test_service_media import imported
from test_service_media import media_service as media_service
from test_service_telegram import approval

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.media_documents import extract_document
from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.tools import ToolError


async def test_media_list_uses_active_session_without_an_approval_lease(media_service):
    service, client, peer, _root = media_service
    ref, other = await session(client), await session(client)
    row = await imported(client, ref)
    foreign = await imported(client, other, b"OTRO DOCUMENTO 6234")
    peer.plans["enumera"] = [("media_list", {})]
    run = await client.call("runs.start", {**ref, "message": "enumera"})
    result = await settled(client, ref, run["run_id"])
    action = result["action_receipts"][0]
    assert result["status"] == "completed" and action["succeeded"] and action["verified"]
    # Durable receipts deliberately retain only a preview; the provider gets
    # the complete text result, including chronology and the full manifest.
    data = json.loads(json.loads(peer.replies[0]["contentItems"][0]["text"])["output"])
    assert [x["attachment_id"] for x in data["attachments"]] == [row["attachment_id"]]
    assert foreign["attachment_id"] not in action["output"]
    assert data["catalog_order"] == "known_catalog_sequence_desc_then_legacy_id"
    assert data["legacy_chronology"] == "unknown"
    assert data["metadata_grants_authority"] is False
    assert not service.authority.list_pending()


async def test_model_catalog_identifies_newest_identically_named_import_and_keeps_other_chat_out(
    media_service,
):
    service, client, peer, _root = media_service
    ref, other = await session(client), await session(client)
    first = await imported(client, ref, b"PRIMERA NOTA 1642", name="nota.txt")
    second = await imported(client, ref, b"SEGUNDA NOTA 2973", name="nota.txt")
    foreign = await imported(client, other, b"OTRO CHAT 5384", name="nota.txt")
    peer.plans["identifica ultima nota"] = [("media_list", {})]
    run = await client.call("runs.start", {**ref, "message": "identifica ultima nota"})
    result = await settled(client, ref, run["run_id"])
    full = json.loads(peer.replies[-1]["contentItems"][0]["text"])["output"]
    catalog = json.loads(full)
    assert result["status"] == "completed" and result["outcome"] == "known"
    assert [r["attachment_id"] for r in catalog["attachments"]] == [
        second["attachment_id"], first["attachment_id"]
    ]
    assert [r["catalog_sequence"] for r in catalog["attachments"]] == [2, 1]
    assert all(r["name"] == "nota.txt" for r in catalog["attachments"])
    assert foreign["attachment_id"] not in full
    assert not service.authority.list_pending()
    assert result["action_receipts"][0]["output_sha256"] == hashlib.sha256(
        full.encode()
    ).hexdigest()


@pytest.mark.parametrize("allow", [True, False])
async def test_pure_document_requires_exact_grant_and_accepts_frozen_arguments(
    media_service, allow
):
    service, client, peer, _root = media_service
    ref = await session(client)
    row = await imported(client, ref, b"TEXTO LOCAL 4826")
    args = {"attachment_id": row["attachment_id"], "sha256": row["sha256"]}
    peer.plans["extrae"] = [("media_inspect", args)]
    started = await client.call("runs.start", {**ref, "message": "extrae"})
    card = await approval(client, ref)
    assert card["tool_name"] == "media_inspect" and not peer.replies
    await client.call(
        "approvals.decide",
        {**ref, "approval_id": card["approval_id"], "digest": card["digest"], "allow": allow},
    )
    result = await settled(client, ref, started["run_id"])
    if allow:
        action = result["action_receipts"][0]
        assert action["succeeded"] and action["verified"]
        assert "TEXTO LOCAL 4826" in action["output"]
        assert row["sha256"] in action["output"] and "external" in action["output"]
    else:
        assert result["status"] == "failed" and not result["action_receipts"]
        assert result["error"] == "approval_denied"
    assert not service.media_tools.frames and not service._media_native_payloads


async def test_direct_media_inspect_callback_has_no_authority(media_service):
    service, _client, _peer, _root = media_service
    with pytest.raises(ToolError, match="tool_authority_required"):
        await service.media_tools.execute(
            "media_inspect",
            {
                "attachment_id": "a" * 32,
                "sha256": "b" * 64,
            },
            asyncio.Event(),
        )


def synthetic_png():
    def piece(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + piece(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + piece(b"IDAT", zlib.compress(b"\0\xff\0\0"))
        + piece(b"IEND", b"")
    )


async def native_work(client, host):
    async with asyncio.timeout(4):
        while True:
            rows = (await client.call("automation.poll", {"host_id": host}))["tasks"]
            if rows:
                return rows[0]
            await asyncio.sleep(0.002)


def native_request(host, work):
    return {"host_id": host, **{key: work[key] for key in ("operation_id", "nonce", "digest")}}


def native_result(work, **extra):
    return {
        "schema": "jarvis.media.v1",
        "succeeded": True,
        "verified": True,
        "effect_status": "known",
        "source_sha256": work["arguments"]["source_sha256"],
        "kind": work["arguments"]["kind"],
        "segments": [{"locator": "image:1", "text": "OBSERVACION SINTETICA 6492"}],
        "metadata": {"width": 1, "height": 1},
        "truncated": False,
        "on_device": True,
        **extra,
    }


async def inspected_work(client, peer, ref, row):
    host = uuid4().hex
    await client.call("automation.attach", {"host_id": host, "capabilities": ["media"]})
    peer.plans["inspeccion nativa"] = [
        (
            "media_inspect",
            {
                "attachment_id": row["attachment_id"],
                "sha256": row["sha256"],
            },
        )
    ]
    run = await client.call("runs.start", {**ref, "message": "inspeccion nativa"})
    card = await approval(client, ref)
    await client.call(
        "approvals.decide",
        {**ref, "approval_id": card["approval_id"], "digest": card["digest"], "allow": True},
    )
    return host, run, await native_work(client, host)


async def test_signed_native_chunks_bind_host_operation_nonce_hash_and_keep_frame_volatile(
    media_service,
):
    from test_service_media import admit

    service, client, peer, root = media_service
    ref, source = await session(client), synthetic_png()
    row = await imported(client, ref, source, "imagen.png")
    foreign = ClientIdentity.generate()
    await admit(client, foreign)
    host, run, work = await inspected_work(client, peer, ref, row)
    request = native_request(host, work)
    read = {**request, "offset": 0}
    with pytest.raises(RpcError):
        await client.call("media.worker.read", read)
    with pytest.raises(RpcError):
        await client.call("automation.claim", {**request, "nonce": "f" * 32})
    await client.call("automation.claim", request)
    async with LocalRpcClient(root / "profile", identity=foreign) as other:
        with pytest.raises(RpcError):
            await other.call("media.worker.read", read)
    copied = await client.call("media.worker.read", read)
    assert base64.b64decode(copied["data_base64"]) == source and copied["complete"]
    assert copied["sha256"] == row["sha256"]
    with pytest.raises(RpcError):
        await client.call("media.worker.read", {**read, "offset": len(source)})
    # Synthetic worker pixels test the host envelope. They do not qualify an
    # actual Vision/PDF decoder or claim the model observed a real desktop.
    frame = b"\xff\xd8\xffSYNTHETIC_EPHEMERAL_FRAME_6492\xff\xd9"
    image = "data:image/jpeg;base64," + base64.b64encode(frame).decode()
    result = native_result(
        work,
        frame={
            "data_url": image,
            "storage": "memory_only",
            "sha256": hashlib.sha256(frame).hexdigest(),
            "bytes": len(frame),
        },
    )
    await client.call("automation.complete", {**request, "result": result})
    receipt = await settled(client, ref, run["run_id"])
    action = receipt["action_receipts"][0]
    assert action["succeeded"] and action["verified"] and row["sha256"] in action["output"]
    assert image not in json.dumps(receipt) and "data:image" not in action["output"]
    assert any(
        x == {"type": "inputImage", "imageUrl": image} for x in peer.replies[0]["contentItems"]
    )
    assert not service.media_tools.frames and not service._media_native_payloads
    assert not any(
        frame in path.read_bytes() for path in (root / "profile").rglob("*") if path.is_file()
    )
    with pytest.raises(RpcError):
        await client.call("media.worker.read", read)
    await client.call("automation.detach", {"host_id": host, "cleanup_confirmed": True})


async def test_real_ogg_derivation_is_bound_to_original_artifact_in_native_tool_receipt(
    media_service,
):
    from test_media_audio import artificial_ogg

    service, client, peer, _root = media_service
    ref, original = await session(client), artificial_ogg()
    row = await imported(client, ref, original, "nota.ogg")
    host, run, work = await inspected_work(client, peer, ref, row)
    request = native_request(host, work)
    assert work["arguments"]["mime"] == "audio/wav"
    assert work["arguments"]["attachment_sha256"] == row["sha256"]
    assert work["arguments"]["source_sha256"] != row["sha256"]
    await client.call("automation.claim", request)
    copied = await client.call("media.worker.read", {**request, "offset": 0})
    wav = base64.b64decode(copied["data_base64"])
    assert wav.startswith(b"RIFF") and copied["complete"]
    assert hashlib.sha256(wav).hexdigest() == work["arguments"]["source_sha256"]
    duration = extract_document(wav, "derivado.wav").metadata["duration_seconds"]
    result = native_result(
        work,
        segments=[{"locator": "time:0-0.25", "text": "VOZ 7254"}],
        metadata={"duration_seconds": duration},
    )
    await client.call("automation.complete", {**request, "result": result})
    receipt = await settled(client, ref, run["run_id"])
    action = receipt["action_receipts"][0]
    assert action["succeeded"] and action["verified"]
    assert row["sha256"] in action["output"] and "nota.ogg" in action["output"]
    # Durable action receipts retain a bounded preview plus full output hash.
    # The model receives the complete approved result through this RAM reply.
    delivered = json.loads(peer.replies[0]["contentItems"][0]["text"])["output"]
    assert action["output_truncated"] is True
    assert action["output_sha256"] == hashlib.sha256(delivered.encode()).hexdigest()
    assert delivered.startswith(action["output"])
    assert work["arguments"]["source_sha256"] in delivered
    assert "VOZ 7254" in delivered and "original_bytes_modified" in delivered
    assert not service._media_native_payloads and not service.media_tools.frames
    await client.call("automation.detach", {"host_id": host, "cleanup_confirmed": True})


async def test_signed_audio_permission_failure_stays_known_without_frame_or_cloud_audio(
    media_service,
):
    from test_media_audio import artificial_ogg

    service, client, peer, _root = media_service
    ref, original = await session(client), artificial_ogg()
    row = await imported(client, ref, original, "permiso.ogg")
    host, run, work = await inspected_work(client, peer, ref, row)
    request = native_request(host, work)
    await client.call("automation.claim", request)
    await client.call(
        "automation.complete",
        {
            **request,
            "result": {
                "succeeded": False,
                "verified": False,
                "effect_status": "known",
                "error": "local_audio_speech_permission_required",
            },
        },
    )
    receipt = await settled(client, ref, run["run_id"])
    action = receipt["action_receipts"][0]
    assert not action["succeeded"] and not action["verified"]
    assert action["effect_status"] == "known"
    assert action["error"] == "local_audio_speech_permission_required"
    assert not service._media_native_payloads and not service.media_tools.frames
    assert all(item["type"] != "inputImage" for item in peer.replies[0]["contentItems"])
    assert base64.b64encode(original).decode() not in json.dumps(peer.calls + peer.replies)
    await client.call("automation.detach", {"host_id": host, "cleanup_confirmed": True})
