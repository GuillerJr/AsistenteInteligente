import json

import pytest

from aegis_core.contracts import ToolAuthorization
from aegis_core.skills.policy import SkillPolicyStore
from aegis_core.skills.registry import SkillRegistry, SkillStore
from aegis_core.skills.teach import SkillTeachHandlers, skill_confirmation_summary
from aegis_core.tools.defaults import build_default_tool_broker

ARGUMENTS = {
    "name": "Informe semanal",
    "description": "Prepara el informe semanal con correos y reuniones de la semana.",
    "when_to_use": ["mi informe semanal", "el informe de la semana"],
    "steps": ["Busca los correos de la semana", "Revisa la agenda", "Resume en tres partes"],
}


def _authorization(arguments):
    return ToolAuthorization.model_construct(call_id="c", tool_name="jarvis_skill_create",
                                             normalized_arguments=arguments)


def _registry(tmp_path):
    tmp_path.chmod(0o700)
    return SkillRegistry(build_default_tool_broker(), SkillStore(tmp_path / "skills"),
                         policy=SkillPolicyStore(tmp_path / "policy.json"), builtins=())


@pytest.mark.asyncio
async def test_taught_skill_is_learned_enabled_and_selectable(tmp_path):
    registry = _registry(tmp_path)
    handler = SkillTeachHandlers(registry).handlers()["jarvis_skill_create"]
    result = json.loads((await handler(_authorization(ARGUMENTS), None)).output)
    assert result["created"] is True and "Informe semanal" in result["message"]
    record = next(item for item in registry.catalogue() if item["skill_id"] == "informe-semanal")
    assert record["enabled"] is True
    selected = registry.select("prepárame mi informe semanal")
    assert selected is not None and selected.manifest.skill_id == "informe-semanal"
    assert selected.manifest.allowed_tools == frozenset()  # Guidance only.


@pytest.mark.asyncio
async def test_executable_steps_are_refused(tmp_path):
    registry = _registry(tmp_path)
    handler = SkillTeachHandlers(registry).handlers()["jarvis_skill_create"]
    bad = {**ARGUMENTS, "steps": ["Ejecuta ```bash\nrm -rf ~\n```"]}
    result = json.loads((await handler(_authorization(bad), None)).output)
    assert result["created"] is False
    assert not any(item["skill_id"] == "informe-semanal" for item in registry.catalogue())


def test_confirmation_lists_the_steps():
    summary = skill_confirmation_summary(_authorization(ARGUMENTS))
    assert "«Informe semanal»" in summary and "1. Busca los correos" in summary
