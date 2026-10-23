"""Provider-independent failures at the bounded inference transport boundary."""

from __future__ import annotations


class ProviderError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None = None, terminal: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.terminal = terminal


class ProviderRateLimitedError(ProviderError):
    def __init__(
        self, message: str = "inference rate limited", *,
        status_code: int = 429, terminal: bool = True,
    ) -> None:
        super().__init__(message, status_code=status_code, terminal=terminal)
