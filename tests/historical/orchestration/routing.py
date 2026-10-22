from __future__ import annotations

from aegis_core.contracts import AgentRole, RouteDecision, UserRequest
from aegis_core.orchestration.routing import _request_may_need_tools
from aegis_core.skills import SkillActivation


def _request_can_use_local_brain(
    request: UserRequest,
    route: RouteDecision,
    skill: SkillActivation | None = None,
) -> bool:
    return (
        route.role is AgentRole.PLANNER
        and request.image is None
        and request.metadata.get("force_remote") is not True
        and not _request_may_need_tools(request, skill)
        and len(request.text.encode("utf-8")) <= 1_200
    )
