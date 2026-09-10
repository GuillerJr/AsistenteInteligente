from __future__ import annotations

from collections import deque
from collections.abc import AsyncGenerator, Iterable
from typing import TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.history import History
from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.output import ColorDepth, create_output
from prompt_toolkit.validation import Validator

from aegis_core.engineering import (
    EngineeringDomain,
    EngineeringInferencePolicy,
    EngineeringResearchPolicy,
)
from aegis_core.secrets import contains_likely_secret_material

MAX_INPUT_CHARACTERS = 50_000


class PrivateHistory(History):
    """Bounded editor recall, not durable conversation memory. Never touches disk."""

    def __init__(self) -> None:
        super().__init__()
        self._entries: deque[str] = deque()
        self._bytes = 0

    async def load(self) -> AsyncGenerator[str, None]:
        for entry in tuple(reversed(self._entries)):
            yield entry

    def load_history_strings(self) -> Iterable[str]:
        return reversed(self._entries)

    def get_strings(self) -> list[str]:
        return list(self._entries)

    def append_string(self, string: str) -> None:
        self.store_string(string)

    def store_string(self, string: str) -> None:
        size = len(string.encode("utf-8"))
        if size > 65_536 or contains_likely_secret_material(string):
            return
        if self._entries and self._entries[-1] == string:
            return
        while self._entries and (len(self._entries) >= 100 or self._bytes + size > 65_536):
            self._bytes -= len(self._entries.popleft().encode("utf-8"))
        self._entries.append(string)
        self._bytes += size

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0


class EngineeringInput:
    def __init__(self, stdin: TextIO, stderr: TextIO, *, color: bool) -> None:
        self.history = PrivateHistory()
        self._input = create_input(stdin=stdin)
        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event: KeyPressEvent) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def newline(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        self._session: PromptSession[str] = PromptSession(
            input=self._input,
            output=create_output(stdout=stderr),
            color_depth=ColorDepth.DEPTH_8_BIT if color else ColorDepth.DEPTH_1_BIT,
            history=self.history,
            completer=NestedCompleter.from_nested_dict(
                {
                    "/help": None,
                    "/status": None,
                    "/new": None,
                    "/clear": None,
                    "/resume": None,
                    "/cancel": None,
                    "/exit": None,
                    "/workspace": None,
                    "/domain": {value.value for value in EngineeringDomain},
                    "/research": {value.value for value in EngineeringResearchPolicy},
                    "/inference": {value.value for value in EngineeringInferencePolicy},
                }
            ),
            complete_while_typing=False,
            enable_history_search=True,
            multiline=True,
            prompt_continuation="  · ",
            key_bindings=bindings,
            validator=Validator.from_callable(
                lambda text: len(text) <= MAX_INPUT_CHARACTERS and "\0" not in text,
                error_message="Máximo 50 000 caracteres, sin NUL.",
            ),
            reserve_space_for_menu=3,
            refresh_interval=0,
        )

    async def read(self, domain: str) -> str:
        return await self._session.prompt_async(
            [
                ("ansicyan bold", "◆ "),
                ("ansicyan", domain),
                ("", " → "),
            ]
        )

    def close(self) -> None:
        self.history.clear()
        self._input.close()
