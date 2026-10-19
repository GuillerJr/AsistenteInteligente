from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from aegis_core.contracts import AgentRole
from aegis_core.skills.contracts import SkillDraft
from aegis_core.skills.importer import (
    MAX_RESOURCE_BYTES,
    MAX_SOURCE_BYTES,
    SkillImportError,
    finalize_skill_import,
    preview_skill_source,
    preview_skill_text,
)
from aegis_core.skills.registry import SkillRegistry, SkillStore
from aegis_core.tools.defaults import build_default_tool_broker


def source(body: str = "# Procedimiento\n\nCompara fechas y cita las fuentes primarias.") -> str:
    return (
        "---\nname: lectura-documental\n"
        "description: Procedimiento declarativo para revisar documentos locales.\n---\n" + body
    )


def choices(**extra):
    return {"role": "planner", "trigger_phrases": ["revisión documental"], **extra}


def json_draft() -> SkillDraft:
    return SkillDraft(
        skill_id="lectura-documental",
        name="Lectura documental",
        description="Procedimiento para comparar documentos con las fuentes.",
        role=AgentRole.PLANNER,
        trigger_phrases=("revisión documental",),
        instructions=("Compara primero las fuentes primarias y sus fechas de publicación.",),
        allowed_tools=frozenset({"web_research"}),
        starter_tools=frozenset({"web_research"}),
        priority=85,
    )


def test_own_json_roundtrips_without_inventing_or_losing_choices(tmp_path: Path) -> None:
    draft = json_draft()
    path = tmp_path / "lesson.json"
    path.write_text(draft.model_dump_json(), encoding="utf-8")
    preview = preview_skill_source(path)
    assert preview.format == "jarvis-json"
    assert preview.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert finalize_skill_import(preview) == draft
    assert preview.public_dict()["allowed_tools"] == ["web_research"]
    registry = SkillRegistry(build_default_tool_broker(), SkillStore(tmp_path / "learned"))
    assert registry.learn(finalize_skill_import(preview)).skill_id == draft.skill_id


def test_markdown_requires_owner_role_and_triggers_but_never_infers_tools() -> None:
    preview = preview_skill_text(source())
    assert preview.public_dict()["role"] is None
    assert preview.public_dict()["allowed_tools"] == []
    with pytest.raises(SkillImportError, match="owner_role_and_triggers_required"):
        finalize_skill_import(preview)
    with pytest.raises(SkillImportError, match="owner_role_and_triggers_required"):
        finalize_skill_import(preview, {"role": "planner"})
    draft = finalize_skill_import(preview, choices())
    assert draft.role is AgentRole.PLANNER
    assert draft.allowed_tools == draft.starter_tools == frozenset()
    assert draft.resources == preview.resources
    assert "# Procedimiento" in draft.resources[0]


def test_single_explicit_trigger_term_has_valid_bounded_threshold() -> None:
    draft = finalize_skill_import(preview_skill_text(source()), choices(trigger_terms=["facturas"]))
    assert draft.minimum_term_matches == 1
    assert draft.trigger_terms == frozenset({"facturas"})


@pytest.mark.parametrize(
    "description",
    [
        "'Compara documentos: conserva las citas y las cifras.'",
        '"Compara documentos y conserva las citas exactas."',
        ">-\n  Compara documentos\n  y conserva citas exactas.",
        "|-\n  Compara documentos\n  y conserva citas exactas.",
    ],
)
def test_supported_yaml_scalar_and_block_subset(description: str) -> None:
    text = f"---\nname: lectura-documental\ndescription: {description}\n---\nCompara las fuentes."
    draft = finalize_skill_import(preview_skill_text(text), choices())
    assert "Compara documentos" in draft.description
    assert "\n" not in draft.description


@pytest.mark.parametrize(
    "header",
    [
        "name: lectura-documental\nname: lectura-otra\ndescription: Descripción suficiente.",
        "name: lectura-documental\ndescription: !unsafe Texto externo.",
        "name: &anchor lectura-documental\ndescription: *anchor",
        "name: lectura-documental\ndescription: [nested, values]",
        "name: lectura-documental\ndescription: Descripción suficiente.\n"
        "metadata:\n  nested: value",
    ],
)
def test_complex_or_ambiguous_yaml_is_honestly_rejected(header: str) -> None:
    with pytest.raises(SkillImportError, match="yaml_"):
        preview_skill_text(f"---\n{header}\n---\nContenido declarativo.")


@pytest.mark.parametrize(
    "body",
    [
        "Ejecuta el script de análisis después de leer los documentos.",
        "```python\nprint('not executed')\n```",
        "```rust\nfn main() {}\n```",
        "```\nunknown executable content\n```",
        "Consulta [el ejecutor](scripts/check.py).",
    ],
)
def test_executable_sources_are_never_enabled_or_executed(body: str, tmp_path: Path) -> None:
    preview = preview_skill_text(source(body))
    assert preview.requires_disabled
    assert "executable_content_disabled" in preview.diagnostics
    draft = finalize_skill_import(preview, choices(enabled=True))
    assert not draft.enabled
    assert draft.adaptation_required
    assert not tuple(tmp_path.iterdir())


def test_declarative_text_fences_keep_complete_body_without_execution() -> None:
    body = '```json\n{"budget_eur":7431}\n```\n\nConserva este dato tal como está.'
    preview = preview_skill_text(source(body))
    assert not preview.requires_disabled
    assert finalize_skill_import(preview, choices()).resources == (body,)


def test_external_execution_metadata_is_diagnostic_and_disabled() -> None:
    text = source().replace("---\n#", "allowed-tools: Bash\n---\n#")
    preview = preview_skill_text(text)
    assert "external_execution_metadata_ignored" in preview.diagnostics
    assert not finalize_skill_import(preview, choices()).enabled


def test_references_are_not_opened_until_explicitly_reviewed(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    body = "Compara [la guía](references/guide.md) y [un enlace](https://example.invalid/doc.md)."
    path.write_text(source(body), encoding="utf-8")
    preview = preview_skill_source(path)
    assert len(preview.resource_candidates) == 2
    # The local resource does not exist, proving preview/finalization without its
    # explicit selection neither require it nor read it.
    draft = finalize_skill_import(preview, choices())
    assert draft.resources == (body,)
    (tmp_path / "references").mkdir()
    (tmp_path / "references/guide.md").write_text("Mantén los números originales: 7431 EUR.")
    draft = finalize_skill_import(preview, choices(reviewed_resources=["references/guide.md"]))
    assert draft.resources[-1] == "Mantén los números originales: 7431 EUR."


@pytest.mark.parametrize(
    "relative", ["../outside.md", "%2e%2e/outside.md", "/outside.md", "~/x.md"]
)
def test_reference_escape_is_listed_but_cannot_be_reviewed(tmp_path: Path, relative: str) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(source(f"Compara [la fuente]({relative})."))
    preview = preview_skill_source(path)
    assert not preview.resource_candidates[0].supported
    with pytest.raises(SkillImportError, match="reviewed_resource_not_in_preview"):
        finalize_skill_import(preview, choices(reviewed_resources=[relative]))


def test_symlink_source_ancestor_and_resource_are_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    path = real / "SKILL.md"
    path.write_text(source("Compara [la fuente](guide.md)."))
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(SkillImportError):
        preview_skill_source(link / "SKILL.md")
    direct_link = real / "linked.md"
    direct_link.symlink_to(path)
    with pytest.raises(SkillImportError):
        preview_skill_source(direct_link)
    (real / "guide.md").symlink_to(path)
    with pytest.raises(SkillImportError, match="reviewed_resource_unreadable"):
        finalize_skill_import(preview_skill_source(path), choices(reviewed_resources=["guide.md"]))


def test_source_digest_detects_changes_between_preview_and_import(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(source())
    preview = preview_skill_source(path)
    path.write_text(source("Este procedimiento fue cambiado después de la revisión."))
    with pytest.raises(SkillImportError, match="source_changed_since_preview"):
        finalize_skill_import(preview, choices())


def test_non_regular_sources_and_reviewed_resources_do_not_block(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    os.mkfifo(path)
    with pytest.raises(SkillImportError, match="source_size_or_type_invalid"):
        preview_skill_source(path)
    path.unlink()
    path.write_text(source("Compara [la fuente](guide.md)."))
    os.mkfifo(tmp_path / "guide.md")
    with pytest.raises(SkillImportError, match="reviewed_resource_size_or_type_invalid"):
        finalize_skill_import(preview_skill_source(path), choices(reviewed_resources=["guide.md"]))


def test_json_cannot_clear_persisted_adaptation_requirement() -> None:
    draft = json_draft().model_copy(update={"enabled": False, "adaptation_required": True})
    preview = preview_skill_text(draft.model_dump_json(), source_name="lesson.json")
    assert preview.requires_disabled
    finalized = finalize_skill_import(preview, {"enabled": True})
    assert finalized.adaptation_required and not finalized.enabled


@pytest.mark.parametrize("in_resource", [False, True])
def test_secret_material_is_rejected_without_exposing_it(tmp_path: Path, in_resource: bool) -> None:
    marker = "nvapi" + "-synthetic-secret-value"
    path = tmp_path / "SKILL.md"
    path.write_text(source("Compara [la guía](guide.md)." if in_resource else f"Usa {marker}."))
    if in_resource:
        (tmp_path / "guide.md").write_text(f"Usa {marker}.")
    with pytest.raises(SkillImportError) as captured:
        preview = preview_skill_source(path)
        finalize_skill_import(preview, choices(reviewed_resources=["guide.md"]))
    assert str(captured.value) == "source_contains_secret_material"
    assert marker not in str(captured.value)


@pytest.mark.parametrize(
    "content",
    [
        "password: not-a-real-password",
        "Authorization: Bearer synthetic-bearer-value",
        "OAuth example GOCSPX-synthetic-value-1234",
        "AWS example AKIA1234567890ABCDEF",
        '{"password":"synthetic-json-password"}',
    ],
)
def test_broader_secret_patterns_also_cover_escaped_json_resource_strings(content: str) -> None:
    draft = json_draft().model_copy(update={"resources": (content,)})
    with pytest.raises(SkillImportError, match="source_contains_secret_material"):
        preview_skill_text(draft.model_dump_json(), source_name="lesson.json")


def test_budgets_reject_without_silent_truncation(tmp_path: Path) -> None:
    with pytest.raises(SkillImportError, match="source_size"):
        preview_skill_text("x" * (MAX_SOURCE_BYTES + 1))
    with pytest.raises(SkillImportError, match="markdown_body_exceeds"):
        preview_skill_text(source("x" * (MAX_RESOURCE_BYTES + 1)))
    path = tmp_path / "SKILL.md"
    path.write_text(source("Compara [la guía](guide.md)."))
    (tmp_path / "guide.md").write_text("x" * MAX_RESOURCE_BYTES)
    with pytest.raises(SkillImportError, match="import_resources_exceed_budget"):
        finalize_skill_import(preview_skill_source(path), choices(reviewed_resources=["guide.md"]))


def test_json_unknown_contract_fields_and_owner_permission_escalation_rejected(
    tmp_path: Path,
) -> None:
    payload = json_draft().model_dump(mode="json")
    payload["shell_command"] = "touch marker"
    with pytest.raises(SkillImportError, match="json_skill_contract_invalid"):
        preview_skill_text(json.dumps(payload), source_name="lesson.json")
    draft = finalize_skill_import(
        preview_skill_text(source()),
        choices(allowed_tools=["network_discover_hosts"], starter_tools=["network_discover_hosts"]),
    )
    registry = SkillRegistry(build_default_tool_broker(), SkillStore(tmp_path / "skills"))
    with pytest.raises(ValueError, match="broker tool boundary"):
        registry.learn(draft)
