from __future__ import annotations

import warnings

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

# LangChain installs its warning policy at package import time. Load that policy
# first, then suppress only the one transitive serializer notice emitted while
# LangGraph initializes. No global filter survives this compatibility boundary.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"The default value of `allowed_objects`",
        category=LangChainPendingDeprecationWarning,
    )
    from langgraph.graph import END, START, StateGraph

__all__ = ["END", "START", "StateGraph"]
