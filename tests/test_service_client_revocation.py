"""Revocation cancels owned runs and invalidates pending approvals before effects."""

import asyncio
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.protocol import LocalRpcClient
from jarvis_core.service import JarvisService


class WritingPeer:
    async def account_status(self):
        return {"connected": True, "mode": "chatgpt"}

    async def chat(self, message, *, on_thread, on_tool_call, **options):
        on_thread("synthetic-" + uuid4().hex)
        await on_tool_call("workspace_write", {"path": "never-written.txt", "text": "NO"})
        return {"answer": "modelo sin comprobación"}

    async def aclose(self):
        pass


async def test_revoking_a_client_cancels_its_waiting_run_and_does_not_execute_or_replay():
    base = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="jvs-rev-", dir=base) as value:
        profile = Path(value) / "profile"
        owner, operator = ClientIdentity.generate(), ClientIdentity.generate()
        service = JarvisService(profile, WritingPeer(), bootstrap_public_key=owner.public_key)
        review = service.client_authority.review(
            owner.client_id,
            {"public_key": operator.public_key, "scope": "owner", "expected_revision": 1},
        )
        service.client_authority.admit(
            owner.client_id, {key: review[key] for key in ("review_id", "review_digest")}
        )
        await service.start()
        try:
            async with LocalRpcClient(profile, identity=owner) as admin:
                async with LocalRpcClient(profile, identity=operator) as client:
                    session = (await client.call("sessions.create", {"title": "Sintética"}))[
                        "session"
                    ]
                    ref = {key: session[key] for key in ("session_id", "temporary")}
                    run = await client.call("runs.start", {**ref, "message": "proponer"})
                    async with asyncio.timeout(5):
                        while not (
                            pending := (await admin.call("approvals.list", ref))["approvals"]
                        ):
                            await asyncio.sleep(0.01)
                    await admin.call(
                        "clients.revoke", {"client_id": operator.client_id, "expected_revision": 2}
                    )
                    decision = await admin.call(
                        "approvals.decide",
                        {
                            **ref,
                            "approval_id": pending[0]["approval_id"],
                            "digest": pending[0]["digest"],
                            "allow": True,
                        },
                    )
                    assert decision == {"accepted": False}
                    async with asyncio.timeout(5):
                        while (
                            result := (
                                await admin.call("runs.get", {**ref, "run_id": run["run_id"]})
                            )["run"]
                        )["status"] not in {"completed", "cancelled", "failed", "interrupted"}:
                            await asyncio.sleep(0.01)
                    assert result["status"] == "cancelled"
                    assert result["error"] == "client_revoked"
                    assert not (profile / "workspace" / "never-written.txt").exists()
                    assert (await admin.call("approvals.list", ref))["approvals"] == []
        finally:
            await service.aclose()
