from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict
from uuid import UUID

from aegis_core.capability_learning import (
    CapabilityRecord,
)
from aegis_core.contracts import (
    AgentResult,
    RouteDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.dialogue import (
    DialogueGuidance,
)
from aegis_core.memory.contracts import ConversationTurn, MemorySearchHit
from aegis_core.memory.task_checkpoint import TaskCheckpoint
from aegis_core.orchestration.task_progress import (
    TaskResolution,
)
from aegis_core.skills import (
    SkillActivation,
)
from aegis_core.tools.computer_workflow import (
    ComputerWorkflowState,
)


class SwarmState(TypedDict, total=False):
    request: UserRequest
    tool_scope_request: UserRequest
    route: RouteDecision
    skill: SkillActivation
    skills: tuple[SkillActivation, ...]
    shared_skill_hashes: frozenset[str]
    capability_gap: bool
    capability_knowledge: CapabilityRecord
    direct_tool_call: ToolCall
    direct_local_result: AgentResult
    memory_hits: tuple[MemorySearchHit, ...]
    graph_memory_context: str
    social_memory_hits: tuple[MemorySearchHit, ...]
    conversation_history: tuple[ConversationTurn, ...]
    conversation_id: UUID | None
    computer_workflow: ComputerWorkflowState | None
    task_checkpoint: TaskCheckpoint | None
    task_resolution: TaskResolution
    dialogue: DialogueGuidance
    specialist_result: AgentResult
    specialist_results: tuple[AgentResult, ...]
    tool_authorizations: tuple[ToolAuthorization, ...]
    tool_results: tuple[ToolExecutionResult, ...]
    execution_contract: str
    reflection_count: int
    final_result: AgentResult
    errors: list[str]
    stream_callback: Callable[[str], None]
    browser_task_round: int
    browser_task_results: tuple[ToolExecutionResult, ...]
    browser_task_stopped: bool
