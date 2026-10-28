"""Bounded own RFC 6570 resource templates, read only through their MCP peer.

A template/URI is an external identifier, never filesystem or network authority.
Expansion is pure and fails before effects on capacity or unsupported input.
Catalog/generation and exact arguments are checked again before resources/read.
Primary sources: RFC 6570; MCP 2026-07-28 server/resources (2026-10-28).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import quote

from .mcp import McpError, _freeze, _json_data

_VAR = r"(?:[A-Za-z0-9_]|%[A-Fa-f0-9]{2})(?:(?:\.)?(?:[A-Za-z0-9_]|%[A-Fa-f0-9]{2}))*"
_SPEC = re.compile(r"(" + _VAR + r")(?:(\*)|:([1-9][0-9]{0,3}))?\Z")
_RESERVED = ":/?#[]@!$&'()*+,;="
_UNRESERVED = "-._~"
# prefix, separator, include variable names, empty named suffix, reserved values
_OPERATORS = {
    "": ("", ",", False, "", False),
    "+": ("", ",", False, "", True),
    "#": ("#", ",", False, "", True),
    ".": (".", ".", False, "", False),
    "/": ("/", "/", False, "", False),
    ";": (";", ";", True, "", False),
    "?": ("?", "&", True, "=", False),
    "&": ("&", "&", True, "=", False),
}


def _parse(template):
    if (
        not isinstance(template, str)
        or not 1 <= len(template.encode()) <= 8192
        or any(ord(c) < 33 or c in "\\\"'<>^`|" for c in template)
    ):
        raise McpError("invalid_resource_template")
    pieces, variables, position, expressions = [], [], 0, 0
    while position < len(template):
        start = template.find("{", position)
        if start < 0:
            start = len(template)
        literal = template[position:start]
        if "}" in literal or re.search(r"%(?![0-9A-Fa-f]{2})", literal):
            raise McpError("invalid_resource_template")
        pieces.append((None, literal))
        if start == len(template):
            break
        end = template.find("}", start + 1)
        if end < 0 or "{" in template[start + 1 : end]:
            raise McpError("invalid_resource_template")
        expression = template[start + 1 : end]
        if not expression:
            raise McpError("invalid_resource_template")
        operator = expression[0] if expression[0] in "+#./;?&" else ""
        specs = expression[len(operator) :].split(",")
        expressions += 1
        if len(specs) > 64 or expressions > 128:
            raise McpError("resource_template_capacity")
        parsed = []
        for value in specs:
            match = _SPEC.fullmatch(value)
            if match is None:
                raise McpError("invalid_resource_template")
            name, explode, prefix = match.groups()
            variables.append(name)
            parsed.append((name, bool(explode), int(prefix) if prefix else None))
        pieces.append((operator, tuple(parsed)))
        position = end + 1
    if len(set(variables)) > 64:
        raise McpError("resource_template_capacity")
    return pieces, tuple(dict.fromkeys(variables))


def template_variables(template):
    return _parse(template)[1]


def _encode(value, reserved):
    if not reserved:
        return quote(value, safe=_UNRESERVED)
    # Existing percent triplets survive only reserved/fragment expansion.
    parts = re.split(r"(%[A-Fa-f0-9]{2})", value)
    return "".join(
        part
        if re.fullmatch(r"%[A-Fa-f0-9]{2}", part)
        else quote(part, safe=_UNRESERVED + _RESERVED)
        for part in parts
    )


def _prefix(value, length):
    if length is None:
        return value
    # A percent-encoded character is atomic. Count a complete encoded UTF-8
    # scalar together, while leaving its original triplets unchanged.
    parts, position = [], 0
    while position < len(value) and len(parts) < length:
        if re.match(r"%[A-Fa-f0-9]{2}", value[position:]):
            byte = int(value[position + 1 : position + 3], 16)
            width = (
                2
                if 0xC2 <= byte <= 0xDF
                else 3
                if 0xE0 <= byte <= 0xEF
                else 4
                if 0xF0 <= byte <= 0xF4
                else 1
            )
            segment = value[position : position + 3 * width]
            if width > 1:
                try:
                    assert re.fullmatch(r"(?:%[A-Fa-f0-9]{2}){" + str(width) + "}", segment)
                    bytes(
                        int(segment[i + 1 : i + 3], 16) for i in range(0, len(segment), 3)
                    ).decode()
                except (ValueError, UnicodeError, AssertionError):
                    segment = value[position : position + 3]
        else:
            segment = value[position]
        parts.append(segment)
        position += len(segment)
    return "".join(parts)


def expand_template(template, arguments):
    pieces, variables = _parse(template)
    snapshot = _json_data(arguments)
    if not isinstance(snapshot, dict) or set(snapshot) - set(variables):
        raise McpError("invalid_resource_template_arguments")
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > 32768:
        raise McpError("resource_template_capacity")
    output = []
    for operator, content in pieces:
        if operator is None:
            output.append(quote(content, safe=_UNRESERVED + _RESERVED + "%"))
            continue
        first, separator, named, empty, reserved = _OPERATORS[operator]
        expanded = []
        for name, explode, prefix in content:
            value = snapshot.get(name)
            if value is None:
                continue

            def encoded(value, reserved=reserved):
                if not isinstance(value, str) or len(value.encode()) > 16384:
                    raise McpError("invalid_resource_template_arguments")
                return _encode(value, reserved)

            def named_value(key, val, named=named, empty=empty):
                return key + ("=" + val if val else empty) if named else val

            if isinstance(value, str):
                expanded.append(named_value(name, encoded(_prefix(value, prefix))))
            elif prefix is not None:
                raise McpError("invalid_resource_template_arguments")
            elif isinstance(value, list):
                items = [encoded(item) for item in value]
                if not items:
                    continue
                if explode:
                    expanded.extend(named_value(name, item) for item in items)
                else:
                    expanded.append(named_value(name, ",".join(items)))
            elif isinstance(value, dict):
                items = [
                    (encoded(key), encoded(item)) for key, item in value.items() if item is not None
                ]
                if not items:
                    continue
                if explode:
                    expanded.extend(
                        key + ("=" + item if item else empty if named else "")
                        for key, item in items
                    )
                else:
                    expanded.append(
                        named_value(name, ",".join(part for item in items for part in item))
                    )
            else:
                raise McpError("invalid_resource_template_arguments")
        if expanded:
            output.append(first + separator.join(expanded))
        if sum(len(part.encode()) for part in output) > 32768:
            raise McpError("resource_template_capacity")
    uri = "".join(output)
    if len(uri.encode()) > 32768:
        raise McpError("resource_template_capacity")
    return uri


class McpResourceTemplates:
    def __init__(self, client):
        self.client = client
        self.templates = {}
        self.generation = None

    def assert_current(self):
        if (
            not self.client.connected
            or self.generation is None
            or self.client._catalog_generations["resources"] != self.generation
        ):
            raise McpError("catalog_changed")

    async def list_templates(self):
        self.client._capability("resources")
        generation = self.client._catalog_generations["resources"]
        cursor, seen, templates, size = None, set(), {}, 0
        for _ in range(self.client.max_pages):
            page = await self.client._request(
                "resources/templates/list", {} if cursor is None else {"cursor": cursor}
            )
            entries = page.get("resourceTemplates")
            if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
                raise McpError("invalid_resource_template_catalog")
            size += len(json.dumps(page).encode())
            for entry in entries:
                uri, name = entry.get("uriTemplate"), entry.get("name")
                if (
                    not isinstance(name, str)
                    or not 1 <= len(name.encode()) <= 512
                    or not isinstance(uri, str)
                    or uri in templates
                ):
                    raise McpError("invalid_resource_template_catalog")
                names = template_variables(uri)
                templates[uri] = _freeze({**entry, "variables": list(names)})
            if len(templates) > self.client.max_items or size > self.client.max_line_bytes * 2:
                raise McpError("catalog_limit")
            cursor = page.get("nextCursor")
            if cursor is None:
                if self.client._catalog_generations["resources"] != generation:
                    raise McpError("catalog_changed")
                self.templates, self.generation = templates, generation
                self.assert_current()
                return tuple(templates.values())
            if not isinstance(cursor, str) or not cursor or len(cursor) > 1024 or cursor in seen:
                raise McpError("invalid_pagination")
            seen.add(cursor)
        raise McpError("pagination_limit")

    def expand(self, template_uri, arguments):
        self.assert_current()
        if template_uri not in self.templates:
            raise McpError("resource_template_not_listed")
        uri = expand_template(template_uri, arguments)
        if not uri:
            raise McpError("invalid_resource_template_arguments")
        return uri

    async def read(self, template_uri, arguments, *, dispatch_guard=None):
        if dispatch_guard is None:
            raise McpError("approval_required")
        snapshot = _freeze(_json_data(arguments))
        uri = self.expand(template_uri, snapshot)
        generation = self.generation
        entry = self.templates[template_uri]

        def before_dispatch():
            self.assert_current()
            if (
                self.generation != generation
                or self.templates.get(template_uri) is not entry
                or self.expand(template_uri, snapshot) != uri
            ):
                raise McpError("catalog_changed")
            dispatch_guard()

        result = await self.client._request(
            "resources/read", {"uri": uri}, effect=True, before_dispatch=before_dispatch
        )
        if not isinstance(result, Mapping) or not isinstance(result.get("contents"), list):
            raise McpError("invalid_resource_result", effect_status="unknown")
        return _freeze(_json_data(result))
