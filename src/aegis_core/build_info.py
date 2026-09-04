from __future__ import annotations

import os
import re

BUILD_REVISION_ENVIRONMENT = "AEGIS_BUILD_REVISION"
DEVELOPMENT_BUILD_REVISION = "development"

_GIT_REVISION = re.compile(r"[0-9a-f]{40}")


def runtime_build_revision() -> str:
    """Return one bounded, non-secret source revision for runtime diagnostics."""
    candidate = os.environ.get(BUILD_REVISION_ENVIRONMENT, "").strip().casefold()
    if _GIT_REVISION.fullmatch(candidate):
        return candidate
    return DEVELOPMENT_BUILD_REVISION
