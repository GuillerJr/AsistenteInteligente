"""Diagnostics for exceptional failures at plugin, worker and cleanup boundaries."""
from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)


def record_boundary_error(error: Exception) -> None:
    """Never log the exception message, its traceback, arguments or user content."""
    LOGGER.warning("boundary_failed error_type=%s", type(error).__name__)
