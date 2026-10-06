"""Synthetic live generation gate. Never prints credentials or response bodies."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from aegis_core.config import Settings
from aegis_core.generated_media import GeneratedMediaStore
from aegis_core.providers.cosmos import CosmosArguments, CosmosClient, CosmosError
from aegis_core.secrets import MacOSKeychain


async def run(root: Path) -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service, account=settings.nvidia_keychain_account
    )
    client = CosmosClient(keychain.get)
    store = GeneratedMediaStore(root / "originals")
    outcomes = []
    try:
        for mode in ("text2image", "text2video"):
            started = time.monotonic()
            try:
                raw, mime, request_id = await client.generate(
                    CosmosArguments(
                        model_mode=mode,
                        prompt="A small friendly green robot on a clean laboratory "
                        "workbench. Static camera, bright soft lighting. The robot waves gently.",
                        resolution="480_1_1" if mode == "text2image" else "480_16_9",
                        num_frames=25,
                    )
                )
                artifact = store.put(raw, mime)
                outcomes.append(
                    {
                        "mode": mode,
                        "passed": True,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "artifact": artifact.model_dump(),
                        "request_id": request_id,
                    }
                )
            except (CosmosError, OSError, ValueError) as error:
                outcomes.append(
                    {
                        "mode": mode,
                        "passed": False,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "error_code": error.code
                        if isinstance(error, CosmosError)
                        else type(error).__name__,
                        "http_status": error.status_code
                        if isinstance(error, CosmosError)
                        else None,
                    }
                )
                # Do not fish for successes on an unavailable/limited/authenticated endpoint.
                break
        report = {
            "source_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "development_run": bool(subprocess.check_output(["git", "diff", "--name-only"])),
            "endpoint": "https://ai.api.nvidia.com/v1/cosmos/nvidia/cosmos3-nano",
            "synthetic_inputs_only": True,
            "outcomes": outcomes,
            "all_passed": len(outcomes) == 2 and all(o["passed"] for o in outcomes),
        }
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = root / "report.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        path.chmod(0o600)
        print(json.dumps(report))
        return 0 if report["all_passed"] else 1
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    os.umask(0o077)
    raise SystemExit(asyncio.run(run(arguments.output_dir)))
