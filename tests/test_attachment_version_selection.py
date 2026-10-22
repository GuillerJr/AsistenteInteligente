from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegis_core.chat_attachments import document_reference
from aegis_core.contracts import DocumentInput, UserRequest
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.memory.sqlite import SQLiteMemoryStore


def turn(sequence, document):
    return ConversationTurn(
        conversation_id=uuid4(), sequence=sequence, role=ConversationRole.USER,
        content="Revisa el adjunto.",
        content_sha256=ConversationTurn.digest_content("Revisa el adjunto."),
        created_at=datetime.now(UTC), documents=(document,),
    )


def test_omitted_latest_version_never_falls_back_to_smaller_old_copy():
    old = DocumentInput(name="informe.txt", content="Versión obsoleta.")
    newest = DocumentInput(name=old.name, content="V" * 32_768)
    request = UserRequest(text="Resume los datos actuales.", documents=(
        DocumentInput(name="a.txt", content="a" * 32_768),
        DocumentInput(name="b.txt", content="b" * 30_000),
    ), metadata={"chat_document_context": [old.model_dump()]})
    reference = document_reference(request, (turn(1, old), turn(3, newest)))
    assert [doc["name"] for doc in reference["attached_documents"]] == ["a.txt", "b.txt"]
    assert reference["attached_documents_omitted"] == ["informe.txt"]
    assert reference["attached_documents_truncated"] is True
    assert old.content not in str(reference)


def test_document_count_limit_is_reported_and_duplicate_versions_do_not_count_twice():
    old = DocumentInput(name="quinto.txt", content="Obsoleto.")
    newer = DocumentInput(name=old.name, content="Actual.")
    request = UserRequest(text="Resume.", documents=tuple(
        DocumentInput(name=f"{index}.txt", content="Contenido.") for index in range(4)
    ))
    reference = document_reference(request, (turn(1, old), turn(3, newer)))
    assert len(reference["attached_documents"]) == 4
    assert reference["attached_documents_omitted"] == [old.name]


def test_newest_small_version_remains_selected_with_utf8_budget():
    old = DocumentInput(name="informe.txt", content="Obsoleto.")
    newer = DocumentInput(name=old.name, content="😀" * 192)
    current = DocumentInput(name="actual.txt", content="😀" * 8_192)
    reference = document_reference(UserRequest(text="Resume.", documents=(current,)),
                                   (turn(1, old), turn(3, newer)))
    assert reference == {"attached_documents": [current.model_dump(), newer.model_dump()]}


def test_persisted_selection_does_not_resurrect_skipped_version_after_restart(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "synthetic.sqlite3"
    store = SQLiteMemoryStore(path, encryption_secret=b"a" * 32)
    store.initialize()
    record = store.create_conversation(namespace="user.synthetic", title="Prueba")
    old = DocumentInput(name="informe.txt", content="Versión obsoleta.")
    newest = DocumentInput(name=old.name, content="V" * 32_768)
    current = (DocumentInput(name="a.txt", content="a" * 32_768),
               DocumentInput(name="b.txt", content="b" * 30_000))
    for documents in [(old,), (newest,), current]:
        store.append_conversation_exchange(
            namespace="user.synthetic", conversation_id=record.conversation_id,
            user_content="Revisa.", assistant_content="Recibido.", documents=documents,
        )
    restarted = SQLiteMemoryStore(path, encryption_secret=b"a" * 32)
    restarted.initialize()
    selected = restarted.conversation_documents(namespace="user.synthetic",
                                                  conversation_id=record.conversation_id)
    assert selected == current
    assert all(document.name != old.name for document in selected)


@pytest.mark.parametrize("same_content", [False, True])
def test_request_rejects_ambiguous_duplicate_attachment_names(same_content):
    with pytest.raises(ValidationError, match="attachment names must be unique"):
        UserRequest(text="Resume.", documents=(
            DocumentInput(name="informe.txt", content="Primero."),
            DocumentInput(name="informe.txt", content="Primero." if same_content else "Segundo."),
        ))
