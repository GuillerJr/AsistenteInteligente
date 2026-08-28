"""Canonical public routing API.

``routing`` remains import-compatible for existing integrations while new code may use
the less ambiguous ``brain.router`` module requested by the native hybrid brain.
"""

from aegis_core.brain.routing import (
    DEEP_REASONING_MODEL_ID,
    FAST_PLANNING_MODEL_ID,
    SPECULATIVE_VERIFIER_MODEL_ID,
    CascadeDecision,
    CascadeTarget,
    ConfidenceMetrics,
    RoutingPolicySnapshot,
    TaskClassification,
    TaskComplexity,
    TokenConfidence,
    calibrate_token_confidence,
    classify_task,
    contains_non_text_content,
    contains_private_data,
    decide_cascade,
    extract_text,
    extract_user_request,
)

__all__ = [
    "DEEP_REASONING_MODEL_ID",
    "FAST_PLANNING_MODEL_ID",
    "SPECULATIVE_VERIFIER_MODEL_ID",
    "CascadeDecision",
    "CascadeTarget",
    "ConfidenceMetrics",
    "RoutingPolicySnapshot",
    "TaskClassification",
    "TaskComplexity",
    "TokenConfidence",
    "calibrate_token_confidence",
    "classify_task",
    "contains_non_text_content",
    "contains_private_data",
    "decide_cascade",
    "extract_text",
    "extract_user_request",
]
