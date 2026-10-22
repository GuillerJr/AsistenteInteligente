"""Bounded file references, isolated from the user's instruction and tool permissions."""
from __future__ import annotations

from collections.abc import Sequence

from aegis_core.contracts import DocumentInput, UserRequest
from aegis_core.memory.contracts import ConversationTurn


def document_reference(
    request: UserRequest, history: Sequence[ConversationTurn],
) -> dict[str, object]:
    selected = {}
    seen = set()
    omitted = []
    used = 0
    # Current files override older copies of the same name. Keep the most recent
    # bounded selection, within the same conversation, including after restarting.
    documents = [*request.documents]
    for turn in reversed(history):
        documents.extend(turn.documents)
    documents.extend(DocumentInput.model_validate(d)
                     for d in request.metadata.get("chat_document_context", ()))
    for document in documents:
        if document.name in seen:
            continue
        # The newest version owns this name even when it does not fit. Falling
        # back to a shorter older copy would silently present obsolete evidence.
        seen.add(document.name)
        size = len(document.content.encode("utf-8"))
        if len(selected) >= 4 or used + size > 65_536:
            omitted.append(document.name)
            continue
        selected[document.name] = document.model_dump()
        used += size
    reference = {"attached_documents": list(selected.values())} if selected else {}
    if omitted:
        reference["attached_documents_omitted"] = omitted
        reference["attached_documents_truncated"] = True
    return reference
