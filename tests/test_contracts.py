import pytest
from pydantic import ValidationError

from aegis_core.contracts import AgentRole, RiskLevel, RouteDecision, UserRequest


def test_request_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        UserRequest(text="")


def test_route_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RouteDecision(
            role=AgentRole.PLANNER,
            risk=RiskLevel.LOW,
            reason="normal request",
            unexpected=True,
        )
