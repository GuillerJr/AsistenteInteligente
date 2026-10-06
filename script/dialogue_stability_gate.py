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

from aegis_core.brain.errors import RemoteRateLimitedError
from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import AgentRole, InputModality, UserRequest
from aegis_core.dialogue_challenges import challenge_cases
from aegis_core.dialogue_stability import dialogue_cases
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.secrets import MacOSKeychain
from aegis_core.tools.broker import ToolBroker, ToolRegistry


class CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport()
        self.requests = 0
        self.rate_limit_events = []
        self.request_outcomes = []

    async def handle_async_request(self, request):
        self.requests += 1
        try:
            model = json.loads(request.content).get("model")
        except (ValueError, AttributeError):
            model = None
        model = model if isinstance(model, str) and len(model) <= 200 else None
        started = time.perf_counter()
        event = {"model": model, "status_code": None, "outcome": "transport_error"}
        try:
            response = await self.inner.handle_async_request(request)
            event.update(status_code=response.status_code, outcome="response")
        except asyncio.CancelledError:
            event["outcome"] = "cancelled"
            raise
        finally:
            event["latency_ms"] = round((time.perf_counter() - started) * 1000)
            self.request_outcomes.append(event)
        if response.status_code == 429:
            # Only whitelisted numeric metadata/classification; never headers or bodies.
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
            except ValueError:
                retry_after = None
            if retry_after is not None and (not math.isfinite(retry_after) or retry_after < 0):
                retry_after = None
            body = (await response.aread()).decode("utf-8", errors="replace").casefold()
            hint = ("daily" if any(term in body for term in ("per day", "rpd", "daily"))
                    else "minute" if any(term in body for term in ("per minute", "rpm"))
                    else "capacity" if any(term in body for term in ("overload", "capacity"))
                    else "unspecified")
            self.rate_limit_events.append({"status": 429, "retry_after_seconds": retry_after,
                                           "provider_limit_hint": hint})
        return response

    async def aclose(self):
        await self.inner.aclose()


class NumericAudit:
    def __init__(self):
        self.stages = {}
        self.totals = dict.fromkeys(
            ("http_requests", "usage_responses", "prompt_tokens", "completion_tokens"), 0)

    def record_system_event(self, _request_id, **event):
        if event.get("event_type") == "nvidia_inference":
            for key in self.totals:
                self.totals[key] += event["data"][key]
            for name in ("intent", "answer", "vision"):
                values = {key: event["data"][f"{name}_{key}"]
                          for key in ("calls", "latency_ms", "http_requests")
                          if f"{name}_{key}" in event["data"]}
                if not values:
                    continue
                stage = self.stages.setdefault(name, dict.fromkeys(values, 0))
                for key, value in values.items():
                    stage[key] += value


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def cases_for(suite: str):
    if suite == "baseline":
        return dialogue_cases()
    if suite == "challenge":
        return challenge_cases()
    return (*dialogue_cases(), *challenge_cases())


async def run(repeat: int, only: set[str], suite: str = "baseline",
              pause_seconds: float = 1.0, rate_limit_wait_seconds: float = 0.0) -> dict:
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
    blocked = False
    consecutive_limits = 0
    cases = tuple(case for case in cases_for(suite) if not only or case.name in only)
    async with NvidiaNimClient(settings, lambda: key, transport=transport) as provider:
        graph = build_swarm_graph(NvidiaBrainClient(provider, audit_sink=audit),
                                 inference_nvidia_only=True,
                                 tool_broker=ToolBroker(ToolRegistry(), role_capabilities={
                                     role: frozenset() for role in AgentRole}),
                                 dynamic_tool_names=lambda _: frozenset())
        for repetition in range(1, repeat + 1):
            for case in cases:
                if samples and pause_seconds:
                    await asyncio.sleep(pause_seconds)
                before = transport.requests
                usage_before = dict(audit.totals)
                stages_before = {name: dict(values) for name, values in audit.stages.items()}
                started = time.perf_counter()
                first = None

                def visible(_text, sample_started=started):
                    nonlocal first
                    if first is None:
                        first = round((time.perf_counter() - sample_started) * 1000)

                model = None
                rate_limited = False
                try:
                    state = await asyncio.wait_for(graph.ainvoke({
                        "request": UserRequest(text=case.request,
                            documents=getattr(case, "documents", ()),
                            image=getattr(case, "image", None),
                            modalities=(frozenset({InputModality.TEXT, InputModality.IMAGE})
                                        if getattr(case, "image", None) is not None
                                        else frozenset({InputModality.TEXT})), metadata={
                            "source_channel": "telegram", **case.metadata}),
                        "conversation_history": case.turns(), "stream_callback": visible,
                    }), timeout=100)
                    result = state["final_result"]
                    failures = list(case.failures(result.content))
                    if (state.get("tool_calls") or state.get("tool_results")
                            or state.get("tool_authorizations")):
                        failures.append("unexpected_tool_activity")
                    model = result.model_id
                except RemoteRateLimitedError:
                    failures = ["request_failed:RemoteRateLimitedError"]
                    rate_limited = True
                except Exception as error:
                    # Do not log exceptions/bodies: providers may contain private headers.
                    failures = ["request_failed:" + type(error).__name__]
                sample = {"case": case.name, "category": getattr(case, "category", "dialogue"),
                          "capability_supported": getattr(case, "supported", True),
                          "repeat": repetition, "passed": not failures,
                          "failures": failures, "model": model,
                          "latency_ms": round((time.perf_counter() - started) * 1000),
                          "first_visible_ms": first,
                          "http_requests": transport.requests - before}
                sample["request_outcomes"] = transport.request_outcomes[before:]
                sample.update({key: total - usage_before[key] for key, total in audit.totals.items()
                               if key != "http_requests"})
                sample["stages"] = {name: {key: value - stages_before.get(name, {}).get(key, 0)
                    for key, value in values.items()} for name, values in audit.stages.items()}
                if sample["http_requests"] != audit.totals["http_requests"] - usage_before[
                        "http_requests"]:
                    sample["failures"].append("inference_http_metrics_mismatch")
                    sample["passed"] = False
                samples.append(sample)
                print(json.dumps(sample), file=sys.stderr, flush=True)
                if rate_limited:
                    consecutive_limits += 1
                    retry_after = (transport.rate_limit_events[-1]["retry_after_seconds"]
                                   if transport.rate_limit_events else None)
                    wait = max(rate_limit_wait_seconds, retry_after or 0)
                    if rate_limit_wait_seconds and consecutive_limits < 3 and wait <= 60:
                        # Move to the next planned sample after a bounded polite wait.
                        # Never retry this sample or replace its recorded failure.
                        print(json.dumps({"waiting_after_rate_limit_seconds": wait,
                                          "failed_sample_preserved": True}),
                              file=sys.stderr, flush=True)
                        await asyncio.sleep(wait)
                    else:
                        blocked = True
                else:
                    consecutive_limits = 0
                if blocked:
                    break
            if blocked:
                break
    stable_source = fingerprint == source_fingerprint(ROOT) and revision == subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain", "--", *BUILD_INPUTS], cwd=ROOT))
    return {"schema_version": 2, "profile": "synthetic_nvidia_dialogue", "suite": suite,
            "source_revision": revision, "tracked_source_dirty": dirty,
            "build_inputs_sha256": fingerprint, "source_unchanged_during_run": stable_source,
            "samples": samples, "passed": sum(sample["passed"] for sample in samples),
            "total": len(samples), "planned_samples": repeat * len(cases),
            "coverage_complete": len(samples) == repeat * len(cases),
            "blocked_by_rate_limit": blocked,
            "rate_limit_events": transport.rate_limit_events,
            "rate_limited_samples": sum("request_failed:RemoteRateLimitedError" in s["failures"]
                                        for s in samples),
            "pause_between_samples_seconds": pause_seconds,
            "all_passed": stable_source and len(samples) == repeat * len(cases) and all(
                sample["passed"] for sample in samples),
            "latency_p50_ms": percentile([s["latency_ms"] for s in samples if s["passed"]], .5),
            "latency_p95_ms": percentile([s["latency_ms"] for s in samples if s["passed"]], .95),
            "latency_scope": "successful_samples_only; failures remain in samples",
            "http_requests": sum(s["http_requests"] for s in samples),
            "categories": {category: {
                "passed": sum(s["passed"] for s in samples if s["category"] == category),
                "total": sum(s["category"] == category for s in samples),
                "latency_p50_ms": percentile([s["latency_ms"] for s in samples
                                               if s["category"] == category and s["passed"]], .5),
                "latency_p95_ms": percentile([s["latency_ms"] for s in samples
                                               if s["category"] == category and s["passed"]], .95),
            } for category in sorted({s["category"] for s in samples})},
            "unsupported_capabilities": sorted({s["category"] for s in samples
                                                if not s["capability_supported"]}),
            "privacy": {"synthetic_only": True, "contains_responses": False,
                        "contains_credentials": False, "external_tool_executions": 0},
            "owner_voice_qualified": False, "pilot_release_qualified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Authorize synthetic NVIDIA requests")
    parser.add_argument("--repeat", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--suite", choices=("baseline", "challenge", "all"), default="baseline")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--pause-seconds", type=float, default=1.0,
                        help="Pause outside measured latency between samples (0 to 60 seconds)")
    parser.add_argument("--rate-limit-wait-seconds", type=float, default=0.0,
                        help="Wait before the next sample after 429; preserve failure; stop at 3")
    args = parser.parse_args()
    known = {case.name for case in cases_for(args.suite)}
    if not args.live or not set(args.case).issubset(known):
        parser.error("--live and valid case names are required")
    if not math.isfinite(args.pause_seconds) or not 0 <= args.pause_seconds <= 60:
        parser.error("--pause-seconds must be finite and between 0 and 60")
    if (not math.isfinite(args.rate_limit_wait_seconds)
            or not 0 <= args.rate_limit_wait_seconds <= 60):
        parser.error("--rate-limit-wait-seconds must be finite and between 0 and 60")
    try:
        report = asyncio.run(run(args.repeat, set(args.case), args.suite, args.pause_seconds,
                                 args.rate_limit_wait_seconds))
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": type(error).__name__}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
