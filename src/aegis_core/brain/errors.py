from __future__ import annotations


class BrainUnavailableError(RuntimeError):
    """No trusted local inference path could satisfy the request."""


class RemoteProviderUnavailableError(RuntimeError):
    """The configured remote specialist failed before a safe fallback existed."""


class RemoteRateLimitedError(RemoteProviderUnavailableError):
    """Remote quota/cooldown prevents execution; never downgrade the user's policy."""


class RemoteAuthenticationError(RemoteProviderUnavailableError):
    """The configured remote credential is missing, rejected or lacks access."""


class ModelContextLimitError(ValueError):
    """Input cannot fit the selected local model; never silently drop the user's text."""
