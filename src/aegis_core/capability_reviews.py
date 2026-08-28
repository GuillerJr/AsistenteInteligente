from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.capability_blueprints import (
    BlueprintEvidence,
    BlueprintReadiness,
    CapabilityBlueprint,
    CapabilityIntegrationPath,
    build_capability_blueprint,
)
from aegis_core.capability_learning import CapabilityRecord
from aegis_core.contracts import RiskLevel


class CapabilityReviewGate(StrEnum):
    RESEARCH_REQUIRED = "research_required"
    OWNER_REVIEW = "owner_review"
    SECURITY_REVIEW = "security_review"


class CapabilityArtifactKind(StrEnum):
    CORE_TOOL_DESIGN = "core_tool_design"
    SHORTCUT_CONTRACT = "shortcut_contract"
    CAPABILITY_PACK_DRAFT = "capability_pack_draft"


class CapabilityRequiredInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str = Field(min_length=8, max_length=300)


class CapabilityReviewDossier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    gap_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    objective: str = Field(min_length=4, max_length=600)
    artifact_kind: CapabilityArtifactKind
    review_gate: CapabilityReviewGate
    readiness: BlueprintReadiness
    risk: RiskLevel
    priority_score: int = Field(ge=0, le=100)
    occurrences: int = Field(ge=1, le=10_000)
    execution_allowed: Literal[False] = False
    required_inputs: tuple[CapabilityRequiredInput, ...] = Field(min_length=4, max_length=6)
    implementation_sequence: tuple[str, ...] = Field(min_length=4, max_length=6)
    security_checks: tuple[str, ...] = Field(min_length=3, max_length=8)
    acceptance_tests: tuple[str, ...] = Field(min_length=3, max_length=8)
    evidence: tuple[BlueprintEvidence, ...] = Field(default_factory=tuple, max_length=3)
    integrity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def contract_must_be_consistent_and_intact(self) -> CapabilityReviewDossier:
        expected_gate = _review_gate(self.readiness)
        if self.review_gate is not expected_gate:
            raise ValueError("capability review gate does not match readiness")
        if (
            self.risk is RiskLevel.CRITICAL
            and self.readiness is not BlueprintReadiness.NEEDS_RESEARCH
            and self.review_gate is not CapabilityReviewGate.SECURITY_REVIEW
        ):
            raise ValueError("critical capability requires security review")
        keys = tuple(item.key for item in self.required_inputs)
        if len(keys) != len(set(keys)):
            raise ValueError("capability required inputs must be unique")
        if self.integrity_sha256 != _dossier_integrity(
            self.model_dump(exclude={"integrity_sha256"}, mode="json")
        ):
            raise ValueError("capability review dossier integrity does not match")
        return self

    @classmethod
    def create(
        cls,
        *,
        blueprint: CapabilityBlueprint,
        artifact_kind: CapabilityArtifactKind,
        required_inputs: tuple[CapabilityRequiredInput, ...],
        implementation_sequence: tuple[str, ...],
    ) -> CapabilityReviewDossier:
        values = {
            "schema_version": 1,
            "gap_id": blueprint.gap_id,
            "objective": blueprint.objective,
            "artifact_kind": artifact_kind,
            "review_gate": _review_gate(blueprint.readiness),
            "readiness": blueprint.readiness,
            "risk": blueprint.risk,
            "priority_score": blueprint.priority_score,
            "occurrences": blueprint.occurrences,
            "execution_allowed": False,
            "required_inputs": required_inputs,
            "implementation_sequence": implementation_sequence,
            "security_checks": blueprint.safety_constraints,
            "acceptance_tests": blueprint.acceptance_tests,
            "evidence": blueprint.evidence,
        }
        return cls(**values, integrity_sha256=_dossier_integrity(values))


_PATH_CONTRACTS: Mapping[
    CapabilityIntegrationPath,
    tuple[
        CapabilityArtifactKind,
        tuple[CapabilityRequiredInput, ...],
        tuple[str, ...],
    ],
] = MappingProxyType(
    {
        CapabilityIntegrationPath.NATIVE_ADAPTER: (
            CapabilityArtifactKind.CORE_TOOL_DESIGN,
            (
                CapabilityRequiredInput(
                    key="native_framework",
                    description="API o framework público de macOS que resuelve el objetivo.",
                ),
                CapabilityRequiredInput(
                    key="permission_scope",
                    description=(
                        "Permiso TCC mínimo necesario o declaración explícita de que no aplica."
                    ),
                ),
                CapabilityRequiredInput(
                    key="closed_input_schema",
                    description="Argumentos tipados, acotados y sin propiedades adicionales.",
                ),
                CapabilityRequiredInput(
                    key="verification_probe",
                    description="Señal local observable que demuestra el resultado sin inferirlo.",
                ),
            ),
            (
                "Validar que la API nativa cubra el objetivo sin automatización visual genérica.",
                "Diseñar un adaptador mínimo con entrada cerrada, timeout y salida acotada.",
                "Registrar la herramienta con capacidad, riesgo y confirmación correspondientes.",
                "Probar permisos ausentes, entradas adversarias, resultado y auditoría.",
            ),
        ),
        CapabilityIntegrationPath.SHORTCUT_WORKFLOW: (
            CapabilityArtifactKind.SHORTCUT_CONTRACT,
            (
                CapabilityRequiredInput(
                    key="exact_shortcut_name",
                    description="Nombre exacto de un atajo creado o revisado por el propietario.",
                ),
                CapabilityRequiredInput(
                    key="closed_input_schema",
                    description="Entrada tipada y acotada que el atajo acepta.",
                ),
                CapabilityRequiredInput(
                    key="bounded_output_schema",
                    description="Salida estructurada y limitada que Jarvis puede verificar.",
                ),
                CapabilityRequiredInput(
                    key="verification_probe",
                    description="Comprobación local independiente del efecto solicitado.",
                ),
            ),
            (
                "Revisar manualmente todas las acciones contenidas en el atajo.",
                "Fijar nombre exacto, entrada cerrada, timeout y salida máxima.",
                "Exponer solo el invocador nativo de Shortcuts mediante el Tool Broker.",
                "Probar atajo ausente, salida inválida, cancelación, replay y auditoría.",
            ),
        ),
        CapabilityIntegrationPath.CAPABILITY_PACK: (
            CapabilityArtifactKind.CAPABILITY_PACK_DRAFT,
            (
                CapabilityRequiredInput(
                    key="public_https_endpoint",
                    description="Endpoint MCP HTTPS público y estable del proveedor seleccionado.",
                ),
                CapabilityRequiredInput(
                    key="auth_mode",
                    description=(
                        "Modo de autenticación explícito; cualquier secreto vive en Keychain."
                    ),
                ),
                CapabilityRequiredInput(
                    key="declared_capabilities",
                    description="Capacidades mínimas que usa cada operación externa.",
                ),
                CapabilityRequiredInput(
                    key="closed_tool_schemas",
                    description="Esquemas MCP cerrados y acotados para todas las herramientas.",
                ),
                CapabilityRequiredInput(
                    key="verification_probe",
                    description="Lectura independiente que verifica el resultado remoto.",
                ),
            ),
            (
                "Verificar identidad, documentación y endpoint oficial del proveedor.",
                "Definir manifiesto, hosts exactos, capacidades y esquemas cerrados.",
                "Empaquetar y comprobar el checksum sin incluir credenciales ni código ejecutable.",
                "Simular selección y política antes de cualquier instalación explícita.",
                "Probar DNS adversario, timeout, respuesta inválida, replay y auditoría.",
            ),
        ),
    }
)


def build_capability_review_dossier(record: CapabilityRecord) -> CapabilityReviewDossier:
    blueprint = build_capability_blueprint(record)
    artifact_kind, required_inputs, sequence = _PATH_CONTRACTS[blueprint.integration_path]
    return CapabilityReviewDossier.create(
        blueprint=blueprint,
        artifact_kind=artifact_kind,
        required_inputs=required_inputs,
        implementation_sequence=sequence,
    )


def _review_gate(readiness: BlueprintReadiness) -> CapabilityReviewGate:
    if readiness is BlueprintReadiness.NEEDS_RESEARCH:
        return CapabilityReviewGate.RESEARCH_REQUIRED
    if readiness is BlueprintReadiness.SENSITIVE_REVIEW:
        return CapabilityReviewGate.SECURITY_REVIEW
    return CapabilityReviewGate.OWNER_REVIEW


def _dossier_integrity(values: dict[str, Any]) -> str:
    payload = json.dumps(
        values,
        ensure_ascii=False,
        allow_nan=False,
        default=lambda item: (
            item.value if isinstance(item, StrEnum) else item.model_dump(mode="json")
        ),
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
