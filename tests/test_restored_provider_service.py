"""Restored-profile SDK policy using owned fake peers and actual local controls."""
import argparse
import asyncio
import json
import os
from pathlib import Path

import pytest
from test_activation import prepared as prepared
from test_codex import executable as executable
from test_recovery import bundle as bundle
from test_recovery import ctx
from test_service import ModelPeer

import jarvis_core.service as module
from jarvis_core.activation import activate_prepared_profile, review_prepared_profile
from jarvis_core.codex import CodexClient
from jarvis_core.host_profile import HostProfileError
from jarvis_core.provider_choice import ProviderChoiceError
from jarvis_core.service import JarvisService
from jarvis_core.state import SessionRef


@pytest.fixture
def activated(prepared):
    activate_prepared_profile(review_prepared_profile(prepared, ctx()), ctx())
    return prepared


class CountPeer(ModelPeer):
    def __init__(self):
        super().__init__()
        self.status_calls = self.login_calls = self.logout_calls = 0
        self.root = None

    async def account_status(self):
        self.status_calls += 1
        return await super().account_status()

    async def login(self):
        self.login_calls += 1
        assert self.root is not None
        value = json.loads((self.root / "provider_choice.json").read_bytes())
        assert value["provider"] == "codex_chatgpt" and value["allowed"]
        return await super().login()

    async def logout(self):
        self.logout_calls += 1
        return await super().logout()


async def test_first_restored_status_and_run_have_no_provider_io(activated):
    peer = CountPeer()
    service = JarvisService(activated, peer)
    try:
        pure = await service.local_status({})
        assert pure["restore_admission"]["state"] == "local_present_valid"
        assert pure["provider_choice"] == {
            "required": True, "selected": False, "query_status_allowed": False, "revision": 0}
        assert (await service.status({}))["provider"] == {"connected": False, "mode": None}
        with pytest.raises(ProviderChoiceError, match="provider_choice_required"):
            await service.start_run({"session_id": "a"*32, "message": "No debe llegar al SDK"})
        assert not service._handles
        assert len(service.store.list_runs(SessionRef("a"*32))) == 1
        assert (peer.status_calls, peer.login_calls,
                peer.logout_calls, len(peer.calls)) == (0,0,0,0)
        assert not (activated / "provider_choice.json").exists()
        assert not (activated / "provider").exists()
    finally:
        await service.aclose()


async def test_explicit_connection_intention_survives_restart_without_forcing_login(activated):
    first = CountPeer()
    first.root = activated
    service = JarvisService(activated, first)
    await service.login({})
    assert first.login_calls == 1
    assert (await service.status({}))["provider"]["connected"]
    await service.aclose()
    second = CountPeer()
    second.root = activated
    service = JarvisService(activated, second)
    try:
        assert (await service.local_status({}))["provider_choice"]["selected"]
        assert (await service.status({}))["provider"]["connected"]
        assert second.status_calls == 1 and second.login_calls == 0
    finally:
        await service.aclose()


async def test_intention_write_failure_prevents_sdk_start_and_latches_off(activated, monkeypatch):
    peer = CountPeer()
    peer.root = activated
    service = JarvisService(activated, peer)
    import jarvis_core.provider_choice as choice_module
    real_open = os.open
    def denied(path, flags, *args, **kwargs):
        if str(path).startswith(".jarvis-provider-choice-"):
            raise PermissionError("fixture only")
        return real_open(path, flags, *args, **kwargs)
    try:
        monkeypatch.setattr(choice_module.os, "open", denied)
        with pytest.raises(ProviderChoiceError):
            await service.login({})
        assert not service.provider_choice.allowed
        assert peer.login_calls == peer.status_calls == 0
        assert not (activated / "provider_choice.json").exists()
    finally:
        await service.aclose()


async def test_late_account_read_after_logout_cannot_revive_connection(activated):
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowPeer(CountPeer):
        async def account_status(self):
            self.status_calls += 1
            entered.set()
            await release.wait()
            return {"connected": True, "mode": "chatgpt"}
    peer = SlowPeer()
    peer.root = activated
    service = JarvisService(activated, peer)
    try:
        await service.login({})
        old = asyncio.create_task(service.status({}))
        await asyncio.wait_for(entered.wait(), 2)
        out = await service.logout({})
        assert out["logout_confirmed"]
        release.set()
        assert (await old)["provider"] == {"connected": False, "mode": None}
        assert not service.provider_choice.allowed
        assert (await service.status({}))["provider"]["connected"] is False
        assert peer.status_calls == 1
    finally:
        release.set()
        await service.aclose()


async def test_old_status_epoch_does_not_disarm_new_explicit_choice(activated):
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowPeer(CountPeer):
        async def account_status(self):
            entered.set()
            await release.wait()
            return {"connected": True, "mode": "chatgpt"}
    peer = SlowPeer()
    peer.root = activated
    service = JarvisService(activated, peer)
    try:
        await service.login({})
        old = asyncio.create_task(service.status({}))
        await asyncio.wait_for(entered.wait(), 2)
        await service.logout({})
        await service.login({})
        release.set()
        assert (await old)["provider"]["connected"] is False
        assert service.provider_choice.allowed
        assert (await service.status({}))["provider"]["connected"]
    finally:
        release.set()
        await service.aclose()


async def test_logout_api_failure_reports_account_erasure_unconfirmed(activated):
    class FailedLogout(CountPeer):
        async def logout(self):
            raise RuntimeError("PRIVATE PEER BODY")
    peer = FailedLogout()
    peer.root = activated
    service = JarvisService(activated, peer)
    try:
        await service.login({})
        response = await service.logout({})
        assert response == {"connected": False, "mode": None, "logout_confirmed": False,
                            "error_code": "profile_logout_unconfirmed"}
        assert not service.provider_choice.allowed
        assert "PRIVATE" not in json.dumps(response)
    finally:
        await service.aclose()


async def test_revoked_choice_during_model_await_rejects_answer(activated):
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowModel(CountPeer):
        async def chat(self, message, *, on_thread, **options):
            on_thread("fixture-owned-thread")
            entered.set()
            await release.wait()
            return {"thread_id": "fixture-owned-thread", "answer": "STALE ANSWER"}
    peer = SlowModel()
    peer.root = activated
    service = JarvisService(activated, peer)
    try:
        await service.login({})
        ref = (await service.create_session({}))["session"]
        start = await service.start_run({"session_id": ref["session_id"], "message": "synthetic"})
        await asyncio.wait_for(entered.wait(), 2)
        # Simulate a host revocation between awaits; real logout refuses active runs.
        service.provider_choice.disable()
        release.set()
        await asyncio.wait_for(service._settlers[start["run_id"]], 3)
        run = service.store.get_run(SessionRef(ref["session_id"]), start["run_id"])
        assert run.status == "failed"
        assert "STALE ANSWER" not in str(run.receipt_json)
        assert not service._provider_epochs
    finally:
        release.set()
        await service.aclose()


async def test_restored_codex_client_is_not_started_for_status_or_logout(activated, executable):
    client = CodexClient(executable, activated / "provider")
    service = JarvisService(activated, client)
    try:
        assert (await service.status({}))["provider"]["connected"] is False
        assert (await service.logout({}))["logout_confirmed"] is False
        assert client._process is None
        assert not (activated / "provider").exists()
    finally:
        await service.aclose()


async def test_foreign_provider_override_rejected_before_client_factory(activated, monkeypatch):
    called = []
    def no_client(*args, **kwargs):
        called.append(True)
        raise AssertionError("client constructor must remain unreachable")
    monkeypatch.setattr(module, "CodexClient", no_client)
    args = argparse.Namespace(state=activated, codex=Path("/synthetic/codex"),
                              provider_state=activated.parent / "old-provider")
    with pytest.raises(HostProfileError, match="profile_provider_override_forbidden"):
        await module._serve(args)
    assert not called


@pytest.mark.parametrize("flag", ["device", "inode", "marker_sha256",
                                  "admission_sha256", "operation_id"])
async def test_partial_bootstrap_expectations_fail_before_any_new_directory(tmp_path, flag):
    path = tmp_path / "must-not-exist"
    args = argparse.Namespace(state=path, codex=Path("/fixture/absent"), provider_state=None)
    setattr(args, "expected_restore_" + flag, "1")
    with pytest.raises(HostProfileError, match="profile_expected_restore_incomplete"):
        await module._serve(args)
    assert not path.exists()


async def test_codex_start_policy_is_checked_after_start_lock_before_process(tmp_path, executable):
    client = CodexClient(executable, tmp_path / "new-sdk")
    calls = []
    def reject(action):
        calls.append(action)
        raise ProviderChoiceError("provider_choice_required")
    client.before_io = reject
    async with client._start_lock:
        pending = asyncio.create_task(client.start())
        await asyncio.sleep(0)
        assert not calls
    with pytest.raises(ProviderChoiceError):
        await pending
    assert calls == ["start"] and client._process is None
    assert not client.directory.exists()
    await client.aclose()


async def test_codex_final_write_guard_drops_request_revoked_behind_lock(tmp_path, executable):
    client = CodexClient(executable, tmp_path / "sdk")
    await client.account_status()
    methods = []
    allowed = True
    def guard(method):
        methods.append(method)
        if not allowed:
            raise ProviderChoiceError("provider_choice_epoch_stale")
    client.before_io = guard
    try:
        async with client._write_lock:
            call = asyncio.create_task(client.request("account/read", {"refreshToken": False}))
            await asyncio.sleep(0)
            allowed = False
        with pytest.raises(ProviderChoiceError):
            await call
        assert methods == ["account/read"]
        assert not client._pending
    finally:
        client.before_io = None
        await client.aclose()


async def test_restored_backup_and_doctor_validate_admission_but_export_no_local_controls(
        activated, tmp_path):
    from test_maintenance_integration import allow_synthetic_owner, finish
    peer = CountPeer()
    peer.root = activated
    service = JarvisService(activated, peer)
    coordinator = service.maintenance_coordinator
    try:
        # No selection is needed for either pure local report or host backup.
        report = await coordinator.doctor({})
        report = await finish(coordinator, report["operation_id"])
        assert report["phase"] == "complete", report
        check = next(row for row in report["result"]["report"]["checks"]
                     if row["role"] == "restore_record")
        assert check["status"] == "ok" and check["code"] == "doctor_local_admission_valid"
        await service.login({})
        review = await coordinator.review_backup({"destination_parent": str(tmp_path),
                                                  "name": "after-activation-backup"})
        operation = await coordinator.backup({key: review[key] for key in
                                            ("review_id", "review_digest")})
        await allow_synthetic_owner(coordinator)
        result = await finish(coordinator, operation["operation_id"])
        assert result["phase"] == "complete", result
        files = {path.name for path in (tmp_path / "after-activation-backup").rglob("*")}
        assert "restore_record.json" not in files
        assert "restore_admission.json" not in files
        assert "provider_choice.json" not in files
        assert peer.status_calls == 0
    finally:
        await service.aclose()


@pytest.mark.parametrize("fault", ["corrupt_control", "disable_io_failure"])
async def test_faulted_choice_cannot_block_closing_already_owned_codex(
        activated, executable, monkeypatch, fault):
    import jarvis_core.provider_choice as choice_module
    client = CodexClient(executable, activated / "provider")
    service = JarvisService(activated, client)
    try:
        await service.login({})
        process = client._process
        assert process is not None and process.returncode is None
        if fault == "corrupt_control":
            (activated / "provider_choice.json").write_bytes(b"INVALID CONTROL FIXTURE")
        else:
            real_open = os.open
            def denied(path, flags, *args, **kwargs):
                if str(path).startswith(".jarvis-provider-choice-"):
                    raise PermissionError("fixture only")
                return real_open(path, flags, *args, **kwargs)
            monkeypatch.setattr(choice_module.os, "open", denied)
        with pytest.raises(ProviderChoiceError):
            await service.logout({})
        assert not service.provider_choice.allowed
        assert (await service.status({}))["provider"]["connected"] is False
        # Cleanup depends on known process ownership, not on a still-valid choice.
        await service.aclose()
        assert process.returncode is not None and client._process is None
    finally:
        await service.aclose()


async def test_pending_login_never_admits_channel_or_rpc_run_before_connection_settles(activated):
    from jarvis_core.protocol import RpcError
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowLogin(CountPeer):
        async def login(self):
            entered.set()
            await release.wait()
            return {"url":"https://auth.openai.com/fixture"}
    peer = SlowLogin()
    peer.root = activated
    service = JarvisService(activated, peer)
    login = asyncio.create_task(service.login({}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(RpcError):
            await service.start_run({"session_id":"a"*32, "message":"must wait for login"})
        assert not peer.calls
    finally:
        release.set()
        await asyncio.gather(login, return_exceptions=True)
        await service.aclose()


async def test_logout_revokes_pending_login_without_second_sdk_or_late_url(activated):
    from jarvis_core.protocol import RpcError
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowLogin(CountPeer):
        async def login(self):
            self.login_calls += 1
            entered.set()
            await release.wait()
            return {"url":"https://auth.openai.com/fixture"}
    peer = SlowLogin()
    peer.root = activated
    service = JarvisService(activated, peer)
    login = asyncio.create_task(service.login({}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(RpcError):
            await service.login({})
        assert not (await service.status({}))["provider"]["connected"]
        assert peer.status_calls == 0
        out = await service.logout({})
        assert not out["logout_confirmed"] and not service.provider_choice.allowed
        assert peer.logout_calls == 0 and peer.login_calls == 1
        release.set()
        with pytest.raises(ProviderChoiceError, match="provider_choice_epoch_stale"):
            await login
        assert service._provider_transition is None
    finally:
        release.set()
        await asyncio.gather(login, return_exceptions=True)
        await service.aclose()
