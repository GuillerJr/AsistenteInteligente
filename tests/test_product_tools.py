"""Advertised product schemas must explain the forms the real domain accepts."""

import json
from contextlib import closing
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from jarvis_core.product_interactions import (
    InteractionActor,
    InteractionScope,
    ProductInteractionError,
    ProductInteractions,
)
from jarvis_core.product_tools import ProductTools
from jarvis_core.tools import ToolError, canonical_arguments

VALID = {
    "request_user_input": {"questions": [{
        "id": "format", "header": "Formato", "prompt": "¿Qué formato prefieres?",
        "options": [
            {"id": "compact", "label": "Compacto", "description": "Una vista breve."},
            {"id": "expanded", "label": "Amplio", "description": "Una vista detallada."},
        ], "sensitive": False,
    }]},
    "suggest_next_actions": {"expected_revision": 0, "suggestions": [
        {"id": "review", "label": "Revisar", "text": "Revisar el resultado del chat."},
        {"id": "continue", "label": "Continuar", "text": "Continuar con el siguiente paso."},
    ]},
    "publish_panel": {"expected_revision": 0, "panel": {
        "title": "Panel sintético", "layout": "grid",
        "theme": {"appearance": "system", "accent": "accent"},
        "widgets": [
            {"id": "scope", "type": "text", "title": "Chat", "source_id": "session",
             "symbol": "doc.text", "color": "secondary", "text": "Contenido literal"},
            {"id": "count", "type": "stat", "title": "Casos", "value": "2"},
            {"id": "notes", "type": "list", "title": "Notas", "items": ["Dato sintético"]},
            {"id": "todo", "type": "checklist", "title": "Tareas",
             "items": [{"text": "Comprobar recibo", "checked": False}]},
            {"id": "advance", "type": "progress", "title": "Avance", "value": 37.5},
            {"id": "notice", "type": "notice", "title": "Aviso", "text": "Sin efectos"},
        ],
    }},
}


@pytest.fixture(scope="module")
def advertised():
    tools = ProductTools(None)  # Schema inspection never executes a provider or effect.
    specs = {spec.name: spec for spec in tools.specs()}
    schemas = {name: json.loads(canonical_arguments(specs[name].input_schema)) for name in VALID}
    return tools, specs, schemas


def nested(value, path):
    for part in path:
        value = value[part]
    return value


@pytest.mark.parametrize("name", VALID)
def test_advertised_schema_accepts_complete_domain_shapes(advertised, name):
    tools, _, schemas = advertised
    Draft202012Validator.check_schema(schemas[name])
    Draft202012Validator(schemas[name]).validate(VALID[name])
    tools.validate(name, VALID[name])


@pytest.mark.parametrize("name,path,key", [
    ("suggest_next_actions", ("suggestions", 0), "id"),
    ("suggest_next_actions", ("suggestions", 0), "label"),
    ("suggest_next_actions", ("suggestions", 0), "text"),
    ("request_user_input", ("questions", 0), "id"),
    ("request_user_input", ("questions", 0), "header"),
    ("request_user_input", ("questions", 0), "prompt"),
    ("request_user_input", ("questions", 0), "options"),
    ("request_user_input", ("questions", 0, "options", 0), "description"),
    ("publish_panel", ("panel",), "title"),
    ("publish_panel", ("panel",), "layout"),
    ("publish_panel", ("panel",), "theme"),
    ("publish_panel", ("panel",), "widgets"),
    ("publish_panel", ("panel", "theme"), "accent"),
    ("publish_panel", ("panel", "widgets", 0), "id"),
    ("publish_panel", ("panel", "widgets", 3, "items", 0), "checked"),
])
def test_advertised_nested_objects_reject_missing_fields(advertised, name, path, key):
    tools, _, schemas = advertised
    candidate = deepcopy(VALID[name])
    nested(candidate, path).pop(key)
    with pytest.raises(ValidationError):
        Draft202012Validator(schemas[name]).validate(candidate)
    with pytest.raises(ToolError, match="invalid_product_arguments"):
        tools.validate(name, candidate)


@pytest.mark.parametrize("name,path,key,value", [
    ("suggest_next_actions", ("suggestions", 0), "prompt", "No es el campo text"),
    ("suggest_next_actions", ("suggestions", 0), "action", {"tool": "process_run"}),
    ("request_user_input", ("questions", 0), "allow_freeform", True),
    ("request_user_input", ("questions", 0, "options", 0), "recommended", True),
    ("publish_panel", ("panel",), "html", "<p>No se ejecuta</p>"),
    ("publish_panel", ("panel", "theme"), "background", "#ffffff"),
    ("publish_panel", ("panel", "widgets", 0), "javascript", "no ejecutar"),
    ("publish_panel", ("panel", "widgets", 3, "items", 0), "permission", True),
])
def test_advertised_nested_objects_reject_unknown_fields(advertised, name, path, key, value):
    tools, _, schemas = advertised
    candidate = deepcopy(VALID[name])
    nested(candidate, path)[key] = value
    with pytest.raises(ValidationError):
        Draft202012Validator(schemas[name]).validate(candidate)
    with pytest.raises(ToolError, match="invalid_product_arguments"):
        tools.validate(name, candidate)


@pytest.mark.parametrize("name,path,value", [
    ("suggest_next_actions", ("suggestions",),
     [VALID["suggest_next_actions"]["suggestions"][0]] * 4),
    ("suggest_next_actions", ("suggestions", 0, "id"), "not/a/local/id"),
    ("suggest_next_actions", ("suggestions", 0, "id"), "trailing_newline\n"),
    ("suggest_next_actions", ("suggestions", 0, "text"), "   "),
    ("suggest_next_actions", ("suggestions", 0, "text"), "literal\0NUL"),
    ("request_user_input", ("questions",), []),
    ("request_user_input", ("questions", 0, "sensitive"), True),
    ("request_user_input", ("questions", 0, "options"), [
        {"id": "only", "label": "Única", "description": "No ofrece dos decisiones."}]),
    ("request_user_input", ("questions", 0, "options", 0, "id"), "freeform"),
    ("publish_panel", ("panel", "theme", "accent"), "#ff00ff"),
    ("publish_panel", ("panel", "widgets", 0, "source_id"), "https://example.invalid"),
    ("publish_panel", ("panel", "widgets", 0, "type"), "html"),
    ("publish_panel", ("panel", "widgets", 1, "value"), 2),
    ("publish_panel", ("panel", "widgets", 2, "items"), [{"text": "Debe ser cadena"}]),
    ("publish_panel", ("panel", "widgets", 3, "items"), ["Debe ser objeto text/checked"]),
    ("publish_panel", ("panel", "widgets", 4, "value"), "45"),
    ("publish_panel", ("panel", "widgets", 4, "value"), 101),
])
def test_advertised_values_explain_supported_choices_and_widget_types(
        advertised, name, path, value):
    tools, _, schemas = advertised
    candidate = deepcopy(VALID[name])
    nested(candidate, path[:-1])[path[-1]] = value
    with pytest.raises(ValidationError):
        Draft202012Validator(schemas[name]).validate(candidate)
    with pytest.raises(ToolError, match="invalid_product_arguments"):
        tools.validate(name, candidate)


def test_descriptors_explain_suggestion_fields_question_choices_and_panel_data(advertised):
    _, specs, schemas = advertised
    suggestion = schemas["suggest_next_actions"]["properties"]["suggestions"]["items"]
    assert set(suggestion["required"]) == {"id", "label", "text"}
    assert suggestion["additionalProperties"] is False
    assert all(suggestion["properties"][key].get("description") for key in suggestion["required"])
    assert all(key in specs["suggest_next_actions"].description
               for key in ("id", "label", "text", "expected_revision", "borrador"))
    question = schemas["request_user_input"]["properties"]["questions"]["items"]
    assert set(question["required"]) == {"id", "header", "prompt", "options"}
    option = question["properties"]["options"]["items"]
    assert set(option["required"]) == {"id", "label", "description"}
    assert "freeform" in question["description"]
    assert "jarvis_request_user_input" in specs["request_user_input"].description
    panel = schemas["publish_panel"]["properties"]["panel"]
    assert set(panel["required"]) == {"title", "layout", "theme", "widgets"}
    assert all(key in specs["publish_panel"].description
               for key in ("source_id", "checklist", "text/checked", "expected_revision"))


def test_schema_valid_values_are_accepted_by_real_interaction_domain(advertised, tmp_path):
    tools, _, _ = advertised
    scope = InteractionScope("a" * 32, "b" * 32, "jarvis", "c" * 32, temporary=True)
    actor = InteractionActor("synthetic-owner")
    with closing(ProductInteractions(
        tmp_path / "interactions.sqlite3", assert_current=lambda *_: None,
        resolve_source=lambda *_: {"type": "sessions", "label": "Chat", "text": "Sólo propio"},
    )) as api:
        for name, candidate in VALID.items():
            tools.validate(name, candidate)
        assert api.create_question(scope, actor, VALID["request_user_input"]["questions"])[
            "status"] == "pending"
        assert api.publish_suggestions(scope, actor, **VALID["suggest_next_actions"])[
            "payload"]["execution"] == "draft_only"
        panel = api.set_panel(scope, actor, **VALID["publish_panel"])
        assert len(panel["payload"]["widgets"]) == 6
        assert panel["payload"]["rendering"] == "native_declarative"


@pytest.mark.parametrize("case", ["suggestion_ids", "option_labels", "widget_ids", "utf8_bytes"])
def test_schema_does_not_replace_domain_uniqueness_or_utf8_validation(advertised, tmp_path, case):
    tools, _, schemas = advertised
    scope = InteractionScope("a" * 32, "b" * 32, "jarvis", "c" * 32, temporary=True)
    actor = InteractionActor("synthetic-owner")
    with closing(ProductInteractions(
        tmp_path / "interactions.sqlite3", assert_current=lambda *_: None,
        resolve_source=lambda *_: {"type": "sessions", "label": "Chat", "text": "Sólo propio"},
    )) as api:
        if case == "suggestion_ids":
            name = "suggest_next_actions"
            candidate = deepcopy(VALID[name])
            candidate["suggestions"][1]["id"] = candidate["suggestions"][0]["id"]
            operation, error = api.publish_suggestions, "suggestions_invalid"
        elif case == "option_labels":
            name = "request_user_input"
            candidate = deepcopy(VALID[name])
            candidate["questions"][0]["options"][1]["label"] = " compacto "
            def operation(scope, actor, **value):
                return api.create_question(scope, actor, value["questions"])

            error = "options_invalid"
        elif case == "widget_ids":
            name = "publish_panel"
            candidate = deepcopy(VALID[name])
            candidate["panel"]["widgets"][1]["id"] = candidate["panel"]["widgets"][0]["id"]
            operation, error = api.set_panel, "widget_invalid"
        else:
            name = "suggest_next_actions"
            candidate = deepcopy(VALID[name])
            candidate["suggestions"][0]["label"] = "é" * 100  # 100 chars, 200 UTF-8 bytes.
            operation, error = api.publish_suggestions, "text_invalid"
        Draft202012Validator(schemas[name]).validate(candidate)
        tools.validate(name, candidate)
        with pytest.raises(ProductInteractionError, match=error):
            operation(scope, actor, **candidate)
