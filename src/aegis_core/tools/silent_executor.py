from __future__ import annotations

import asyncio
from collections import defaultdict
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.brain.behavior_tree import BehaviorStatus, TacticalUIBehaviorTree
from aegis_core.brain.vision_processor import VisualState


class SilentActionKind(StrEnum):
    PRESS = "press"
    TYPE = "type"
    SCROLL = "scroll"


class SilentAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SilentActionKind
    process_identifier: int = Field(gt=1, le=2_147_483_647)
    bundle_identifier: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{2,254}$")
    accessibility_identifier: str | None = Field(default=None, min_length=1, max_length=256)
    accessibility_label: str | None = Field(default=None, min_length=1, max_length=256)
    text: str | None = Field(default=None, min_length=1, max_length=2_048)
    delta_x: int | None = Field(default=None, ge=-64, le=64)
    delta_y: int | None = Field(default=None, ge=-64, le=64)
    fallback_x: float | None = Field(default=None, ge=0.0, le=100_000.0)
    fallback_y: float | None = Field(default=None, ge=0.0, le=100_000.0)
    expected_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def action_fields_are_coherent(self) -> SilentAction:
        has_selector = bool(self.accessibility_identifier or self.accessibility_label)
        if not has_selector:
            raise ValueError("silent action requires an Accessibility selector")
        if self.kind is SilentActionKind.TYPE and self.text is None:
            raise ValueError("type action requires text")
        if self.kind is not SilentActionKind.TYPE and self.text is not None:
            raise ValueError("text is only valid for type action")
        if self.kind is SilentActionKind.SCROLL and not (self.delta_x or self.delta_y):
            raise ValueError("scroll action requires a non-zero delta")
        if self.kind is not SilentActionKind.SCROLL and (
            self.delta_x is not None or self.delta_y is not None
        ):
            raise ValueError("scroll deltas are invalid for this action")
        if (self.fallback_x is None) != (self.fallback_y is None):
            raise ValueError("fallback coordinates must be paired")
        return self


class SilentExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    pathway: str = Field(pattern=r"^(accessibility|process_event|none)$")
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_changed: bool
    error_code: str | None = Field(default=None, max_length=96)
    recovery_status: str = Field(
        default="not_needed",
        pattern=r"^(not_needed|recovered|user_intervention)$",
    )
    recovery_cycles: int = Field(default=0, ge=0, le=3)


class SilentNativeBridge(Protocol):
    async def observe(self, process_identifier: int, bundle_identifier: str) -> VisualState: ...

    async def accessibility_action(self, action: SilentAction) -> bool: ...

    async def process_event(self, action: SilentAction) -> bool: ...


class SilentRecoveryBridge(Protocol):
    async def dismiss_modal(self, state: VisualState) -> bool: ...

    async def reload(self, process_identifier: int, bundle_identifier: str) -> bool: ...

    async def request_user_intervention(self, reason: str) -> None: ...


class SilentExecutor:
    """AX-first, per-PID executor that never takes the owner's hardware cursor.

    ES: “Accesibilidad primero” significa pedir al control lógico de macOS que se
    pulse, escriba o desplace. Solo cuando ese control declara que la acción no es
    compatible usamos un evento dirigido al PID; nunca publicamos en el flujo HID
    global que alimenta el ratón físico.

    EN: Accessibility actions are deterministic and focus-preserving. A targeted
    process event is a bounded fallback, not a license to move the global cursor.
    """

    def __init__(
        self,
        bridge: SilentNativeBridge,
        *,
        recovery_bridge: SilentRecoveryBridge | None = None,
        verification_delay_seconds: float = 0.05,
    ):
        if not 0.01 <= verification_delay_seconds <= 0.25:
            raise ValueError("silent verification delay is out of range")
        self._bridge = bridge
        self._recovery = recovery_bridge
        self._verification_delay = verification_delay_seconds
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def execute(self, action: SilentAction) -> SilentExecutionResult:
        # ES: una cerradura por PID impide que dos planes modifiquen la misma app
        # simultáneamente y luego atribuyan el cambio visual a la acción equivocada.
        # EN: the per-process lock preserves causal ordering for verification.
        async with self._locks[action.process_identifier]:
            result, state = await self._execute_once(action)
            if result.error_code != "state_verification_failed" or self._recovery is None:
                return result
            return await self._recover(action, result, state)

    async def _execute_once(
        self,
        action: SilentAction,
    ) -> tuple[SilentExecutionResult, VisualState]:
        # ES: primero tomamos una huella estructural. expected_state_sha256 enlaza
        # la intención con la pantalla que el planificador observó; si ya cambió,
        # el clic sería una acción sobre contexto obsoleto y se rechaza sin tocar UI.
        # EN: the pre-action hash is an optimistic-concurrency token for the UI.
        before = await self._bridge.observe(
            action.process_identifier,
            action.bundle_identifier,
        )
        if before.state_sha256 != action.expected_state_sha256:
            return (
                self._result(False, "none", before, before, "stale_visual_context"),
                before,
            )
        pathway = "accessibility"
        # ES: AX es la ruta primaria porque invoca la semántica del elemento
        # (“presionar este botón”) sin coordenadas, foco ni movimiento del cursor.
        # EN: coordinates are used only for the PID-targeted fallback explicitly
        # bounded by the previously authorized Accessibility selector.
        accepted = await self._bridge.accessibility_action(action)
        if not accepted:
            if action.fallback_x is None or action.fallback_y is None:
                return (
                    self._result(
                        False,
                        "none",
                        before,
                        before,
                        "accessibility_action_unsupported",
                    ),
                    before,
                )
            pathway = "process_event"
            accepted = await self._bridge.process_event(action)
        if not accepted:
            return (
                self._result(False, pathway, before, before, "event_dispatch_rejected"),
                before,
            )
        await asyncio.sleep(self._verification_delay)
        after = await self._bridge.observe(
            action.process_identifier,
            action.bundle_identifier,
        )
        changed = before.state_sha256 != after.state_sha256
        # ES: comparar state_sha256 antes/después es una prueba de efecto, no una
        # suposición. Un dispatcher puede aceptar el evento aunque la app lo ignore;
        # sin cambio verificable devolvemos state_verification_failed y el árbol de
        # comportamiento intenta quitar un modal o solicita ayuda tras tres ciclos.
        # EN: dispatch success is not action success; observable state change is the
        # commit point that allows the execution plan to continue.
        return (
            self._result(
                changed,
                pathway,
                before,
                after,
                None if changed else "state_verification_failed",
            ),
            after,
        )

    async def _recover(
        self,
        action: SilentAction,
        initial_result: SilentExecutionResult,
        initial_state: VisualState,
    ) -> SilentExecutionResult:
        assert self._recovery is not None
        current_state = initial_state
        latest_result = initial_result
        retry_cycles = 0

        async def retry_action() -> bool:
            nonlocal current_state, latest_result, retry_cycles
            retry_cycles = min(retry_cycles + 1, 3)
            retry = action.model_copy(update={"expected_state_sha256": current_state.state_sha256})
            latest_result, current_state = await self._execute_once(retry)
            return latest_result.success

        async def dismiss_modal() -> bool:
            nonlocal current_state
            dismissed = await self._recovery.dismiss_modal(current_state)
            if not dismissed:
                return False
            await asyncio.sleep(self._verification_delay)
            current_state = await self._bridge.observe(
                action.process_identifier,
                action.bundle_identifier,
            )
            return True

        async def reevaluate() -> bool:
            nonlocal current_state
            current_state = await self._bridge.observe(
                action.process_identifier,
                action.bundle_identifier,
            )
            return True

        async def reload_action() -> bool:
            nonlocal current_state
            reloaded = await self._recovery.reload(
                action.process_identifier,
                action.bundle_identifier,
            )
            if not reloaded:
                return False
            await asyncio.sleep(self._verification_delay)
            current_state = await self._bridge.observe(
                action.process_identifier,
                action.bundle_identifier,
            )
            return True

        tree = TacticalUIBehaviorTree(
            try_action=retry_action,
            detect_modal=lambda: self._contains_modal(current_state),
            dismiss_modal=dismiss_modal,
            reevaluate=reevaluate,
            reload_action=reload_action,
            retry_parent=retry_action,
            on_user_intervention=self._recovery.request_user_intervention,
        )
        context = await tree.run(graph_context=current_state.markdown)
        if context.terminal_status is BehaviorStatus.SUCCESS and latest_result.success:
            return latest_result.model_copy(
                update={
                    "recovery_status": "recovered",
                    "recovery_cycles": retry_cycles,
                }
            )
        return latest_result.model_copy(
            update={
                "success": False,
                "state_changed": False,
                "error_code": "user_intervention_required",
                "recovery_status": "user_intervention",
                "recovery_cycles": 3,
            }
        )

    @staticmethod
    def _contains_modal(state: VisualState) -> bool:
        haystack = " ".join(
            (state.markdown, *(element.label for element in state.elements))
        ).casefold()
        return any(
            marker in haystack
            for marker in (
                "cookie",
                "consent",
                "modal",
                "popup",
                "diálogo",
                "dialog",
                "aceptar cookies",
            )
        )

    @staticmethod
    def _result(
        success: bool,
        pathway: str,
        before: VisualState,
        after: VisualState,
        error_code: str | None,
    ) -> SilentExecutionResult:
        return SilentExecutionResult(
            success=success,
            pathway=pathway,
            before_sha256=before.state_sha256,
            after_sha256=after.state_sha256,
            state_changed=before.state_sha256 != after.state_sha256,
            error_code=error_code,
        )
