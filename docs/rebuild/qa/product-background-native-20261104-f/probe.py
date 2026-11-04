"""Read-only native A/B observation; import starts no process or inference."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import signal
import sys
import time
from pathlib import Path

import common as c

ROOT = Path(__file__).parent
COMMIT = "2c8259de8112aa5f1487814a5c5c35b3931e5c43"
LIMIT, OUTPUT = 262144, 65536
READ_METHODS = {"sessions.list", "sessions.messages", "runs.get", "product.snapshot"}
A_PREF = {"revision": 2, "enabled": True, "silenced": True}
B_PREF = {"revision": 0, "enabled": False, "silenced": False}


def identifier(value):
    c.require(type(value) is str and re.fullmatch("[a-f0-9]{32}", value), "public_id_invalid")
    return value


def encode(value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    c.require(len(raw) <= OUTPUT, "sanitized_output_limit")
    return raw


def load_attempt(pin):
    p = c.read_json(ROOT / "prepared.json", pin)
    c.require(p["status"] == "prepared" and p["commit"] == COMMIT
              and p["full_ci_passed"] is True, "exact_CI_preparation_required")
    a = c.read_json(ROOT / "admission.json", p["admission_sha256"])
    c.require(a["harness_root"] == str(ROOT)
              and a["scripts_sha256"]["probe.py"] == c.sha(Path(__file__)), "probe_admission_changed")
    for name, digest in a["scripts_sha256"].items():
        c.require(c.sha(ROOT / name) == digest, "harness_source_changed")
    c.require(c.sha(ROOT / "reviewer-checklist.json") == a["reviewer_checklist_sha256"],
              "review_checklist_changed")
    active = c.read_json(ROOT / "active.json")
    now = time.time()
    c.require(active["prepared_sha256"] == pin
              and type(active["launch_requested_unix"]) in {int, float}
              and math.isfinite(active["launch_requested_unix"])
              and active["launch_requested_unix"] <= now < active["launch_requested_unix"] + 810
              and not (ROOT / "quit-intent.json").exists()
              and not (ROOT / "closed.json").exists(), "current_attempt_required")
    current = c.snapshot_pid(active["app"]["pid"])
    c.require(current is not None and c.identity(current) == c.identity(active["app"]),
              "active_app_identity_changed")
    q, state, app = map(Path, (p["root_quota"], p["state"], p["app"]))
    c.require(q == Path("/private/tmp/jq-8k57q6f0") and state == q / "state-f"
              and app == q / "Jarvis.app" and p["source"] == str(q / "source")
              and p["dependencies"] == str(q / "dependencies"), "explicit_F_paths_required")
    key = q / "state-f.clients/desktop.key"
    c.private(state); c.private(key, "file")
    c.verify_copies(p, deadline=time.monotonic() + 20)
    return p, state, key


class Inspector:
    def __init__(self, client, args, checkpoint):
        self.client, self.args, self.checkpoint = client, args, checkpoint
        self.received = 0

    async def call(self, method, params):
        c.require(method in READ_METHODS, "mutation_not_admitted")
        self.checkpoint()
        value = await self.client.call(method, params)
        self.received += len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode())
        c.require(self.received <= LIMIT, "aggregate_RPC_input_limit")
        self.checkpoint()
        return value

    async def inspect(self):
        listing = await self.call("sessions.list", {"temporary": False})
        c.require(not listing["has_more"] and type(listing["sessions"]) is list
                  and 1 <= len(listing["sessions"]) <= 2, "fresh_F_session_limit")
        histories, inventory = {}, []
        for row in listing["sessions"]:
            session = identifier(row["session_id"])
            c.require(row["temporary"] is False and session not in histories, "unexpected_session")
            h = await self.call("sessions.messages", {
                "session_id": session, "temporary": False, "after": 0})
            c.require(not h["has_more"] and not h["runs_has_more"]
                      and len(h["messages"]) <= 2 and len(h["runs"]) <= 1, "fresh_history_limit")
            histories[session] = h
            inventory.append({"session_id": session, "message_count": len(h["messages"]),
                              "runs": [{"run_id": identifier(r["run_id"]), "status": r["status"],
                                        "outcome": r["outcome"]} for r in h["runs"]]})
        result = {"inventory": inventory}
        if self.args.mode == "inventory":
            return {**result, "received_bytes": self.received, "status": "inspected", "conditions": {}}
        a, b = self.args.session_a, self.args.session_b
        c.require(a != b and set(histories) == {a, b}, "exact_two_F_sessions_required")
        conditions = {"b_empty": not histories[b]["messages"] and not histories[b]["runs"]}
        snapshots = {}
        for label, session, expected in (("a", a, A_PREF), ("b", b, B_PREF)):
            s = await self.call("product.snapshot", {
                "session_id": session, "temporary": False, "run_id": "host"})
            scope, pref, interactions = s["scope"], s["notification_preferences"], s["interactions"]
            c.require(scope["session_id"] == session and scope["temporary"] is False
                      and scope["run_id"] == "host" and type(interactions) is list
                      and len(interactions) <= 512, "snapshot_scope_changed")
            observed = {k: pref[k] for k in ("revision", "enabled", "silenced")}
            c.require(type(observed["revision"]) is int and type(observed["enabled"]) is bool
                      and type(observed["silenced"]) is bool, "invalid_preferences")
            conditions[label + "_exact_preferences"] = observed == expected
            conditions[label + "_no_interactions"] = not interactions
            snapshots[label] = {"session_id": session, "preferences": observed,
                                "interaction_count": len(interactions)}
        if self.args.mode == "before":
            conditions["a_empty"] = not histories[a]["messages"] and not histories[a]["runs"]
        else:
            c.require(len(histories[a]["runs"]) == 1, "exactly_one_F_run_required")
            run_id = identifier(histories[a]["runs"][0]["run_id"])
            if self.args.run_id is not None:
                c.require(run_id == self.args.run_id, "run_identity_changed")
            row = (await self.call("runs.get", {
                "session_id": a, "temporary": False, "run_id": run_id}))["run"]
            c.require(row["run_id"] == run_id and type(row["action_receipts"]) is list,
                      "selected_run_changed")
            conditions["no_tool_calls"] = not row["action_receipts"]
            conditions["one_A_run"] = len(histories[a]["runs"]) == 1
            if self.args.mode == "during":
                conditions["A_running"] = row["status"] == "running" and row["cleanup_confirmed"] is False
            else:
                conditions["A_completed_known_clean"] = (
                    row["status"] == "completed" and row["outcome"] == "known"
                    and row["cleanup_confirmed"] is True and row.get("answer_source") == "model")
            result["run"] = {"run_id": run_id, "status": row["status"], "outcome": row["outcome"],
                             "cleanup_confirmed": row["cleanup_confirmed"],
                             "answer_source": row.get("answer_source"),
                             "action_receipts_count": len(row["action_receipts"]),
                             "answer_bytes": len((row.get("answer") or "").encode())}
        return {**result, "snapshots": snapshots, "conditions": conditions,
                "received_bytes": self.received,
                "status": "passed_selected_conditions" if all(conditions.values()) else "failed_conditions"}


async def inspect(args, p, state, key, checkpoint):
    core = Path(p["source"])
    sys.path[:0] = [str(core), p["dependencies"]]
    from jarvis_core.client_identity import ClientIdentity
    from jarvis_core.protocol import LocalRpcClient
    for name in ("jarvis_core", "jarvis_core.client_identity", "jarvis_core.protocol"):
        c.require(Path(sys.modules[name].__file__).resolve().parent == core / "jarvis_core",
                  "selected_source_import_changed")
    client = LocalRpcClient(state, identity=ClientIdentity.load(key), timeout=5, max_frame_bytes=LIMIT)
    try:
        async with asyncio.timeout(30):
            await client.connect()
            return await Inspector(client, args, checkpoint).inspect()
    finally:
        await asyncio.wait_for(client.aclose(), 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inventory", "before", "during", "after"))
    parser.add_argument("--prepared-sha256", required=True)
    parser.add_argument("--session-a")
    parser.add_argument("--session-b")
    parser.add_argument("--run-id")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    c.require(sys.flags.no_user_site == 1 and sys.dont_write_bytecode, "use_python_I_S_B")
    c.require(re.fullmatch("[a-f0-9]{64}", args.prepared_sha256), "prepared_pin_required")
    c.require(args.receipt.parent == ROOT and args.receipt.is_absolute()
              and re.fullmatch(r"[a-z0-9-]{1,64}\.json", args.receipt.name)
              and not args.receipt.exists(), "new_explicit_receipt_required")
    if args.mode != "inventory":
        identifier(args.session_a); identifier(args.session_b)
    if args.run_id is not None:
        identifier(args.run_id)
    started, deadline = time.monotonic(), time.monotonic() + 40
    previous = signal.getsignal(signal.SIGALRM)

    def expire(_number, _frame):
        raise c.GuardIssue("probe_deadline")

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, 40)
    prepared, result = None, None
    try:
        prepared, state, key = load_attempt(args.prepared_sha256)

        def checkpoint():
            c.require(time.monotonic() < deadline, "probe_deadline")
            c.resources(prepared)

        checkpoint()
        result = asyncio.run(inspect(args, prepared, state, key, checkpoint))
        checkpoint()
    except (Exception, KeyboardInterrupt) as error:
        result = {"status": "failed_or_unknown",
                  "error": str(error) if isinstance(error, c.GuardIssue) else type(error).__name__}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    result.update(schema="jarvis.background-UI-read-only-probe/1", mode=args.mode,
                  source_commit=COMMIT, ci_run_id=prepared["run_id"] if prepared else None,
                  prepared_sha256=args.prepared_sha256, session_a=args.session_a,
                  session_b=args.session_b, requested_run_id=args.run_id,
                  observed_at_unix=time.time(), elapsed_seconds=round(time.monotonic() - started, 3),
                  mutations_sent=False, provider_auth_read=False, model_or_answer_values_emitted=False,
                  ui_selection_proven_by_RPC=False, full_qualification=False, permits_effect_replay=False)
    c.write_new(args.receipt, encode(result))
    print(json.dumps({"status": result["status"], "receipt": str(args.receipt),
                      "sha256": c.sha(args.receipt), "conditions": result.get("conditions", {})}))
    return 0 if result["status"] in {"inspected", "passed_selected_conditions"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
