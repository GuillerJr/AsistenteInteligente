#!/usr/bin/env python3
"""Opt-in synthetic NVIDIA evaluation. Never reads chats or executes tools."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import httpx

# Local opt-in script: the package is loaded from this checkout, not an installed app.
# ruff: noqa: E402
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "script"))

from distribution_preflight import BUILD_INPUTS, source_fingerprint

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import UserRequest
from aegis_core.dialogue_stability import dialogue_cases
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.secrets import MacOSKeychain


class CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport()
        self.requests = 0

    async def handle_async_request(self, request):
        self.requests += 1
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


class NumericAudit:
    def __init__(self):
        self.totals = dict.fromkeys(
            ("http_requests", "usage_responses", "prompt_tokens", "completion_tokens"), 0)

    def record_system_event(self, _request_id, **event):
        if event.get("event_type") == "nvidia_inference":
            for key in self.totals:
                self.totals[key] += event["data"][key]


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


async def run(repeat: int, only: set[str]) -> dict:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    fingerprint = source_fingerprint(ROOT)
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    if not key:
        raise RuntimeError("credential_unavailable")
    transport = CountingTransport()
    audit = NumericAudit()
    samples = []
    async with NvidiaNimClient(settings, lambda: key, transport=transport) as provider:
        graph = build_swarm_graph(NvidiaBrainClient(provider, audit_sink=audit),
                                 inference_nvidia_only=True,
                                 dynamic_tool_names=lambda _: frozenset())
        for repetition in range(1, repeat + 1):
            for case in dialogue_cases():
                if only and case.name not in only:
                    continue
                before = transport.requests
                usage_before = dict(audit.totals)
                started = time.perf_counter()
                first = None

                def visible(_text, sample_started=started):
                    nonlocal first
                    if first is None:
                        first = round((time.perf_counter() - sample_started) * 1000)

                model = None
                try:
                    state = await asyncio.wait_for(graph.ainvoke({
                        "request": UserRequest(text=case.request, metadata={
                            "source_channel": "telegram", **case.metadata}),
                        "conversation_history": case.turns(), "stream_callback": visible,
                    }), timeout=100)
                    result = state["final_result"]
                    failures = list(case.failures(result.content))
                    if state.get("tool_results") or state.get("tool_authorizations"):
                        failures.append("unexpected_tool_activity")
                    model = result.model_id
                except Exception as error:
                    # Do not log exceptions/bodies: providers may contain private headers.
                    failures = ["request_failed:" + type(error).__name__]
                sample = {"case": case.name, "repeat": repetition, "passed": not failures,
                          "failures": failures, "model": model,
                          "latency_ms": round((time.perf_counter() - started) * 1000),
                          "first_visible_ms": first,
                          "http_requests": transport.requests - before}
                sample.update({key: total - usage_before[key] for key, total in audit.totals.items()
                               if key != "http_requests"})
                if sample["http_requests"] != audit.totals["http_requests"] - usage_before[
                        "http_requests"]:
                    sample["failures"].append("inference_http_metrics_mismatch")
                    sample["passed"] = False
                samples.append(sample)
                print(json.dumps(sample), file=sys.stderr, flush=True)
    stable_source = fingerprint == source_fingerprint(ROOT) and revision == subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain", "--", *BUILD_INPUTS], cwd=ROOT))
    return {"schema_version": 1, "profile": "synthetic_nvidia_dialogue",
            "source_revision": revision, "tracked_source_dirty": dirty,
            "build_inputs_sha256": fingerprint, "source_unchanged_during_run": stable_source,
            "samples": samples, "passed": sum(sample["passed"] for sample in samples),
            "total": len(samples), "all_passed": stable_source and all(
                sample["passed"] for sample in samples),
            "latency_p50_ms": percentile([s["latency_ms"] for s in samples], .5),
            "latency_p95_ms": percentile([s["latency_ms"] for s in samples], .95),
            "http_requests": sum(s["http_requests"] for s in samples),
            "privacy": {"synthetic_only": True, "contains_responses": False,
                        "contains_credentials": False, "external_tool_executions": 0},
            "owner_voice_qualified": False, "pilot_release_qualified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Authorize synthetic NVIDIA requests")
    parser.add_argument("--repeat", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    known = {case.name for case in dialogue_cases()}
    if not args.live or not set(args.case).issubset(known):
        parser.error("--live and valid case names are required")
    try:
        report = asyncio.run(run(args.repeat, set(args.case)))
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": type(error).__name__}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
