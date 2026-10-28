"""Own durable event workflows and an explicitly opened, authenticated loopback inbox.

Hooks enqueue immutable prompt checkpoints. They never run code or authorize tools.
The host dispatches each claim through its ordinary runtime and authority. A claim
is durable before dispatch; interruption is recorded as unknown and is not replayed.
Webhook keys live only in the listener, outside the portable document.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jarvis_core.private_json import PrivateJsonDocument, PrivateJsonError
from jarvis_core.scheduler import DispatchResult

_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_STATUSES = _TERMINAL | {"queued", "running", "paused"}
_HOOK_KEYS = {"hook_id", "title", "event", "agent_id", "workspace_id", "steps", "enabled",
              "revision", "created_at", "updated_at"}
_FLOW_KEYS = {"flow_id", "hook_id", "hook_revision", "title", "agent_id", "workspace_id",
              "steps", "event_id", "payload", "payload_sha256", "source", "status", "outcome",
              "cursor", "revision", "created_at", "updated_at", "expires_at", "error_code",
              "run_ids", "claim_id", "claim_run_id", "receipts"}


class AutomationFlowError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code="automation_document_invalid"):
    raise AutomationFlowError(code)


def _text(value, maximum, *, pattern=None):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        _fail("automation_value_invalid")
    try:
        if len(value.encode()) > maximum or (pattern and re.fullmatch(pattern, value) is None):
            _fail("automation_value_invalid")
    except UnicodeError:
        _fail("automation_value_invalid")
    return value


def _id(value):
    return _text(value, 32, pattern=r"[a-f0-9]{32}")


def _revision(value):
    if type(value) is not int or value < 1:
        _fail("automation_revision_invalid")
    return value


def _time(value):
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value < 2**48:
        _fail("automation_time_invalid")
    return float(value)


def _steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
        _fail("automation_steps_invalid")
    for row in steps:
        if (not isinstance(row, dict) or set(row) != {"prompt", "pause_after"}
                or type(row["pause_after"]) is not bool):
            _fail("automation_steps_invalid")
        _text(row["prompt"], 8000)
    return steps


def _encoded(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("automation_payload_invalid")


def _payload(value):
    if not isinstance(value, dict) or len(_encoded(value)) > 16384:
        _fail("automation_payload_invalid")
    return value


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("automation_payload_invalid")
        value[key] = item
    return value


@dataclass(frozen=True)
class StepClaim:
    flow_id: str
    claim_id: str
    hook_id: str
    title: str
    prompt: str
    agent_id: str
    workspace_id: str
    payload: dict
    step_index: int


class AutomationFlowStore:
    """Bounded private JSON transactions; no implicit executor or event source."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        path = Path(path)
        if path.name != "automation.json" or not callable(clock):
            _fail("automation_configuration_invalid")
        self.clock = clock
        self.pending_owners = []
        self.document = PrivateJsonDocument(
            path.parent, path.name, initial=lambda: {"version": 1, "revision": 1,
                                                     "hooks": [], "flows": []},
            validate=self._validate, max_bytes=4194304)

    @staticmethod
    def _validate(data):
        if (not isinstance(data, dict) or set(data) != {"version", "revision", "hooks", "flows"}
                or type(data["version"]) is not int or data["version"] != 1
                or not isinstance(data["hooks"], list) or len(data["hooks"]) > 32
                or not isinstance(data["flows"], list) or len(data["flows"]) > 256):
            _fail()
        _revision(data["revision"])
        hooks, flows, events = set(), set(), set()
        for row in data["hooks"]:
            if not isinstance(row, dict) or set(row) != _HOOK_KEYS:
                _fail()
            _id(row["hook_id"])
            if row["hook_id"] in hooks or type(row["enabled"]) is not bool:
                _fail()
            hooks.add(row["hook_id"])
            _text(row["title"], 512)
            _text(row["event"], 64, pattern=r"[a-z][a-z0-9_.-]{0,63}")
            _text(row["agent_id"], 64, pattern=r"[A-Za-z0-9_-]{1,64}")
            _text(row["workspace_id"], 128, pattern=r"[A-Za-z0-9_-]{1,128}")
            _steps(row["steps"])
            _revision(row["revision"])
            _time(row["created_at"]), _time(row["updated_at"])
            if row["updated_at"] < row["created_at"]:
                _fail()
        if sum(len(step["prompt"].encode()) for row in data["hooks"]
               for step in row["steps"]) > 262144:
            _fail("automation_hook_bytes_limit")
        # Local RPC escapes non-ASCII characters. The review limit is measured
        # in those actual wire bytes, including metadata, rather than UTF-8 only.
        if len(json.dumps(data["hooks"], ensure_ascii=True).encode()) > 262144:
            _fail("automation_hook_bytes_limit")
        for row in data["flows"]:
            if not isinstance(row, dict) or set(row) != _FLOW_KEYS:
                _fail()
            _id(row["flow_id"]), _id(row["hook_id"])
            _revision(row["revision"]), _revision(row["hook_revision"])
            _text(row["event_id"], 128, pattern=r"[A-Za-z0-9_.:-]{1,128}")
            if row["flow_id"] in flows or (row["hook_id"], row["event_id"]) in events:
                _fail()
            flows.add(row["flow_id"])
            events.add((row["hook_id"], row["event_id"]))
            _text(row["title"], 512)
            _text(row["agent_id"], 64, pattern=r"[A-Za-z0-9_-]{1,64}")
            _text(row["workspace_id"], 128, pattern=r"[A-Za-z0-9_-]{1,128}")
            _steps(row["steps"]), _payload(row["payload"])
            if (row["payload_sha256"] != hashlib.sha256(_encoded(row["payload"])).hexdigest()
                    or not isinstance(row["source"], str)
                    or row["source"] not in {"local", "webhook"}
                    or not isinstance(row["status"], str) or row["status"] not in _STATUSES
                    or not isinstance(row["outcome"], str) or row["outcome"] not in {
                        "pending", "known", "unknown"}
                    or type(row["cursor"]) is not int
                    or not 0 <= row["cursor"] <= len(row["steps"])
                    or not isinstance(row["run_ids"], list) or len(row["run_ids"]) > 16
                    or not isinstance(row["receipts"], list) or len(row["receipts"]) > 16):
                _fail()
            for identity in row["run_ids"]:
                _id(identity)
            if len(set(row["run_ids"])) != len(row["run_ids"]):
                _fail()
            if row["claim_id"] is not None:
                _id(row["claim_id"])
            if row["claim_run_id"] is not None:
                _id(row["claim_run_id"])
                if row["claim_run_id"] not in row["run_ids"]:
                    _fail()
            claim_ids = set()
            for index, receipt in enumerate(row["receipts"]):
                if (not isinstance(receipt, dict) or set(receipt) != {
                        "step_index", "claim_id", "run_id", "status", "outcome", "error_code"}
                        or type(receipt["step_index"]) is not int
                        or receipt["step_index"] != index
                        or not isinstance(receipt["status"], str)
                        or receipt["status"] not in _TERMINAL
                        or not isinstance(receipt["outcome"], str)
                        or receipt["outcome"] not in {"known", "unknown"}
                        or (receipt["status"] == "completed" and receipt["outcome"] != "known")):
                    _fail()
                _id(receipt["claim_id"])
                if receipt["claim_id"] in claim_ids:
                    _fail()
                claim_ids.add(receipt["claim_id"])
                if receipt["run_id"] is not None:
                    _id(receipt["run_id"])
                    if receipt["run_id"] not in row["run_ids"]:
                        _fail()
                if receipt["error_code"] is not None:
                    _text(receipt["error_code"], 128, pattern=r"[a-z][a-z0-9_]{0,127}")
            if row["error_code"] is not None:
                _text(row["error_code"], 128, pattern=r"[a-z][a-z0-9_]{0,127}")
            for key in ("created_at", "updated_at", "expires_at"):
                _time(row[key])
            if row["updated_at"] < row["created_at"] or row["expires_at"] <= row["created_at"]:
                _fail()
            if (len(row["receipts"]) not in {row["cursor"], row["cursor"] + 1}
                    or (row["status"] in {"queued", "running", "paused", "completed"}
                        and len(row["receipts"]) != row["cursor"])):
                _fail()
            if ((row["status"] in {"queued", "running"} and row["outcome"] != "pending")
                    or (row["status"] in {"paused", "completed"} and row["outcome"] != "known")
                    or (row["status"] in _TERMINAL and row["outcome"] == "pending")
                    or (row["status"] == "running" and row["claim_id"] is None)
                    or (row["status"] == "completed" and row["cursor"] != len(row["steps"]))
                    or (row["status"] in {"queued", "running", "paused"}
                        and row["cursor"] == len(row["steps"]))):
                _fail()

    @property
    def pending_cleanup(self):
        return bool(self.pending_owners)

    def _change(self, change):
        if self.pending_cleanup:
            _fail("automation_cleanup_pending")
        def update(data):
            before = _encoded(data)
            change(data)
            if _encoded(data) != before:
                data["revision"] += 1
            return data
        try:
            return self.document.update(update, before_publish=lambda _staging: None)
        except PrivateJsonError as error:
            if getattr(error, "owner", None) is not None:
                self.pending_owners.append(error.owner)
            raise

    def close(self):
        for owner in tuple(self.pending_owners):
            owner.cleanup()
            self.pending_owners.remove(owner)

    def snapshot(self):
        data = self.document.read()
        return {**data, "configuration_hash": hashlib.sha256(_encoded(data["hooks"])).hexdigest()}

    def list_hooks(self):
        return self.document.read()["hooks"]

    def list_flows(self):
        return self.document.read()["flows"]

    @staticmethod
    def _find(data, collection, identity):
        _id(identity)
        key = "hook_id" if collection == "hooks" else "flow_id"
        row = next((r for r in data[collection] if r[key] == identity), None)
        if row is None:
            _fail("automation_not_found")
        return row

    @staticmethod
    def _cas(row, expected_revision):
        _revision(expected_revision)
        if row["revision"] != expected_revision:
            _fail("automation_revision_conflict")

    def get_hook(self, hook_id):
        return self._find(self.document.read(), "hooks", hook_id)

    def get_flow(self, flow_id):
        return self._find(self.document.read(), "flows", flow_id)

    def create_hook(self, title, event, agent_id, workspace_id, steps):
        now, identity = _time(self.clock()), uuid4().hex
        row = {"hook_id": identity, "title": title, "event": event, "agent_id": agent_id,
               "workspace_id": workspace_id, "steps": steps, "enabled": False, "revision": 1,
               "created_at": now, "updated_at": now}
        def change(data):
            if len(data["hooks"]) >= 32:
                _fail("automation_hook_limit")
            data["hooks"].append(row)
        self._change(change)
        return self.get_hook(identity)

    def update_hook(self, hook_id, expected_revision, *, enabled=None, title=None,
                    event=None, steps=None):
        def change(data):
            row = self._find(data, "hooks", hook_id)
            self._cas(row, expected_revision)
            for key, value in {"enabled": enabled, "title": title, "event": event,
                               "steps": steps}.items():
                if value is not None:
                    row[key] = value
            row["revision"] += 1
            row["updated_at"] = max(row["updated_at"], _time(self.clock()))
            for flow in data["flows"]:
                if flow["hook_id"] == hook_id:
                    if flow["status"] in {"queued", "paused"}:
                        self._stop_flow(flow, "cancelled", "known", "automation_hook_changed")
                    elif flow["status"] == "running":
                        self._interrupt(flow, "automation_hook_changed")
        self._change(change)
        return self.get_hook(hook_id)

    def remove_hook(self, hook_id, expected_revision):
        def change(data):
            row = self._find(data, "hooks", hook_id)
            self._cas(row, expected_revision)
            data["hooks"].remove(row)
            for flow in data["flows"]:
                if flow["hook_id"] == hook_id:
                    if flow["status"] in {"queued", "paused"}:
                        self._stop_flow(flow, "cancelled", "known", "automation_hook_removed")
                    elif flow["status"] == "running":
                        self._interrupt(flow, "automation_hook_removed")
        self._change(change)
        return True

    def emit(self, event, event_id, payload, *, hook_id=None, source="local",
             expected_revision=None, ttl_seconds=86400):
        _text(event, 64, pattern=r"[a-z][a-z0-9_.-]{0,63}")
        _text(event_id, 128, pattern=r"[A-Za-z0-9_.:-]{1,128}")
        _payload(payload)
        if source not in {"local", "webhook"} or type(ttl_seconds) is not int or not (
                1 <= ttl_seconds <= 604800):
            _fail("automation_event_invalid")
        digest, ids = hashlib.sha256(_encoded(payload)).hexdigest(), []
        now = _time(self.clock())
        def change(data):
            selected = ([self._find(data, "hooks", hook_id)] if hook_id is not None
                        else data["hooks"])
            for hook in selected:
                if expected_revision is not None:
                    self._cas(hook, expected_revision)
                if not hook["enabled"] or hook["event"] != event:
                    if hook_id is not None:
                        _fail("automation_hook_unavailable")
                    continue
                old = next((r for r in data["flows"] if r["hook_id"] == hook["hook_id"]
                            and r["event_id"] == event_id), None)
                if old is not None:
                    if old["payload_sha256"] != digest or old["source"] != source:
                        _fail("automation_event_conflict")
                    ids.append(old["flow_id"])
                    continue
                if len(data["flows"]) >= 256:
                    _fail("automation_flow_limit")
                identity = uuid4().hex
                ids.append(identity)
                data["flows"].append({
                    "flow_id": identity, "hook_id": hook["hook_id"],
                    "hook_revision": hook["revision"], "title": hook["title"],
                    "agent_id": hook["agent_id"], "workspace_id": hook["workspace_id"],
                    "steps": hook["steps"], "event_id": event_id, "payload": payload,
                    "payload_sha256": digest, "source": source, "status": "queued",
                    "outcome": "pending", "cursor": 0, "revision": 1, "created_at": now,
                    "updated_at": now, "expires_at": now + ttl_seconds, "error_code": None,
                    "run_ids": [], "claim_id": None, "claim_run_id": None, "receipts": []})
        data = self._change(change)
        return tuple(r for identity in ids for r in data["flows"] if r["flow_id"] == identity)

    def _stop_flow(self, row, status, outcome, error_code):
        row.update(status=status, outcome=outcome, error_code=error_code,
                   updated_at=max(row["updated_at"], _time(self.clock())),
                   revision=row["revision"] + 1)

    def _control(self, flow_id, expected_revision, action):
        def change(data):
            row = self._find(data, "flows", flow_id)
            self._cas(row, expected_revision)
            if row["status"] in _TERMINAL:
                _fail("automation_flow_terminal")
            if action == "resume":
                if row["status"] != "paused" or row["outcome"] != "known":
                    _fail("automation_resume_unavailable")
                hook = self._find(data, "hooks", row["hook_id"])
                if not hook["enabled"] or hook["revision"] != row["hook_revision"]:
                    _fail("automation_hook_changed")
                if row["expires_at"] <= _time(self.clock()):
                    _fail("automation_expired")
                self._stop_flow(row, "queued", "pending", None)
            elif row["status"] == "running":
                # Dispatch cancellation is requested by the runner; a late callback
                # cannot turn this interrupted effect into success or replay it.
                self._interrupt(row, "automation_dispatch_cancelled")
            elif action == "pause":
                self._stop_flow(row, "paused", "known", None)
            else:
                self._stop_flow(row, "cancelled", "known", "automation_cancelled")
        self._change(change)
        return self.get_flow(flow_id)

    def pause(self, flow_id, expected_revision):
        return self._control(flow_id, expected_revision, "pause")

    def resume(self, flow_id, expected_revision):
        return self._control(flow_id, expected_revision, "resume")

    def cancel(self, flow_id, expected_revision):
        return self._control(flow_id, expected_revision, "cancel")

    def recover_unfinished(self):
        count = 0
        def change(data):
            nonlocal count
            for row in data["flows"]:
                if row["status"] == "running":
                    self._interrupt(row, "automation_restart_interrupted")
                    count += 1
        self._change(change)
        return count

    def _interrupt(self, row, error_code):
        row["receipts"].append({"step_index": row["cursor"], "claim_id": row["claim_id"],
                                "run_id": row["claim_run_id"], "status": "interrupted",
                                "outcome": "unknown", "error_code": error_code})
        self._stop_flow(row, "interrupted", "unknown", error_code)

    def claim_next(self):
        claim = None
        now = _time(self.clock())
        # An idle poll is a read, not a no-op write transaction. Besides avoiding
        # lock churn, this skips re-serializing the whole bounded history twice.
        # Another enqueue just after this snapshot is handled by wake/the next
        # poll; any real claim is still revalidated under the document lock.
        snapshot = self.document.read()
        if not any(row["status"] == "queued" or (
                row["status"] == "paused" and row["expires_at"] <= now)
                for row in snapshot["flows"]):
            return None
        def change(data):
            nonlocal claim
            for row in data["flows"]:
                if row["status"] == "paused" and row["expires_at"] <= now:
                    self._stop_flow(row, "cancelled", "known", "automation_expired")
                if row["status"] != "queued":
                    continue
                hook = next((h for h in data["hooks"] if h["hook_id"] == row["hook_id"]), None)
                if row["expires_at"] <= now:
                    self._stop_flow(row, "cancelled", "known", "automation_expired")
                    continue
                if hook is None or not hook["enabled"] or hook["revision"] != row["hook_revision"]:
                    self._stop_flow(row, "cancelled", "known", "automation_hook_changed")
                    continue
                if claim is None:
                    identity = uuid4().hex
                    self._stop_flow(row, "running", "pending", None)
                    row["claim_id"] = identity
                    row["claim_run_id"] = None
                    claim = StepClaim(row["flow_id"], identity, row["hook_id"], row["title"],
                                      row["steps"][row["cursor"]]["prompt"], row["agent_id"],
                                      row["workspace_id"], row["payload"], row["cursor"])
        self._change(change)
        return claim

    def attach_run(self, flow_id, claim_id, run_id):
        _id(run_id), _id(claim_id)
        def change(data):
            row = self._find(data, "flows", flow_id)
            if row["status"] != "running" or row["claim_id"] != claim_id:
                _fail("automation_claim_changed")
            if row["claim_run_id"] is None:
                if run_id in row["run_ids"]:
                    _fail("automation_claim_changed")
                row["claim_run_id"] = run_id
                if run_id not in row["run_ids"]:
                    row["run_ids"].append(run_id)
                row["revision"] += 1
            elif row["claim_run_id"] != run_id:
                _fail("automation_claim_changed")
        self._change(change)

    def finish_step(self, claim, result):
        if not isinstance(claim, StepClaim) or not isinstance(result, DispatchResult):
            _fail("automation_result_invalid")
        def change(data):
            row = self._find(data, "flows", claim.flow_id)
            if row["status"] != "running" or row["claim_id"] != claim.claim_id:
                return  # A cancellation/recovery receipt is monotonic.
            if result.run_id is not None:
                if row["claim_run_id"] not in {None, result.run_id}:
                    _fail("automation_claim_changed")
                if row["claim_run_id"] is None and result.run_id in row["run_ids"]:
                    _fail("automation_claim_changed")
                if result.run_id not in row["run_ids"]:
                    row["run_ids"].append(result.run_id)
            row["receipts"].append({"step_index": row["cursor"], "claim_id": claim.claim_id,
                                    "run_id": result.run_id or row["claim_run_id"],
                                    "status": result.status,
                                    "outcome": result.outcome, "error_code": result.error_code})
            if result.status != "completed" or result.outcome != "known":
                self._stop_flow(row, result.status, result.outcome, result.error_code)
                return
            pause = row["steps"][row["cursor"]]["pause_after"]
            row["cursor"] += 1
            row["claim_id"] = None
            row["claim_run_id"] = None
            if row["cursor"] == len(row["steps"]):
                self._stop_flow(row, "completed", "known", None)
            elif pause:
                self._stop_flow(row, "paused", "known", None)
            else:
                self._stop_flow(row, "queued", "pending", None)
        self._change(change)
        return self.get_flow(claim.flow_id)


def validate_automation_document(value):
    """Pure portable-format validation; opens no profile or registry."""
    AutomationFlowStore._validate(value)


def disarm_automation_document(value):
    """Pure restore transform: retain evidence, never restore an armed listener/job."""
    validate_automation_document(value)
    result = json.loads(_encoded(value))
    changed = False
    for hook in result["hooks"]:
        if hook["enabled"]:
            hook["enabled"] = False
            hook["revision"] += 1
            changed = True
    for row in result["flows"]:
        if row["status"] in {"queued", "paused", "running"}:
            if row["status"] == "running":
                row["receipts"].append({"step_index": row["cursor"], "claim_id": row["claim_id"],
                                        "run_id": row["claim_run_id"], "status": "interrupted",
                                        "outcome": "unknown",
                                        "error_code": "automation_restore_disarmed"})
            row.update(status="interrupted", outcome="unknown",
                       error_code="automation_restore_disarmed", revision=row["revision"] + 1)
            changed = True
    if changed:
        result["revision"] += 1
    validate_automation_document(result)
    return result


class AutomationFlowRunner:
    def __init__(self, store, dispatch, *, poll_interval=1, active_poll_interval=0.1,
                 dispatch_timeout=300, close_timeout=10):
        if (not callable(dispatch) or any(type(value) not in {int, float}
                or not math.isfinite(value) or not 0 < value <= maximum
                for value, maximum in ((poll_interval, 5), (active_poll_interval, 1),
                                       (dispatch_timeout, 3600),
                                       (close_timeout, 30)))):
            _fail("automation_configuration_invalid")
        self.store, self.dispatch = store, dispatch
        self.poll_interval, self.dispatch_timeout = poll_interval, dispatch_timeout
        self.active_poll_interval = active_poll_interval
        self.close_timeout = close_timeout
        self._task = self._dispatch_task = None
        self._lease = None
        self._lease_identity = None
        self._stop, self._wake = asyncio.Event(), asyncio.Event()
        self.error_code = None

    @property
    def running(self):
        return self._task is not None and not self._task.done() and not self._stop.is_set()

    @property
    def active_count(self):
        return int(self._dispatch_task is not None and not self._dispatch_task.done())

    @property
    def pending_cleanup(self):
        return self._lease is not None and self._stop.is_set()

    def wake(self):
        self._wake.set()

    def start(self):
        if self.running:
            return
        if self._lease is not None or self._task is not None:
            _fail("automation_runner_closed")
        parent = self.store.document._open_parent()
        descriptor = None
        try:
            descriptor = os.open("automation.runner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
            PrivateJsonDocument._regular(descriptor, 0)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(descriptor)
            linked = os.stat("automation.runner.lock", dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino):
                _fail("automation_runner_changed")
            self.store.recover_unfinished()
            self._lease, descriptor = descriptor, None
            self._lease_identity = info.st_dev, info.st_ino
            self._task = asyncio.create_task(self._pump())
        except BlockingIOError:
            _fail("automation_runner_busy")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    async def _pump(self):
        try:
            while not self._stop.is_set():
                self._wake.clear()
                claim = self.store.claim_next()
                if claim is not None:
                    self._dispatch_task = asyncio.create_task(self._execute(claim))
                    # Controls may invalidate a running claim. Retain ownership
                    # until the callback settles, before admitting another step.
                    while not self._dispatch_task.done():
                        await asyncio.sleep(self.active_poll_interval)
                        row = self.store.get_flow(claim.flow_id)
                        if row["status"] != "running" or self._stop.is_set():
                            self._dispatch_task.cancel()
                    await self._dispatch_task
                    self._dispatch_task = None
                    continue
                try:
                    await asyncio.wait_for(self._wake.wait(), self.poll_interval)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.error_code = getattr(error, "code", "automation_runner_unconfirmed")

    async def _execute(self, claim):
        try:
            async with asyncio.timeout(self.dispatch_timeout) as deadline:
                result = await self.dispatch(claim)
            if deadline.expired() or self._stop.is_set() or not isinstance(result, DispatchResult):
                _fail("automation_dispatch_unconfirmed")
        except asyncio.CancelledError:
            result = DispatchResult(None, "interrupted", "unknown", "automation_dispatch_cancelled")
        except Exception as error:
            result = DispatchResult(None, "failed", "unknown",
                                    getattr(error, "code", "automation_dispatch_unconfirmed"))
        self.store.finish_step(claim, result)

    async def aclose(self):
        self._stop.set()
        self.wake()
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
        if self._task is not None:
            _, pending = await asyncio.wait({self._task}, timeout=self.close_timeout)
            if pending:
                _fail("automation_cleanup_pending")
            await self._task
        self.store.close()
        if self._lease is not None:
            info = os.fstat(self._lease)
            if (info.st_dev, info.st_ino) != self._lease_identity:
                _fail("automation_runner_changed")
            os.close(self._lease)
            self._lease = None


class WebhookListener:
    """One explicit hook, one ephemeral IPv4 loopback port, no outbound delivery."""

    def __init__(self, store, *, emit=None, on_enqueue=None, clock=time.time):
        self.store, self.emit = store, emit or store.emit
        self.on_enqueue, self.clock = on_enqueue, clock
        self._server = None
        self._startup_task = None
        self._key = None
        self._hook = None
        self._tasks = set()
        self._writers = set()
        self._closing = False
        self._rejected = 0
        self._port = None

    @property
    def active_count(self):
        return len(self._tasks)

    @property
    def pending_cleanup(self):
        return self._closing and bool(
            self._tasks or self._writers or self._server or self._startup_task)

    def status(self):
        running = (not self._closing and self._server is not None
                   and self._server.is_serving())
        address = None
        if running:
            port = self._server.sockets[0].getsockname()[1]
            address = f"http://127.0.0.1:{port}/hooks/{self._hook['hook_id']}"
        return {"running": running, "address": address,
                "hook_id": self._hook["hook_id"] if self._hook else None,
                "active_count": self.active_count, "rejected": self._rejected,
                "pending_cleanup": self.pending_cleanup}

    async def start(self, hook_id, key):
        if (self._server is not None or self._startup_task is not None
                or self._tasks or self._closing):
            _fail("automation_webhook_already_open")
        if not isinstance(key, bytes) or not 32 <= len(key) <= 64:
            _fail("automation_webhook_key_invalid")
        hook = self.store.get_hook(hook_id)
        if not hook["enabled"] or hook["event"] != "webhook":
            _fail("automation_hook_unavailable")
        self._hook, self._key = hook, bytes(key)
        # The server acquisition is owned even if the RPC that awaits it is
        # cancelled. A later close drains this exact task and its server.
        self._startup_task = asyncio.create_task(asyncio.start_server(
            self._accept, "127.0.0.1", 0, limit=4096))
        try:
            self._server = await asyncio.shield(self._startup_task)
            self._startup_task = None
            if self._closing:
                self._server.close()
                _fail("automation_webhook_revoked")
            self._port = self._server.sockets[0].getsockname()[1]
        except BaseException:
            self.revoke()
            raise
        return self.status()

    def revoke(self):
        """Withdraw admission synchronously; retain keys/resources until close ACK."""
        self._closing = True
        if self._server is not None:
            self._server.close()
        for writer in tuple(self._writers):
            try:
                writer.close()
            except (OSError, RuntimeError):
                pass  # Preserve the writer for an explicit close retry.
        for task in tuple(self._tasks):
            task.cancel()

    def _accept(self, reader, writer):
        # Custody precedes scheduling: aclose may cancel a task before its first
        # instruction, in which case the coroutine's finally never executes.
        self._writers.add(writer)
        task = asyncio.create_task(self._request(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _request(self, reader, writer):
        self._writers.add(writer)
        status, response = 400, {"error": "automation_webhook_invalid"}
        try:
            if self._closing or len(self._writers) > 4:
                status = 429
            else:
                async with asyncio.timeout(3):
                    header = await reader.readuntil(b"\r\n\r\n")
                    if len(header) > 4096:
                        _fail("automation_webhook_invalid")
                    lines = header.decode("ascii").split("\r\n")
                    if lines[0] != f"POST /hooks/{self._hook['hook_id']} HTTP/1.1":
                        _fail("automation_webhook_invalid")
                    fields = {}
                    for line in lines[1:-2]:
                        name, value = line.split(":", 1)
                        name = name.lower()
                        if name in fields or re.fullmatch(r"[a-z0-9-]+", name) is None:
                            _fail("automation_webhook_invalid")
                        fields[name] = value.strip()
                    if ("transfer-encoding" in fields
                            or fields.get("content-type") != "application/json"
                            or "origin" in fields
                            or fields.get("host") != f"127.0.0.1:{self._port}"
                            or re.fullmatch(r"[1-9][0-9]{0,4}", fields.get("content-length", ""))
                            is None or int(fields["content-length"]) > 16384):
                        _fail("automation_webhook_invalid")
                    body = await reader.readexactly(int(fields["content-length"]))
                    timestamp, nonce = fields.get("x-jarvis-timestamp", ""), fields.get(
                        "x-jarvis-nonce", "")
                    signature = fields.get("x-jarvis-signature", "")
                    if (re.fullmatch(r"[0-9]{1,12}", timestamp) is None
                            or re.fullmatch(r"[a-f0-9]{32}", nonce) is None
                            or re.fullmatch(r"[a-f0-9]{64}", signature) is None
                            or abs(_time(self.clock()) - int(timestamp)) > 60):
                        status = 401
                        _fail("automation_webhook_unauthorized")
                    expected = hmac.digest(self._key, f"{timestamp}.{nonce}.".encode() + body,
                                           "sha256").hex()
                    if not hmac.compare_digest(expected, signature):
                        status = 401
                        _fail("automation_webhook_unauthorized")
                    payload = json.loads(body, object_pairs_hook=_object,
                                         parse_constant=lambda _value: _fail(
                                             "automation_payload_invalid"))
                    _payload(payload)
                    rows = self.emit("webhook", nonce, payload, hook_id=self._hook["hook_id"],
                                     expected_revision=self._hook["revision"], source="webhook")
                    if self.on_enqueue is not None:
                        self.on_enqueue()
                    status, response = 202, {"flow_ids": [row["flow_id"] for row in rows]}
        except (AutomationFlowError, PrivateJsonError) as error:
            response = {"error": error.code}
            if error.code == "automation_event_conflict":
                status = 409
            elif error.code in {"automation_hook_unavailable", "automation_revision_conflict"}:
                status = 409
            elif error.code == "automation_webhook_owner_revoked":
                status = 401
        except (ValueError, UnicodeError, OSError, TimeoutError, RecursionError,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            if status != 202:
                self._rejected += 1
            try:
                raw = _encoded(response)
                writer.write(f"HTTP/1.1 {status} Jarvis\r\nContent-Type: application/json\r\n"
                             f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n".encode()
                             + raw)
                async with asyncio.timeout(2):
                    await writer.drain()
            except (OSError, TimeoutError, RuntimeError):
                pass
            writer.close()
            settled = False
            try:
                async with asyncio.timeout(2):
                    await writer.wait_closed()
                settled = True
            except (OSError, TimeoutError, RuntimeError):
                pass  # Retain custody until an explicit close retry confirms it.
            if settled:
                self._writers.discard(writer)

    async def aclose(self):
        self.revoke()
        if self._startup_task is not None:
            startup = self._startup_task
            _, pending = await asyncio.wait({startup}, timeout=5)
            if pending:
                _fail("automation_webhook_cleanup_pending")
            try:
                self._server = startup.result()
            except (OSError, asyncio.CancelledError):
                pass
            self._startup_task = None
        if self._server is not None:
            self._server.close()
            try:
                async with asyncio.timeout(5):
                    await self._server.wait_closed()
            except TimeoutError:
                _fail("automation_webhook_cleanup_pending")
            self._server = None
        for writer in tuple(self._writers):
            writer.close()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=5)
            if pending:
                _fail("automation_webhook_cleanup_pending")
            await asyncio.gather(*tasks, return_exceptions=True)
        for writer in tuple(self._writers):
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (OSError, TimeoutError, RuntimeError):
                continue
            self._writers.discard(writer)
        if self._writers:
            _fail("automation_webhook_cleanup_pending")
        self._key = self._hook = None
