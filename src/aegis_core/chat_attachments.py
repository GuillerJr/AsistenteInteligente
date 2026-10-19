"""Bounded file references, isolated from the user's instruction and tool permissions."""
from __future__ import annotations

from collections.abc import Sequence

from aegis_core.contracts import DocumentInput, UserRequest
from aegis_core.memory.contracts import ConversationTurn


def document_reference(
    request: UserRequest, history: Sequence[ConversationTurn],
) -> dict[str, object]:
    selected = {}
    used = 0
    # Current files override older copies of the same name. Keep the most recent
    # bounded selection, within the same conversation, including after restarting.
    documents = [*request.documents]
    for turn in reversed(history):
        documents.extend(turn.documents)
    documents.extend(DocumentInput.model_validate(d)
                     for d in request.metadata.get("chat_document_context", ()))
    for document in documents:
        size = len(document.content.encode("utf-8"))
        if document.name in selected or len(selected) >= 4 or used + size > 65_536:
            continue
        selected[document.name] = document.model_dump()
        used += size
    return {"attached_documents": list(selected.values())} if selected else {}
