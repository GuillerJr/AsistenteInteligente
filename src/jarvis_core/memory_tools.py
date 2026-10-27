"""Memory adapters for the same run authority as filesystem tools."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict

from jarvis_core.local_embeddings import EngineError
from jarvis_core.memory import MemoryError, MemoryScope, MemoryStore
from jarvis_core.memory_intelligence import MemoryIntelligenceError
from jarvis_core.memory_policy import MemoryPolicyError
from jarvis_core.model_artifacts import ArtifactError
from jarvis_core.runtime import ToolDefinition, ToolResult
from jarvis_core.tools import ToolSpec, check_tool_authority


class MemoryTools:
    def __init__(self, store: MemoryStore, scope: Callable[[], MemoryScope], *,
                 search=None) -> None:
        self.store, self.scope = store, scope
        self.search = search

    @staticmethod
    def _validate(name: str, arguments: Mapping) -> None:
        allowed = {
            "memory_recall": {"query", "mode"},
            "memory_store": {"key", "text", "title", "source_uri", "expected_revision"},
            "memory_forget": {"key", "expected_revision"},
        }[name]
        required = {"query"} if name == "memory_recall" else {"key"}
        if name == "memory_store":
            required.add("text")
        if name == "memory_forget":
            required.add("expected_revision")
        if set(arguments) - allowed or required - set(arguments):
            raise ValueError("memory_arguments_invalid")
        if "key" in arguments and (
            not isinstance(arguments["key"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", arguments["key"]) is None
        ):
            raise ValueError("memory_key_invalid")
        for key, limit in (("query", 512), ("text", 8000), ("title", 256), ("source_uri", 512)):
            if key in arguments and (
                not isinstance(arguments[key], str)
                or len(arguments[key].encode()) > limit
                or not arguments[key].strip()
            ):
                raise ValueError("memory_text_invalid")
        if "expected_revision" in arguments and (
            type(arguments["expected_revision"]) is not int or arguments["expected_revision"] < 1
        ):
            raise ValueError("memory_revision_invalid")
        if "mode" in arguments and (not isinstance(arguments["mode"], str)
                or arguments["mode"] not in {"lexical", "semantic_local", "hybrid_local"}):
            raise ValueError("memory_search_mode_invalid")

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                name,
                lambda arguments, name=name: self._validate(name, arguments),
                lambda arguments, cancellation, name=name: self._execute(
                    name, arguments, cancellation
                ),
                requires_confirmation=name != "memory_recall",
            )
            for name in ("memory_recall", "memory_store", "memory_forget")
        )

    def specs(self) -> tuple[ToolSpec, ...]:
        properties = {
            "query": {"type": "string", "maxLength": 512},
            "mode": {"type": "string", "enum": ["lexical", "semantic_local", "hybrid_local"]},
            "key": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
            "text": {"type": "string", "maxLength": 8000},
            "title": {"type": "string", "maxLength": 256},
            "source_uri": {"type": "string", "maxLength": 512},
            "expected_revision": {"type": "integer", "minimum": 1},
        }
        entries = (
            (
                "memory_recall",
                "Busca memoria local de esta carpeta; devuelve citas del snapshot. "
                "El modo predeterminado es léxico; semantic_local y hybrid_local usan "
                "únicamente MiniLM local preparado por el dueño. "
                "Si falta, se informa sin cambiar de motor. "
                "Una etiqueta de fuente no prueba su origen.",
                ("query", "mode"),
                ("query",),
            ),
            (
                "memory_store",
                "Guarda texto explícito con aprobación del dueño. Para reemplazar "
                "una nota consulta antes su revisión y envía expected_revision. "
                "Nunca guarda permisos.",
                ("key", "text", "title", "source_uri", "expected_revision"),
                ("key", "text"),
            ),
            (
                "memory_forget",
                "Elimina una nota con aprobación del dueño y su revisión exacta.",
                ("key", "expected_revision"),
                ("key", "expected_revision"),
            ),
        )
        return tuple(
            ToolSpec(
                name,
                description,
                {
                    "type": "object",
                    "properties": {key: properties[key] for key in keys},
                    "required": list(required),
                    "additionalProperties": False,
                },
            )
            for name, description, keys, required in entries
        )

    async def _execute(
        self, name: str, arguments: Mapping, cancellation: asyncio.Event
    ) -> ToolResult:
        self._validate(name, arguments)
        if cancellation.is_set():
            raise asyncio.CancelledError
        scope = self.scope()
        committed = False
        mutation_dispatched = False
        try:
            if name == "memory_recall":
                mode = arguments.get("mode", "lexical")
                if self.search is not None:
                    found = await self.search(scope, arguments["query"], mode=mode, limit=5)
                elif mode == "lexical":
                    found = self.store.search(scope, arguments["query"], limit=5)
                else:
                    raise MemoryError("memory_semantic_unavailable")
                if cancellation.is_set():
                    raise asyncio.CancelledError
                result = asdict(found)
            else:
                check_tool_authority(name, arguments)
                if name == "memory_store":
                    try:
                        existing = self.store.get(scope, arguments["key"])
                    except MemoryError as error:
                        if error.code != "document_not_found":
                            raise
                    else:
                        if arguments.get("expected_revision") != existing.document.revision:
                            return ToolResult(name, False, False, error="memory_revision_required")
                    check_tool_authority(name, arguments, commit=True)
                    mutation_dispatched = True
                    record = self.store.upsert(
                        scope,
                        arguments["key"],
                        arguments["text"],
                        source_uri=arguments.get("source_uri", "jarvis://provided-text"),
                        title=arguments.get("title", ""),
                        expected_revision=arguments.get("expected_revision"),
                    )
                    committed = True
                    reread = self.store.get(scope, arguments["key"])
                    if reread.text != arguments["text"] or reread.document != record:
                        return ToolResult(
                            name,
                            False,
                            False,
                            effect_status="unknown",
                            error="memory_verification_failed",
                        )
                    result = asdict(record)
                else:
                    check_tool_authority(name, arguments, commit=True)
                    mutation_dispatched = True
                    result = asdict(
                        self.store.delete(
                            scope,
                            arguments["key"],
                            expected_revision=arguments["expected_revision"],
                        )
                    )
                    committed = True
                    try:
                        self.store.get(scope, arguments["key"])
                    except MemoryError as error:
                        if error.code != "document_not_found":
                            raise
                    else:
                        return ToolResult(
                            name,
                            False,
                            False,
                            effect_status="unknown",
                            error="memory_verification_failed",
                        )
            return ToolResult(name, True, True, output=json.dumps(result, ensure_ascii=False))
        except (MemoryError, MemoryIntelligenceError, MemoryPolicyError,
                EngineError, ArtifactError) as error:
            deterministic = error.code in {
                "revision_conflict",
                "memory_capacity_exceeded",
                "invalid_revision",
                "invalid_document_key",
                "invalid_document_source",
                "invalid_document_text",
                "document_not_found",
            }
            return ToolResult(
                name,
                False,
                False,
                error=error.code,
                effect_status=(
                    "unknown"
                    if committed or (mutation_dispatched and not deterministic)
                    else "known"
                ),
            )
