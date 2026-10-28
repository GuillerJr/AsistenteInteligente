"""Offline standards validator workers: own schemas/data, no owner paths."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

from jarvis_core import mcp_schema
from jarvis_core.mcp import McpError, _schema, _validate

SCHEMA = {"type": "object", "$defs": {"value": {"anyOf": [
    {"type": "string", "pattern": "^[A-Z]{2}[0-9]{2}$"}, {"type": "null"}]}},
    "properties": {"value": {"$ref": "#/$defs/value"}}, "required": ["value"],
    "additionalProperties": False}


def test_local_defs_nullable_anyof_and_pattern_use_offline_worker():
    _schema(SCHEMA)
    _validate({"value": "AB42"}, SCHEMA)
    _validate({"value": None}, SCHEMA)
    for value in ({"value": "bad"}, {"value": 12}, {}, {"value": None, "extra": True}):
        with pytest.raises(McpError, match="invalid_tool_arguments"):
            _validate(value, SCHEMA)


@pytest.mark.parametrize("ref", ["https://example.com/schema", "file:///private/tmp/note", "../x"])
def test_remote_file_or_relative_schema_retrieval_is_rejected(ref):
    with pytest.raises(McpError, match="unsupported_tool_schema"):
        _schema({"type": "object", "$ref": ref})


def test_draft7_oneof_conditional_constraints_and_unevaluated_2020():
    schema = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
        "properties": {"kind": {"enum": ["a", "b"]}, "count": {"type": "integer"}},
        "required": ["kind"], "if": {"properties": {"kind": {"const": "a"}}},
        "then": {"required": ["count"]}, "else": {"not": {"required": ["count"]}}}
    _schema(schema)
    _validate({"kind": "a", "count": 2}, schema)
    _validate({"kind": "b"}, schema)
    with pytest.raises(McpError, match="invalid_tool_arguments"):
        _validate({"kind": "b", "count": 2}, schema)
    modern = {"type": "object", "allOf": [{"properties": {"x": {"type": ["string", "null"]}}}],
              "unevaluatedProperties": False}
    _schema(modern)
    _validate({"x": None}, modern)
    with pytest.raises(McpError, match="invalid_tool_arguments"):
        _validate({"x": None, "other": 1}, modern)


def test_cyclic_local_ref_and_pathological_pattern_fail_with_finite_worker_custody():
    _schema({"type": "object", "$ref": "#"})
    with pytest.raises(McpError, match="unsupported_tool_schema"):
        _validate({}, {"type": "object", "$ref": "#"})
    schema = {"type": "object", "properties": {"x": {"type": "string", "pattern": "^(a+)+$"}}}
    started = time.monotonic()
    with pytest.raises(McpError, match="schema_validation_budget_exceeded"):
        _validate({"x": "a" * 5000 + "!"}, schema)
    assert time.monotonic() - started < 6


def test_input_is_data_never_interpolated_into_worker_source(tmp_path):
    marker = tmp_path / "must-not-exist"
    literal = f"__import__('pathlib').Path({json.dumps(str(marker))}).touch()"
    schema = {"type": "object", "properties": {"x": {"anyOf": [{"type": "string"}]}}}
    _validate({"x": literal}, schema)
    assert not marker.exists()


def test_base_interpreter_loads_only_selected_dependency_roots(monkeypatch):
    base = getattr(sys, "_base_executable", None)
    if (not base or base == sys.executable or not Path(base).is_file()
            or not os.access(base, os.X_OK)):
        pytest.skip("El intérprete actual no tiene un ejecutable base externo distinto")
    roots = mcp_schema._reviewed_dependency_roots()
    assert roots and all(Path(root).is_absolute() for root in roots)
    monkeypatch.setattr(mcp_schema.sys, "executable", base)
    mcp_schema.validate(SCHEMA, {"value": "AB42"})
    with pytest.raises(mcp_schema.SchemaError, match="invalid_tool_arguments"):
        mcp_schema.validate(SCHEMA, {"value": 42})


@pytest.mark.parametrize("schema,value", [
    ({"type": "object", "properties": {"$ref": {"type": "string"}},
      "required": ["$ref"], "additionalProperties": False}, {"$ref": "private label"}),
    ({"enum": [{"$ref": "private label", "$id": "not-a-schema"}]},
     {"$ref": "private label", "$id": "not-a-schema"}),
    ({"const": {"$ref": {"literal": True}}, "examples": [{"$ref": 7}]},
     {"$ref": {"literal": True}}),
])
def test_reference_named_instance_data_is_not_a_schema_reference(schema, value):
    mcp_schema.check_schema(schema)
    mcp_schema.validate(schema, value)


@pytest.mark.parametrize("container", [
    {"properties": {"value": {"$ref": "https://example.com/unavailable"}}},
    {"allOf": [{"$ref": "https://example.com/unavailable"}]},
    {"if": {"$ref": "https://example.com/unavailable"}},
    {"$defs": {"value": {"$ref": "https://example.com/unavailable"}}},
    {"dependencies": {"value": {"$ref": "https://example.com/unavailable"}}},
])
def test_actual_schema_locations_still_refuse_external_references(container):
    with pytest.raises(mcp_schema.SchemaError, match="unsupported_tool_schema"):
        mcp_schema.check_schema(container)


def test_data_identifiers_cannot_authorize_external_schema_references():
    schema = {"$ref": "https://example.com/unavailable", "examples": [
        {"$id": "https://example.com/unavailable"}]}
    with pytest.raises(mcp_schema.SchemaError, match="unsupported_tool_schema"):
        mcp_schema.check_schema(schema)
