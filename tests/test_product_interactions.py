"""Product UI data contracts; all sources, clients and clocks are synthetic."""

from dataclasses import replace

import pytest

from jarvis_core.product_interactions import (
    InteractionActor,
    InteractionScope,
    ProductInteractionError,
    ProductInteractions,
)


class Host:
    def __init__(self):
        self.now = 1_000_000_000
        self.revoked = False
        self.sources = {"selected_run": {"type": "runs", "label": "Ejecución",
                                          "text": "Fuente sintética seleccionada"}}
        self.scope = InteractionScope("session_a", "run_a", "jarvis", "workspace_a")
        self.actor = InteractionActor("client_a")
        self.checks = []

    def guard(self, scope, actor, write):
        self.checks.append((scope, actor, write))
        if (self.revoked or scope.agent_id != "jarvis" or scope.workspace_id != "workspace_a"
                or scope.generation != 0 or actor.client_id not in {"client_a", "client_b"}):
            raise ProductInteractionError("product_host_scope_revoked")

    def source(self, scope, actor, identity):
        self.guard(scope, actor, False)
        if identity not in self.sources:
            raise ProductInteractionError("product_source_not_selected")
        return self.sources[identity]

    def store(self, path):
        return ProductInteractions(path, assert_current=self.guard,
                                   resolve_source=self.source, clock=lambda: self.now)


@pytest.fixture
def product(tmp_path):
    host = Host()
    store = host.store(tmp_path / "interactions.sqlite3")
    try:
        yield host, store
    finally:
        store.close()


def questions():
    return [{"id": "style", "header": "Estilo", "prompt": "¿Qué estilo prefieres?",
             "options": [{"id": "simple", "label": "Simple", "description": "Vista compacta"},
                         {"id": "expanded", "label": "Amplio", "description": "Vista detallada"}]}]


def panel():
    return {"title": "Panel local", "layout": "grid",
            "theme": {"appearance": "system", "accent": "accent"},
            "widgets": [{"id": "tasks", "type": "checklist", "title": "Pasos",
                         "symbol": "list.bullet", "color": "primary",
                         "items": [{"text": "Revisar", "checked": False}]}]}


def create(host, store, **kwargs):
    return store.create_question(host.scope, host.actor, questions(), **kwargs)


def test_question_answers_are_volatile_data_never_authority_or_history(product, tmp_path):
    host, store = product
    question = create(host, store)
    assert question["payload"]["questions"][0]["allow_freeform"]
    receipt = store.answer_question(host.scope, host.actor, question["id"], 1,
                                    {"style": {"text": "RESPUESTA_SINTETICA_PRIVADA_789"}})
    assert receipt == {"id": question["id"], "revision": 2, "status": "answered",
                       "grants_tool_approval": False}
    snapshot = store.snapshot(host.scope, host.actor)
    assert "RESPUESTA_SINTETICA_PRIVADA_789" not in str(snapshot)
    assert b"RESPUESTA_SINTETICA_PRIVADA_789" not in (
        tmp_path / "interactions.sqlite3").read_bytes()
    result = store.consume_answers(host.scope, host.actor, question["id"], 2)
    assert result["answers"] == {"style": {"text": "RESPUESTA_SINTETICA_PRIVADA_789"}}
    assert result["grants_tool_approval"] is False
    with pytest.raises(ProductInteractionError, match="answers_unavailable"):
        store.consume_answers(host.scope, host.actor, question["id"], 3)
    assert not store._answers


@pytest.mark.parametrize("answers", [
    {"style": {"option_id": "simple", "text": "ambas"}},
    {"style": {"option_id": "unknown"}},
    {"style": {"option_id": ["simple", "expanded"]}},
    {"style": {"text": ""}},
    {"other_question": {"text": "sin correspondencia"}},
])
def test_answer_choice_is_exclusive_and_correlated(product, answers):
    host, store = product
    question = create(host, store)
    with pytest.raises(ProductInteractionError, match=r"(answers_invalid|text_invalid)"):
        store.answer_question(host.scope, host.actor, question["id"], 1, answers)
    assert store.get(host.scope, host.actor, question["id"])["status"] == "pending"


def test_cas_cancel_and_expiry_prevent_late_answers(product):
    host, store = product
    question = create(host, store, ttl_seconds=1)
    with pytest.raises(ProductInteractionError, match="revision_changed"):
        store.answer_question(host.scope, host.actor, question["id"], 0,
                              {"style": {"option_id": "simple"}})
    store.cancel(host.scope, host.actor, question["id"], 1)
    with pytest.raises(ProductInteractionError, match="question_closed"):
        store.answer_question(host.scope, host.actor, question["id"], 2,
                              {"style": {"option_id": "simple"}})
    question = create(host, store, ttl_seconds=1)
    host.now += 1_000_000_000
    assert store.get(host.scope, host.actor, question["id"])["status"] == "expired"
    with pytest.raises(ProductInteractionError, match="question_expired"):
        store.answer_question(host.scope, host.actor, question["id"], 1,
                              {"style": {"text": "tarde"}})


@pytest.mark.parametrize("count", [0, 4])
def test_question_count_is_bounded(product, count):
    host, store = product
    with pytest.raises(ProductInteractionError, match="questions_invalid"):
        store.create_question(host.scope, host.actor, questions() * count)


def test_sensitive_question_cannot_capture_owner_secret(product):
    host, store = product
    values = questions()
    values[0]["sensitive"] = True
    with pytest.raises(ProductInteractionError, match="sensitive_input_requires_owner_surface"):
        store.create_question(host.scope, host.actor, values)
    assert not store._answers


def test_scope_client_role_and_generation_fail_closed(product):
    host, store = product
    question = create(host, store)
    other_scope = replace(host.scope, session_id="session_b", run_id="run_b")
    assert store.snapshot(other_scope, host.actor)["interactions"] == []
    assert store.snapshot(host.scope, InteractionActor("client_b"))["interactions"] == []
    with pytest.raises(ProductInteractionError, match="not_found"):
        store.get(other_scope, host.actor, question["id"])
    with pytest.raises(ProductInteractionError, match="owner_required"):
        store.answer_question(host.scope, InteractionActor("client_a", "read"),
                              question["id"], 1, {"style": {"option_id": "simple"}})
    with pytest.raises(ProductInteractionError, match="host_scope_revoked"):
        store.get(replace(host.scope, generation=1), host.actor, question["id"])
    host.revoked = True
    with pytest.raises(ProductInteractionError, match="host_scope_revoked"):
        store.get(host.scope, host.actor, question["id"])


def test_temporary_scope_never_opens_or_initializes_disk(tmp_path):
    host = Host()
    host.scope = replace(host.scope, temporary=True)
    path = tmp_path / "never-created" / "interactions.sqlite3"
    store = host.store(path)
    question = create(host, store)
    assert store.get(host.scope, host.actor, question["id"])["status"] == "pending"
    assert store._state._disk is None
    assert not path.parent.exists()
    store.close()
    store.close()


def test_restart_recovers_metadata_without_recovering_or_replaying_answers(tmp_path):
    host = Host()
    path = tmp_path / "interactions.sqlite3"
    store = host.store(path)
    pending = create(host, store)
    answered = create(host, store)
    store.answer_question(host.scope, host.actor, answered["id"], 1,
                          {"style": {"text": "SOLO_RAM"}})
    store.close()
    store = host.store(path)
    try:
        assert store.get(host.scope, host.actor, pending["id"])["status"] == "interrupted"
        view = store.get(host.scope, host.actor, answered["id"])
        assert view["status"] == "answered" and view["answers_available"] is False
        with pytest.raises(ProductInteractionError, match="answers_unavailable"):
            store.consume_answers(host.scope, host.actor, answered["id"], 2)
    finally:
        store.close()


async def test_wait_for_answer_is_bound_and_cancellation_closes_question(product):
    import asyncio

    host, store = product
    question = create(host, store)
    waiter = asyncio.create_task(store.wait_answers(host.scope, host.actor, question["id"]))
    await asyncio.sleep(0)
    store.answer_question(host.scope, host.actor, question["id"], 1,
                          {"style": {"option_id": "expanded"}})
    assert (await waiter)["answers"] == {"style": {"option_id": "expanded"}}
    question = create(host, store)
    waiter = asyncio.create_task(store.wait_answers(host.scope, host.actor, question["id"]))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert store.get(host.scope, host.actor, question["id"])["status"] == "cancelled"


def test_progress_presence_and_suggestions_are_revised_bound_data(product):
    host, store = product
    progress = store.publish_progress(host.scope, host.actor, expected_revision=0,
                                      label="Leyendo", value=10)
    with pytest.raises(ProductInteractionError, match="revision_changed"):
        store.publish_progress(host.scope, host.actor, expected_revision=0, label="Obsoleto")
    progress = store.publish_progress(host.scope, host.actor, expected_revision=1,
                                      label="Terminado", value=100, status="completed")
    assert progress["status"] == "completed"
    with pytest.raises(ProductInteractionError, match="progress_closed"):
        store.publish_progress(host.scope, host.actor, expected_revision=2, label="Repetir")
    presence = store.publish_presence(host.scope, host.actor, expected_revision=0,
                                      state="waiting_user", ttl_seconds=1)
    suggestions = store.publish_suggestions(
        host.scope, host.actor, expected_revision=0,
        suggestions=[{"id": "review", "label": "Revisar", "text": "Revisa el resultado"}],
        ttl_seconds=1,
    )
    assert store.select_suggestion(host.scope, host.actor, suggestions["id"], 1, "review") == {
        "draft_text": "Revisa el resultado", "execution": "draft_only",
        "grants_tool_approval": False,
    }
    host.now += 1_000_000_000
    assert store.get(host.scope, host.actor, presence["id"])["status"] == "expired"
    with pytest.raises(ProductInteractionError, match="suggestions_closed"):
        store.select_suggestion(host.scope, host.actor, suggestions["id"], 1, "review")


@pytest.mark.parametrize("value", [True, -1, 101, float("nan"), float("inf")])
def test_progress_rejects_nonfinite_or_invalid_values(product, value):
    host, store = product
    with pytest.raises(ProductInteractionError, match="progress_invalid"):
        store.publish_progress(host.scope, host.actor, expected_revision=0,
                               label="Paso", value=value)


def test_panel_theme_checklist_and_live_selected_internal_sources(product, tmp_path):
    host, store = product
    definition = panel()
    definition["widgets"].append({"id": "source", "type": "text", "title": "Fuente",
                                   "source_id": "selected_run"})
    view = store.set_panel(host.scope, host.actor, expected_revision=0, panel=definition)
    assert view["payload"]["rendering"] == "native_declarative"
    assert view["payload"]["widgets"][1]["source"]["type"] == "runs"
    assert b"Fuente sint\xc3\xa9tica seleccionada" not in (
        tmp_path / "interactions.sqlite3").read_bytes()
    edited = store.toggle_widget_item(host.scope, host.actor, view["id"], 1,
                                      widget_id="tasks", item_index=0, checked=True)
    assert edited["payload"]["widgets"][0]["items"][0]["checked"] is True
    with pytest.raises(ProductInteractionError, match="revision_changed"):
        store.toggle_widget_item(host.scope, host.actor, view["id"], 1,
                                  widget_id="tasks", item_index=0, checked=False)
    host.sources.clear()
    with pytest.raises(ProductInteractionError, match="source_not_selected"):
        store.get(host.scope, host.actor, view["id"])


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(html="<script>ejecutar()</script>"),
    lambda value: value["theme"].update(css="body{}"),
    lambda value: value["widgets"][0].update(on_click="shell"),
    lambda value: value["widgets"][0].update(symbol="https://example.invalid/a.svg"),
    lambda value: value["widgets"][0].update(source_id="https://example.invalid"),
    lambda value: value["widgets"][0].update(source_id="unselected"),
])
def test_panel_rejects_executable_markup_remote_sources_and_styles(product, mutation):
    host, store = product
    definition = panel()
    mutation(definition)
    with pytest.raises(ProductInteractionError):
        store.set_panel(host.scope, host.actor, expected_revision=0, panel=definition)


def test_scope_revocation_clears_answers_and_cannot_reopen_scope(product):
    host, store = product
    question = create(host, store)
    store.answer_question(host.scope, host.actor, question["id"], 1,
                          {"style": {"text": "RAM_REVOCADA"}})
    store.revoke_scope(host.scope, host.actor)
    assert not store._answers
    assert store.get(host.scope, host.actor, question["id"])["status"] == "revoked"
    with pytest.raises(ProductInteractionError, match="scope_revoked"):
        store.create_question(host.scope, host.actor, questions())
    with pytest.raises(ProductInteractionError, match="scope_revoked"):
        store.consume_answers(host.scope, host.actor, question["id"], 3)


def enable_notifications(host, store):
    return store.configure_notifications(host.scope, host.actor, expected_revision=0,
                                         enabled=True, silenced=False)


def attention(host, store, **kwargs):
    return store.request_attention(host.scope, host.actor, kind="run_completed",
                                    correlation_id=host.scope.run_id, **kwargs)


def test_notifications_are_opt_in_generic_correlated_and_deduplicated(product):
    host, store = product
    with pytest.raises(ProductInteractionError, match="notifications_disabled"):
        attention(host, store)
    assert enable_notifications(host, store)["revision"] == 1
    with pytest.raises(ProductInteractionError, match="revision_changed"):
        enable_notifications(host, store)
    with pytest.raises(ProductInteractionError, match="attention_correlation_invalid"):
        store.request_attention(host.scope, host.actor, kind="run_completed",
                                 correlation_id="other_run")
    first = attention(host, store)
    assert attention(host, store)["id"] == first["id"]
    assert first["payload"]["body"] == "Abre Jarvis para revisar la actividad de tu chat."
    other_scope = replace(host.scope, run_id="run_b")
    second = store.request_attention(other_scope, host.actor, kind="run_completed",
                                      correlation_id="run_b")
    assert second["id"] != first["id"]


def test_claim_is_exact_revalidated_and_cannot_deliver_twice(product):
    host, store = product
    enable_notifications(host, store)
    item = attention(host, store)
    claim = store.claim_notification(host.scope, host.actor, item["id"], 1)
    with pytest.raises(ProductInteractionError, match="revision_changed"):
        store.claim_notification(host.scope, host.actor, item["id"], 1)
    with pytest.raises(ProductInteractionError, match="notification_claim_invalid"):
        store.validate_notification_claim(host.scope, host.actor, item["id"], 2, "wrong_claim")
    assert store.validate_notification_claim(host.scope, host.actor, item["id"], 2,
                                              claim["claim"])["valid"]
    receipt = store.settle_notification(host.scope, host.actor, item["id"], 2,
                                         claim["claim"], outcome="delivered")
    assert receipt["status"] == "delivered" and receipt["revision"] == 3
    with pytest.raises(ProductInteractionError, match="notification_closed"):
        store.claim_notification(host.scope, host.actor, item["id"], 3)
    assert attention(host, store)["status"] == "delivered"
    with pytest.raises(ProductInteractionError, match="notification_claim_invalid"):
        store.settle_notification(host.scope, host.actor, item["id"], 3,
                                    claim["claim"], outcome="delivered")


def test_silence_or_optout_revokes_claim_before_native_effect(product):
    host, store = product
    enable_notifications(host, store)
    item = attention(host, store)
    claim = store.claim_notification(host.scope, host.actor, item["id"], 1)
    store.configure_notifications(host.scope, host.actor, expected_revision=1,
                                    enabled=True, silenced=True)
    assert store.get(host.scope, host.actor, item["id"])["status"] == "revoked"
    with pytest.raises(ProductInteractionError, match="revision_changed"):
        store.validate_notification_claim(host.scope, host.actor, item["id"], 2, claim["claim"])
    with pytest.raises(ProductInteractionError, match="notifications_disabled"):
        attention(host, store)
    store.configure_notifications(host.scope, host.actor, expected_revision=2,
                                    enabled=True, silenced=False)
    assert attention(host, store)["status"] == "revoked"  # no automatic requeue


def test_client_revocation_is_rechecked_immediately_before_native_effect(product):
    host, store = product
    enable_notifications(host, store)
    item = attention(host, store)
    claim = store.claim_notification(host.scope, host.actor, item["id"], 1)
    host.revoked = True
    with pytest.raises(ProductInteractionError, match="host_scope_revoked"):
        store.validate_notification_claim(host.scope, host.actor, item["id"], 2, claim["claim"])


def test_restart_keeps_unknown_delivery_and_never_requeues(tmp_path):
    host = Host()
    path = tmp_path / "interactions.sqlite3"
    store = host.store(path)
    enable_notifications(host, store)
    item = attention(host, store)
    store.claim_notification(host.scope, host.actor, item["id"], 1)
    store.close()
    store = host.store(path)
    try:
        view = store.get(host.scope, host.actor, item["id"])
        assert view["status"] == "unknown" and view["revision"] == 3
        assert attention(host, store)["status"] == "unknown"
        with pytest.raises(ProductInteractionError, match="notification_closed"):
            store.claim_notification(host.scope, host.actor, item["id"], 3)
        resumed = store.resume_notification(host.scope, host.actor, item["id"], 3)
        assert resumed["navigation_only"] is True and resumed["grants_tool_approval"] is False
        assert resumed["session_id"] == "session_a" and resumed["run_id"] == "run_a"
        with pytest.raises(ProductInteractionError, match="notification_closed"):
            store.resume_notification(host.scope, host.actor, item["id"], 4)
    finally:
        store.close()


def test_question_notification_cannot_outlive_question_or_expiry(product):
    host, store = product
    enable_notifications(host, store)
    question = create(host, store, ttl_seconds=1)
    item = store.request_attention(host.scope, host.actor, kind="question",
                                     correlation_id=question["id"], ttl_seconds=900)
    assert item["expires_ns"] == question["expires_ns"]
    store.answer_question(host.scope, host.actor, question["id"], 1,
                           {"style": {"option_id": "simple"}})
    with pytest.raises(ProductInteractionError, match="question_closed"):
        store.claim_notification(host.scope, host.actor, item["id"], 1)
    other = attention(host, store, ttl_seconds=1)
    host.now += 1_000_000_000
    with pytest.raises(ProductInteractionError, match="notification_expired"):
        store.claim_notification(host.scope, host.actor, other["id"], 1)
    with pytest.raises(ProductInteractionError, match="notification_expired"):
        store.resume_notification(host.scope, host.actor, other["id"], 1)


def test_temporary_notifications_do_not_leak_preferences_or_outbox_to_disk(tmp_path):
    host = Host()
    host.scope = replace(host.scope, temporary=True)
    path = tmp_path / "not-opened" / "interactions.sqlite3"
    store = host.store(path)
    enable_notifications(host, store)
    item = attention(host, store)
    assert store.get(host.scope, host.actor, item["id"])["status"] == "queued"
    assert not path.parent.exists()
    store.close()
    store = host.store(path)
    try:
        preferences = store.snapshot(host.scope, host.actor)["notification_preferences"]
        assert preferences["enabled"] is False
        assert store.snapshot(host.scope, host.actor)["interactions"] == []
        assert not path.parent.exists()
    finally:
        store.close()


def test_final_scope_recheck_rolls_back_answer_without_retaining_values(product):
    host, store = product
    question = create(host, store)
    original = store._assert_current
    calls = 0

    def revoke_during_call(scope, actor, write):
        nonlocal calls
        calls += 1
        original(scope, actor, write)
        if calls == 2:
            raise ProductInteractionError("product_host_scope_revoked")

    store._assert_current = revoke_during_call
    with pytest.raises(ProductInteractionError, match="host_scope_revoked"):
        store.answer_question(host.scope, host.actor, question["id"], 1,
                               {"style": {"text": "NO_CONSERVAR_SI_SE_REVOCA"}})
    store._assert_current = original
    assert store.get(host.scope, host.actor, question["id"])["status"] == "pending"
    assert not store._answers


def test_host_revocation_hook_erases_volatile_answers_without_new_client_access(product):
    host, store = product
    question = create(host, store)
    store.answer_question(host.scope, host.actor, question["id"], 1,
                           {"style": {"text": "BORRAR_AL_REVOCAR"}})
    store.forget_client(host.actor.client_id)
    assert not store._answers and not store._answer_owners
    with pytest.raises(ProductInteractionError, match="client_revoked"):
        store.consume_answers(host.scope, host.actor, question["id"], 2)
