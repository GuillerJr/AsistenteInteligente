from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.capability_learning import (
    CapabilityLearningStatus,
    CapabilityRecord,
)
from aegis_core.contracts import RiskLevel


class BlueprintReadiness(StrEnum):
    NEEDS_RESEARCH = "needs_research"
    READY_FOR_REVIEW = "ready_for_review"
    SENSITIVE_REVIEW = "sensitive_review"


class CapabilityIntegrationPath(StrEnum):
    NATIVE_ADAPTER = "native_adapter"
    SHORTCUT_WORKFLOW = "shortcut_workflow"
    CAPABILITY_PACK = "capability_pack"


class BlueprintEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=12, max_length=2_048)


class CapabilityBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    gap_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    objective: str = Field(min_length=4, max_length=600)
    readiness: BlueprintReadiness
    integration_path: CapabilityIntegrationPath
    risk: RiskLevel
    priority_score: int = Field(ge=0, le=100)
    occurrences: int = Field(ge=1, le=10_000)
    review_domains: tuple[str, ...] = Field(min_length=1, max_length=4)
    safety_constraints: tuple[str, ...] = Field(min_length=3, max_length=8)
    acceptance_tests: tuple[str, ...] = Field(min_length=3, max_length=8)
    evidence: tuple[BlueprintEvidence, ...] = Field(default_factory=tuple, max_length=3)

    @model_validator(mode="after")
    def readiness_must_match_evidence(self) -> CapabilityBlueprint:
        if self.readiness is BlueprintReadiness.NEEDS_RESEARCH and self.evidence:
            raise ValueError("unresearched blueprint cannot contain evidence")
        if self.readiness is not BlueprintReadiness.NEEDS_RESEARCH and not self.evidence:
            raise ValueError("reviewable blueprint requires evidence")
        return self


_SENSITIVE_TERMS = frozenset(
    {
        "borra",
        "borrar",
        "buy",
        "compra",
        "comprar",
        "delete",
        "elimina",
        "eliminar",
        "envia",
        "enviar",
        "envía",
        "install",
        "instala",
        "instalar",
        "publish",
        "publica",
        "publicar",
        "reserve",
        "reserva",
        "reservar",
        "send",
        "sube",
        "subir",
        "upload",
    }
)
_PACK_TERMS = frozenset(
    {
        "buy",
        "compra",
        "comprar",
        "conecta",
        "conectar",
        "connect",
        "publish",
        "publica",
        "publicar",
        "reserve",
        "reserva",
        "reservar",
        "send",
        "sincroniza",
        "sincronizar",
        "sube",
        "subir",
        "sync",
        "upload",
    }
)
_SHORTCUT_TERMS = frozenset(
    {
        "automate",
        "automatiza",
        "automatizar",
        "backup",
        "organiza",
        "organizar",
        "respalda",
        "respaldar",
    }
)


def build_capability_blueprint(record: CapabilityRecord) -> CapabilityBlueprint:
    sensitive = not record.terms.isdisjoint(_SENSITIVE_TERMS)
    researched = record.status is CapabilityLearningStatus.RESEARCHED
    if not record.terms.isdisjoint(_PACK_TERMS):
        path = CapabilityIntegrationPath.CAPABILITY_PACK
        domains = ("external_service", "network_egress", "credential_boundary")
    elif not record.terms.isdisjoint(_SHORTCUT_TERMS):
        path = CapabilityIntegrationPath.SHORTCUT_WORKFLOW
        domains = ("shortcuts", "application_automation")
    else:
        path = CapabilityIntegrationPath.NATIVE_ADAPTER
        domains = ("native_api", "macos_tcc")

    readiness = (
        BlueprintReadiness.NEEDS_RESEARCH
        if not researched
        else BlueprintReadiness.SENSITIVE_REVIEW
        if sensitive
        else BlueprintReadiness.READY_FOR_REVIEW
    )
    evidence = tuple(
        BlueprintEvidence(title=source.title, url=source.url) for source in record.sources
    )
    safety_constraints = [
        "El Tool Broker conserva toda la autoridad de ejecución.",
        "La evidencia y las respuestas de modelos se tratan como datos no confiables.",
        "No se admiten shell, código descargado ni permisos implícitos.",
        "Toda mutación exige una confirmación de un solo uso.",
    ]
    if path is CapabilityIntegrationPath.CAPABILITY_PACK:
        safety_constraints.append(
            "El endpoint debe ser HTTPS público, con esquema cerrado y credencial en Keychain."
        )
    elif path is CapabilityIntegrationPath.SHORTCUT_WORKFLOW:
        safety_constraints.append(
            "El atajo debe existir previamente y se invoca solo por nombre exacto."
        )
    else:
        safety_constraints.append(
            "El adaptador debe usar una API nativa acotada y fallar si TCC no autoriza."
        )
    acceptance_tests = (
        "Ejecuta únicamente el objetivo y argumentos autorizados.",
        "Deniega argumentos extra, secretos y destinos fuera de alcance.",
        "Falla cerrado ante timeout, permiso ausente o salida inválida.",
        "Audita autorización y resultado sin guardar contenido privado.",
        "Rechaza replay y llamadas propuestas fuera del conjunto ofrecido.",
    )
    priority = min(100, 35 + min(record.occurrences, 4) * 10 + (25 if researched else 0))
    return CapabilityBlueprint(
        gap_id=record.gap_id,
        objective=record.normalized_goal,
        readiness=readiness,
        integration_path=path,
        risk=RiskLevel.CRITICAL if sensitive else RiskLevel.HIGH,
        priority_score=priority,
        occurrences=record.occurrences,
        review_domains=domains,
        safety_constraints=tuple(safety_constraints),
        acceptance_tests=acceptance_tests,
        evidence=evidence,
    )
