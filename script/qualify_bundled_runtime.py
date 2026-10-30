#!/usr/bin/env python3
"""Remote CI relocation smoke; runs the product interpreter, never a fixture LLM.

No account is connected, no microphone is opened and no Login Item is changed.
All mutations and the generated signing identity belong to this CI-only corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import plistlib
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

BOOTSTRAP = """import runpy,sys
source,dependencies,script = sys.argv.pop(1),sys.argv.pop(1),sys.argv.pop(1)
sys.path[:0]=[source,dependencies]
runpy.run_path(script,run_name='__main__')
"""
SERVICE_BOOTSTRAP = """import runpy,sys
source,dependencies = sys.argv.pop(1),sys.argv.pop(1)
sys.path[:0]=[source,dependencies]
runpy.run_module('jarvis_core.service',run_name='__main__',alter_sys=True)
"""


def bounded_capture(command, *, environment, timeout, stdout_limit=16384, stderr_limit=65536):
    """Drain concurrently, cap retained bytes during execution and reap our child."""
    deadline = time.monotonic() + timeout
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    failure, stop_deadline, killed = None, None, False
    with (
        subprocess.Popen(command, cwd="/", env=environment, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE) as process,
        selectors.DefaultSelector() as selector,
    ):
        for name in buffers:
            selector.register(getattr(process, name), selectors.EVENT_READ, name)
        while selector.get_map():
            now = time.monotonic()
            if failure is None and now >= deadline:
                failure = "qualification_deadline"
            if failure is not None and stop_deadline is None:
                if process.poll() is None:
                    process.terminate()  # this newly created CI harness only
                stop_deadline = now + 15
            if stop_deadline is not None and now >= stop_deadline and not killed:
                if process.poll() is None:
                    process.kill()
                killed = True
            if killed and now >= stop_deadline + 5:
                raise RuntimeError("qualification_output_cleanup_unconfirmed")
            for key, _ in selector.select(.2):
                block = os.read(key.fileobj.fileno(), 8192)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                name = key.data
                available = limits[name] - len(buffers[name])
                buffers[name].extend(block[:available])
                if len(block) > available and failure is None:
                    failure = "qualification_output_limit"
        code = process.wait(timeout=15)
    if failure is not None:
        raise RuntimeError(failure)
    return code, bytes(buffers["stdout"]), bytes(buffers["stderr"])


async def worker(root: Path, pin: str, work: Path, commit: str) -> dict:
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    from jarvis_core.bundled_runtime import verify_runtime_tree
    from jarvis_core.client_identity import ClientIdentity
    from jarvis_core.codex import CodexClient
    from jarvis_core.host_profile import ProfileLease
    from jarvis_core.local_embeddings import LocalEmbeddingClient
    from jarvis_core.model_artifacts import prepare_model
    from jarvis_core.protocol import LocalRpcClient, TransportSecurityError
    from jarvis_core.web import configure_bundled_tls, tls_context

    runtime = verify_runtime_tree(root, expected_manifest_sha256=pin,
                                  expected_source_commit=commit)
    assert Path(sys.executable).resolve() == runtime.path("python")
    assert all(not entry.startswith(("/opt/homebrew/", "/Users/runner/"))
               for entry in sys.path if entry and entry != str(Path(__file__).parent))
    # These exercise native libraries and their dependency closures, not only
    # import metadata. Every dependency must originate in the relocated bundle.
    import cryptography
    import jsonschema
    import numpy
    import onnxruntime
    import referencing
    import soundfile
    import tokenizers
    for module in (cryptography, jsonschema, numpy, onnxruntime, referencing,
                   soundfile, tokenizers):
        assert Path(module.__file__).resolve().is_relative_to(runtime.path("dependencies"))
    assert "CPUExecutionProvider" in onnxruntime.get_available_providers()
    assert "OPUS" in soundfile.available_subtypes("OGG")
    configure_bundled_tls(runtime.read_file("certs/cacert.pem", limit=512 * 1024))
    assert tls_context().cert_store_stats()["x509_ca"] > 0
    # Explicit synthetic model preparation: private copy, no HTTP fallback.
    model = work / "model"
    await asyncio.to_thread(prepare_model, model, bundled_source=runtime.path("model"))
    encoder = LocalEmbeddingClient(model)
    try:
        vector = await encoder.embed_query("El marcador de memoria es topacio.",
                                          deadline=time.monotonic() + 90)
        assert len(vector) == 384
    finally:
        closed = await encoder.close()
        assert closed.get("closed") and closed.get("cleanup_confirmed", True)
    # Start the actual official packaged CLI with a new empty provider profile.
    provider = CodexClient(runtime.path("codex"), work / "provider",
                           certificate_bundle=root / "certs/cacert.pem")
    try:
        account = await provider.account_status()
        assert account == {"connected": False, "mode": None}
    finally:
        await provider.aclose()
    identity = ClientIdentity.generate()
    profile = work / "state"
    environment = {key: os.environ[key] for key in ("HOME", "TMPDIR", "PATH", "LANG")
                   if key in os.environ}
    environment["JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY"] = identity.public_key
    process = await asyncio.create_subprocess_exec(
        str(runtime.path("python")), "-I", "-S", "-B", "-u", "-c", SERVICE_BOOTSTRAP,
        str(runtime.path("source")), str(runtime.path("dependencies")),
        "--state", str(profile), "--codex", str(runtime.path("codex")),
        "--bundled-runtime-root", str(root), "--bundled-runtime-sha256", pin,
        "--parent-watch-stdin", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        env=environment, cwd="/", limit=65536)
    errors = bytearray()
    stderr_overflow = asyncio.Event()

    async def drain_errors():
        while block := await process.stderr.read(8192):
            available = 65536 - len(errors)
            errors.extend(block[:available])
            if len(block) > available:
                stderr_overflow.set()

    stderr_task = asyncio.create_task(drain_errors())
    client = None
    try:
        async with asyncio.timeout(60):
            while client is None:
                if process.returncode is not None:
                    raise RuntimeError("packaged_service_early_exit")
                if stderr_overflow.is_set():
                    raise RuntimeError("packaged_service_output_limit")
                candidate = LocalRpcClient(profile, identity=identity, timeout=1)
                try:
                    await candidate.connect()
                    await candidate.call("health")
                except (OSError, TransportSecurityError, TimeoutError):
                    await candidate.aclose()
                    await asyncio.sleep(.05)
                else:
                    await candidate.aclose()
                    client = LocalRpcClient(profile, identity=identity, timeout=10)
                    await client.connect()
        session = (await client.call("sessions.create", {"title": "Distribución CI"}))["session"]
        ref = {key: session[key] for key in ("session_id", "temporary")}
        assert (await client.call("sessions.messages", ref))["messages"] == []
        await client.aclose()
        client = None
        # EOF is the same custody path used when the native parent disappears.
        # No SIGTERM, parent PID guessing or fallback interpreter is involved.
        process.stdin.close()
        await process.stdin.wait_closed()
        await asyncio.wait_for(process.wait(), 30)
        await asyncio.wait_for(stderr_task, 5)
        assert process.returncode == 0 and not stderr_overflow.is_set()
        assert not (profile / "service.sock").exists()
        lease = ProfileLease.acquire(profile)
        lease.close()
    finally:
        if client is not None:
            await client.aclose()
        if process.returncode is None:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 30)
            except TimeoutError:
                # CI-owned child only; preserve an honest failing outcome.
                process.kill()
                await process.wait()
                raise
        await asyncio.wait_for(stderr_task, 5)
    return {"schema": "jarvis.bundled-runtime-qualification.v1", "source_commit": commit,
            "manifest_sha256": pin, "runtime_bytes": runtime.bytes,
            "runtime_files": runtime.files, "relocated": True,
            "external_python": False, "external_codex": False, "external_source": False,
            "native_dependencies_imported": True, "opus_available": True,
            "tls_ca_loaded": True, "minilm_offline_cpu_dimension": 384,
            "official_codex_empty_account": True, "signed_ipc_sqlite": True,
            "parent_eof_clean_shutdown": True, "profile_lease_released": True,
            "login_item_changed": False, "owner_accounts_used": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cualificación remota del runtime autocontenido")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--pin", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        async def controlled_worker():
            task = asyncio.create_task(worker(args.root, args.pin, args.work, args.source_commit))
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(signum, task.cancel)
            return await task

        result = asyncio.run(controlled_worker())
        print(json.dumps(result, sort_keys=True))
        return
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("JARVIS_TEST_GUARD_PROFILE") != "ci"
            or os.environ.get("JARVIS_TEST_GUARD_CHILD_CHECKED")
            != os.environ.get("JARVIS_TEST_GUARD_NONCE")):
        parser.error("La cualificación completa sólo se ejecuta bajo el supervisor de CI.")
    if args.app is None or args.receipt is None:
        parser.error("Faltan el bundle y el destino del recibo.")
    args.work.mkdir(mode=0o700)
    moved = args.work / "Movido con espacios" / "Jarvis.app"
    moved.parent.mkdir(mode=0o700)
    shutil.copytree(args.app, moved)
    info = plistlib.loads((moved / "Contents/Info.plist").read_bytes())
    if info.get("JarvisDistributionChannel") != "self-contained":
        parser.error("El bundle no es autocontenido.")
    pin = info["JarvisRuntimeManifestSHA256"]
    root = moved / "Contents/Resources/JarvisRuntime"
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"}
    for name in ("home", "tmp", "smoke"):
        (args.work / name).mkdir(mode=0o700)
    environment.update(HOME=str(args.work / "home"), TMPDIR=str(args.work / "tmp") + "/")
    command = [str(root / "python/bin/python3.11"), "-I", "-S", "-B", "-u", "-c",
               BOOTSTRAP, str(root / "core"), str(root / "site-packages"),
               str(Path(__file__).resolve()), "--worker", "--root", str(root), "--pin", pin,
               "--work", str(args.work / "smoke"), "--source-commit", args.source_commit]
    started = time.monotonic()
    code, output, errors = bounded_capture(command, environment=environment, timeout=240)
    if code:
        # This corpus contains no account credentials. Keep the useful bounded
        # CI failure, without ever exporting generated identity files or profiles.
        sys.stderr.buffer.write(errors[-16384:])
        raise RuntimeError("packaged_runtime_qualification_failed")
    receipt = json.loads(output)
    receipt["seconds"] = round(time.monotonic() - started, 3)
    receipt["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
