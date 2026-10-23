import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aegis_core import __version__
from aegis_core.activity import SwarmActivityTracker
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.orchestration.graph import build_nvidia_swarm_graph, build_swarm_graph
from aegis_core.orchestration.inference import create_completion


def test_installed_runtime_imports_without_retired_hybrid_modules():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", "import sys, json, importlib.metadata; "
         "import aegis_core.runtime.daemon as daemon; "
         "print(json.dumps({'modules': sorted(sys.modules), 'origin': daemon.__file__, "
         "'version': importlib.metadata.version('aegis-swarm')}))"], cwd=root,
        check=True, capture_output=True, text=True, timeout=30,
    )
    installed = json.loads(result.stdout)
    assert installed["version"] == __version__, (
        f"El entorno tiene aegis-swarm {installed['version']} en {installed['origin']}; "
        f"el proyecto requiere {__version__}. Actualiza la instalación del proyecto."
    )
    retired = [name for name in installed["modules"]
               if "hybrid_client" in name or "speculative_engine" in name
               or "mlx_distributed" in name or name.startswith("historical")]
    assert not retired, f"El runtime de {installed['origin']} importa módulos retirados: {retired}"
    for path in ("brain/hybrid_client.py", "brain/speculative_engine.py",
                 "providers/mlx_distributed.py", "providers/apple.py",
                 "orchestration/compat_inference.py"):
        assert not (root / "src/aegis_core" / path).exists()


def test_installed_mlx_module_only_retains_voice_transcription():
    from aegis_core.orchestration import routing
    from aegis_core.providers import mlx_provider

    assert callable(mlx_provider.MLXWhisperTranscriber.transcribe_pcm_s16le)
    assert not hasattr(routing, "_request_can_use_local_brain")
    for retired in ("MLXProvider", "MLXDraftVerification", "MLXVerificationIpcService"):
        assert not hasattr(mlx_provider, retired)


def test_legacy_nvidia_factory_preserves_explicit_provider_policy():
    builder = build_nvidia_swarm_graph
    with pytest.raises(ValueError, match="NVIDIA exclusively"):
        builder(object(), inference_nvidia_only=False)
    with pytest.raises(ValueError, match="NVIDIA exclusively"):
        builder(object(), local_provider=object())


def test_configured_graph_has_no_nvidia_requirement_or_implicit_fallback():
    assert build_swarm_graph(object(), inference_nvidia_only=False) is not None
    with pytest.raises(ValueError, match="one configured inference provider"):
        build_swarm_graph(object(), local_provider=object())


def test_production_factory_rejects_provider_without_nvidia_contract():
    with pytest.raises(ValueError, match="NVIDIA-only completion"):
        build_nvidia_swarm_graph(object())


@pytest.mark.asyncio
async def test_provider_failure_never_enters_local_or_cascade_path():
    class Provider:
        async def complete_nvidia_only(self, **kwargs):
            raise OSError("synthetic outage")

        async def complete(self, **kwargs):
            pytest.fail("alternate provider was invoked")

        async def complete_cascade(self, **kwargs):
            pytest.fail("retired cascade was invoked")

    complete = create_completion(Provider(), SwarmActivityTracker())
    with pytest.raises(OSError):
        await complete(AgentRole.PLANNER, messages=[])


@pytest.mark.asyncio
async def test_nvidia_completion_preserves_cancellation_and_stream_callback():
    emitted = []

    class Provider:
        async def complete_nvidia_only(self, **kwargs):
            kwargs["on_delta"]("Listo")
            return AgentResult(role=kwargs["role"], model_id="nvidia/test", content="Listo")

    complete = create_completion(Provider(), SwarmActivityTracker())
    assert (await complete(AgentRole.PLANNER, messages=[], stream_callback=emitted.append)).content
    assert emitted == ["Listo"]

    class Cancelled:
        async def complete_nvidia_only(self, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await create_completion(Cancelled(), SwarmActivityTracker())(AgentRole.PLANNER, messages=[])
