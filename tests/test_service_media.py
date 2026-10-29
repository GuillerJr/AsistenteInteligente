"""Signed own RPC, bounded RAM uploads and actual CAS; no owner files/accounts."""

import asyncio
import base64
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from test_service import session, settled
from test_service_telegram import TelegramPeer

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.media_documents import MediaError
from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.service import JarvisService
from jarvis_core.state import SessionRef


@pytest.fixture
async def media_service():
    parent = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    root = Path(tempfile.mkdtemp(prefix="jr-media-", dir=parent)).resolve()
    peer = TelegramPeer()
    service = JarvisService(root / "profile", peer)
    client = LocalRpcClient(root / "profile", max_frame_bytes=1024 * 1024)
    await service.start()
    await client.connect()
    try:
        yield service, client, peer, root
    finally:
        peer.release.set()
        await client.aclose()
        await service.aclose()
        shutil.rmtree(root)


async def begin(client, ref, payload=b"MARCADOR DOCUMENTO 7314", name="prueba.txt", **extra):
    return await client.call(
        "media.upload.begin",
        {
            **ref,
            "name": name,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            **extra,
        },
    )


async def chunk(client, upload_id, payload, offset=0):
    return await client.call(
        "media.upload.chunk",
        {
            "upload_id": upload_id,
            "offset": offset,
            "data_base64": base64.b64encode(payload).decode(),
        },
    )


async def imported(client, ref, payload=b"MARCADOR DOCUMENTO 7314", name="prueba.txt"):
    upload = await begin(client, ref, payload, name)
    await chunk(client, upload["upload_id"], payload)
    result = await client.call("media.upload.commit", {"upload_id": upload["upload_id"]})
    return result["attachment"]


async def admit(client, identity):
    value = await client.call(
        "clients.review",
        {
            "public_key": identity.public_key,
            "scope": "owner",
            "expected_revision": 1,
        },
    )
    return await client.call(
        "clients.admit", {key: value[key] for key in ("review_id", "review_digest")}
    )


async def test_ram_upload_exact_chunks_and_commit_are_idempotent_without_model(media_service):
    service, client, peer, root = media_service
    ref = await session(client)
    payload = b"MARCADOR DOCUMENTO 7314"
    upload = await begin(client, ref, payload, upload_id=uuid4().hex)
    again = await begin(client, ref, payload, upload_id=upload["upload_id"])
    assert upload == again and upload["status"] == "receiving"
    assert not (await client.call("media.list", ref))["attachments"]
    assert not any(payload in path.read_bytes() for path in root.rglob("*") if path.is_file())
    part = payload[:7]
    first = await chunk(client, upload["upload_id"], part)
    assert first == await chunk(client, upload["upload_id"], part)
    with pytest.raises(RpcError):
        await chunk(client, upload["upload_id"], b"cambio", 0)
    with pytest.raises(RpcError):
        await chunk(client, upload["upload_id"], payload[7:], 8)
    await chunk(client, upload["upload_id"], payload[7:], 7)
    result = await client.call("media.upload.commit", {"upload_id": upload["upload_id"]})
    assert result == await client.call("media.upload.commit", {"upload_id": upload["upload_id"]})
    assert result["status"] == "complete" and result["received"] == len(payload)
    rows = (await client.call("media.list", ref))["attachments"]
    assert len(rows) == 1 and rows[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    read = await client.call(
        "media.read",
        {
            **ref,
            "attachment_id": rows[0]["attachment_id"],
            "sha256": rows[0]["sha256"],
            "offset": 0,
        },
    )
    assert base64.b64decode(read["data_base64"]) == payload and read["complete"]
    assert not peer.calls and not service._media_uploads[upload["upload_id"]]["data"]
    assert service._media_uploads[upload["upload_id"]]["expiry"] is None


async def test_upload_owner_binding_hash_size_and_same_id_conflicts(media_service):
    service, client, peer, root = media_service
    ref = await session(client)
    payload = b"UNO DOS"
    upload = await begin(client, ref, payload)
    with pytest.raises(RpcError):
        await begin(client, ref, b"OTRO", upload_id=upload["upload_id"])
    with pytest.raises(RpcError):
        await client.call("media.upload.status", {"upload_id": []})
    other = ClientIdentity.generate()
    await admit(client, other)
    async with LocalRpcClient(root / "profile", identity=other) as foreign:
        with pytest.raises(RpcError):
            await foreign.call("media.upload.status", {"upload_id": upload["upload_id"]})
        with pytest.raises(RpcError):
            await chunk(foreign, upload["upload_id"], payload)
    await chunk(client, upload["upload_id"], b"FALSO!!")
    with pytest.raises(RpcError):
        await client.call("media.upload.commit", {"upload_id": upload["upload_id"]})
    assert not (await client.call("media.list", ref))["attachments"] and not peer.calls
    cancelled = await client.call("media.upload.cancel", {"upload_id": upload["upload_id"]})
    assert cancelled["status"] == "cancelled" and cancelled["received"] == 0
    assert not service._media_uploads[upload["upload_id"]]["data"]


async def test_upload_expiry_runs_without_another_rpc_and_releases_lease(
    media_service, monkeypatch
):
    from jarvis_core import media_management

    service, client, _peer, _root = media_service
    monkeypatch.setattr(media_management, "_UPLOAD_TTL_SECONDS", 0.025)
    upload = await begin(client, await session(client))
    row = service._media_uploads[upload["upload_id"]]
    assert row["expiry"] is not None
    async with asyncio.timeout(2):
        while row["status"] == "receiving":
            await asyncio.sleep(0.002)
    assert row["status"] == "expired" and not row["data"] and row["expiry"] is None
    assert not any(x.label == "media.upload.owner" for x in service.profile_gate._admitted.values())


@pytest.mark.parametrize("unknown", [True, False])
async def test_uncertain_or_rejected_publication_never_replays(media_service, monkeypatch, unknown):
    service, client, _peer, _root = media_service
    ref = await session(client)
    upload = await begin(client, ref)
    await chunk(client, upload["upload_id"], b"MARCADOR DOCUMENTO 7314")
    store, calls = service._media_store(), []
    original = store.ingest_bytes

    def fail(*args, **kwargs):
        calls.append(1)
        if unknown:
            original(*args, **kwargs)
            raise OSError("synthetic acknowledgement lost after own publication")
        raise MediaError("media_attachment_invalid")

    monkeypatch.setattr(store, "ingest_bytes", fail)
    with pytest.raises(RpcError):
        await client.call("media.upload.commit", {"upload_id": upload["upload_id"]})
    observed = await client.call("media.upload.status", {"upload_id": upload["upload_id"]})
    assert observed["status"] == ("publication_unknown" if unknown else "failed")
    with pytest.raises(RpcError):
        await client.call("media.upload.commit", {"upload_id": upload["upload_id"]})
    assert len(calls) == 1 and observed["permits_replay"] is False
    assert len((await client.call("media.list", ref))["attachments"]) == int(unknown)


async def test_revoked_upload_owner_loses_ram_and_cannot_borrow_admin(media_service):
    service, admin, _peer, root = media_service
    ref, operator = await session(admin), ClientIdentity.generate()
    await admit(admin, operator)
    async with LocalRpcClient(root / "profile", identity=operator) as client:
        upload = await begin(client, ref)
        await chunk(client, upload["upload_id"], b"PARCIAL")
        await admin.call(
            "clients.revoke", {"client_id": operator.client_id, "expected_revision": 2}
        )
    row = service._media_uploads[upload["upload_id"]]
    assert row["status"] == "cancelled" and not row["data"] and row["expiry"] is None
    with pytest.raises(RpcError):
        await admin.call("media.upload.commit", {"upload_id": upload["upload_id"]})
    assert not (await admin.call("media.list", ref))["attachments"]


async def test_temporary_attachment_stays_ram_scoped_and_revoke_removes_access(media_service):
    service, client, peer, root = media_service
    ref = await session(client, temporary=True)
    other = await session(client)
    payload = b"TEMPORAL SOLO RAM 5482"
    row = await imported(client, ref, payload)
    assert row["temporary"] is True
    assert not any(payload in path.read_bytes() for path in root.rglob("*") if path.is_file())
    assert not (await client.call("media.list", other))["attachments"]
    read = {"attachment_id": row["attachment_id"], "sha256": row["sha256"], "offset": 0}
    with pytest.raises(RpcError):
        await client.call("media.read", {**other, **read})
    await client.call(
        "media.revoke", {**ref, "attachment_id": row["attachment_id"], "sha256": row["sha256"]}
    )
    with pytest.raises(RpcError):
        await client.call("media.read", {**ref, **read})
    assert not peer.calls
    service._media_store().clear_temporary(
        service._media_scope(SessionRef(ref["session_id"], True))
    )
    assert not (await client.call("media.list", ref))["attachments"]


async def test_request_identity_binds_exact_attachment_selection_before_model(media_service):
    _service, client, peer, _root = media_service
    ref = await session(client)
    a = await imported(client, ref, b"DOCUMENTO A")
    b = await imported(client, ref, b"DOCUMENTO B")
    identity = uuid4().hex
    request = {
        **ref,
        "message": "revisa metadatos",
        "request_id": identity,
        "attachment_ids": [a["attachment_id"]],
    }
    await client.call("runs.start", request)
    await settled(client, ref, identity)
    await client.call("runs.start", request)
    with pytest.raises(RpcError):
        await client.call("runs.start", {**request, "attachment_ids": [b["attachment_id"]]})
    assert len(peer.calls) == 1
    context = peer.calls[0][1]["context_data"]
    assert a["sha256"] in context and b["sha256"] not in context and "DOCUMENTO A" not in context


async def test_playback_ogg_uses_real_local_codec_and_revocation_invalidates_cache(media_service):
    from test_media_audio import artificial_ogg

    service, client, peer, _root = media_service
    ref = await session(client)
    original = artificial_ogg()
    row = await imported(client, ref, original, "voz.ogg")
    args = {**ref, "attachment_id": row["attachment_id"], "sha256": row["sha256"], "offset": 0}
    response = await client.call("media.playback.read", args)
    decoded = base64.b64decode(response["data_base64"])
    assert decoded.startswith(b"RIFF") and response["payload_mime"] == "audio/wav"
    assert response["payload_sha256"] == hashlib.sha256(decoded).hexdigest()
    assert response["payload_sha256"] != row["sha256"] and response["complete"]
    cache = service._media_playback
    assert (await client.call("media.playback.read", args)) == response
    assert service._media_playback is cache
    await client.call(
        "media.revoke", {**ref, "attachment_id": row["attachment_id"], "sha256": row["sha256"]}
    )
    with pytest.raises(RpcError):
        await client.call("media.playback.read", args)
    assert not peer.calls
