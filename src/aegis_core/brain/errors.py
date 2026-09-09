from __future__ import annotations


class BrainUnavailableError(RuntimeError):
    """No trusted local inference path could satisfy the request."""


class RemoteProviderUnavailableError(RuntimeError):
    """The configured remote specialist failed before a safe fallback existed."""
