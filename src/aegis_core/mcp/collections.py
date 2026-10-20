"""How each integration lists items and reads them back, independent of phrasing.

A follow-up such as "el segundo", "todos" or "el de LinkedIn" refers to items of a
verified list. Resolving it needs three facts per item kind: which tool produced the
list (and where its ids live), which tool reads one item, and optionally which tool
reads several at once. A new MCP integration declares those facts here, next to its
reviewed rules; the resolver, the planner guard and the approval flow use only this
registry, never a per-service branch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ListSource:
    """Where a list tool returns its items: payload key and item id field."""

    list_key: str
    id_key: str


@dataclass(frozen=True, slots=True)
class CollectionContract:
    kind: str
    # Spanish noun used in scope text and clarifications, and words that name it.
    noun: str
    plural: str
    synonyms: frozenset[str]
    list_tools: Mapping[str, ListSource]
    detail_tool: str
    detail_field: str
    batch_tool: str | None = None
    batch_field: str | None = None
    batch_max: int = 10
    # Detail reads that also need the item's container (a calendar for events).
    container_field: str | None = None

    def __post_init__(self) -> None:
        if (self.batch_tool is None) != (self.batch_field is None):
            raise ValueError("a batch read declares both its tool and its id field")
        if not 1 <= self.batch_max <= 25:
            raise ValueError("batch reads are bounded")

    @property
    def read_tools(self) -> frozenset[str]:
        return frozenset({self.detail_tool, *((self.batch_tool,) if self.batch_tool else ())})


_CONTRACTS: tuple[CollectionContract, ...] = (
    CollectionContract(
        kind="mail", noun="correo", plural="correos",
        synonyms=frozenset({"correo", "correos", "mensaje", "mensajes", "email", "emails",
                            "mail", "mails"}),
        list_tools=MappingProxyType({
            "search_gmail_messages": ListSource("messages", "message_id"),
        }),
        detail_tool="get_gmail_message_content", detail_field="message_id",
        batch_tool="get_gmail_messages_content_batch", batch_field="message_ids",
    ),
    CollectionContract(
        kind="file", noun="archivo en Drive", plural="archivos en Drive",
        synonyms=frozenset({"archivo", "archivos", "documento", "documentos", "fichero",
                            "ficheros"}),
        list_tools=MappingProxyType({
            "search_drive_files": ListSource("files", "file_id"),
            "list_drive_items": ListSource("items", "file_id"),
        }),
        detail_tool="get_drive_file_content", detail_field="file_id",
    ),
    CollectionContract(
        kind="event", noun="evento", plural="eventos",
        synonyms=frozenset({"evento", "eventos", "reunion", "reuniones", "cita", "citas"}),
        list_tools=MappingProxyType({"get_events": ListSource("events", "event_id")}),
        detail_tool="get_events", detail_field="event_id", container_field="calendar_id",
    ),
)

COLLECTIONS: Mapping[str, CollectionContract] = MappingProxyType(
    {contract.kind: contract for contract in _CONTRACTS}
)


def contract_for_list_tool(remote: str) -> CollectionContract | None:
    return next((c for c in _CONTRACTS if remote in c.list_tools), None)


def contract_for_read_tool(remote: str) -> CollectionContract | None:
    return next((c for c in _CONTRACTS if remote in c.read_tools), None)


def contract_for_word(word: str) -> CollectionContract | None:
    return next((c for c in _CONTRACTS if word in c.synonyms), None)


def collection_words() -> frozenset[str]:
    return frozenset(word for contract in _CONTRACTS for word in contract.synonyms)
