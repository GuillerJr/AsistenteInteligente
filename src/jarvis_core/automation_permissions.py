"""Explicit remembered tool decisions, outside portable profile backups.

Rules remain bound to client key, physical profile, agent and workspace. Native
actions may vary content only within the same exact app/window or tab/origin and
action class. Other tools remember only a digest of the exact arguments.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from jarvis_core.client_identity import profile_binding
from jarvis_core.private_json import PrivateJsonDocument
from jarvis_core.tools import ToolError, canonical_arguments


def permission_selector(tool, args):
    data = json.loads(canonical_arguments(args))
    if tool == "desktop_action":
        # Same physical process/window and operation kind; refs are short-lived.
        keys = {"target", "window_ref", "action", "key"}
        data = {k: v for k, v in data.items() if k in keys}
    elif tool == "browser_action":
        keys = {"tab_id", "expected_origin", "action", "key"}
        data = {k: v for k, v in data.items() if k in keys}
        if not data.get("expected_origin"):
            raise ToolError("permission_exact_origin_required")
    digest = hashlib.sha256(
        canonical_arguments({"tool": tool, "selector": data}).encode()
    ).hexdigest()
    return digest


class AutomationPermissions:
    def __init__(self, profile: Path):
        self.binding = profile_binding(profile)
        self.volatile = []
        self.session_revision = 1
        self.document = PrivateJsonDocument(
            profile.with_name(profile.name + ".automation"),
            "permissions.json",
            initial=lambda: {
                "version": 1,
                "profile_binding": self.binding,
                "revision": 1,
                "rules": [],
                "search_template": None,
            },
            validate=self._validate,
            max_bytes=131072,
        )

    def _validate(self, value):
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "profile_binding", "revision", "rules", "search_template"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or value["profile_binding"] != self.binding
            or type(value["revision"]) is not int
            or value["revision"] < 1
            or not isinstance(value["rules"], list)
            or len(value["rules"]) > 128
            or (
                value["search_template"] is not None
                and (
                    not isinstance(value["search_template"], str)
                    or len(value["search_template"].encode()) > 2048
                )
            )
        ):
            raise ToolError("automation_permissions_invalid")
        ids = set()
        for row in value["rules"]:
            if (
                not isinstance(row, dict)
                or set(row)
                != {
                    "id",
                    "client_id",
                    "agent_id",
                    "workspace_id",
                    "tool",
                    "selector",
                    "max_input_bytes",
                    "destination",
                }
                or any(not isinstance(row[k], str) for k in row if k != "max_input_bytes")
                or re.fullmatch(r"[a-f0-9]{32}", row["id"]) is None
                or row["id"] in ids
                or any(
                    re.fullmatch(r"[a-f0-9]{64}", row[k]) is None
                    for k in ("client_id", "workspace_id", "selector")
                )
                or re.fullmatch(r"[a-z][a-z0-9_]{2,63}", row["tool"]) is None
                or type(row["max_input_bytes"]) is not int
                or not 0 <= row["max_input_bytes"] <= 131072
                or len(row["destination"].encode()) > 512
            ):
                raise ToolError("automation_permissions_invalid")
            ids.add(row["id"])

    @staticmethod
    def _input_size(args):
        return sum(
            len(v.encode())
            for k, v in args.items()
            if k in {"text", "value", "expected_value"} and isinstance(v, str)
        )

    def snapshot(self):
        data = self.document.read()
        return {
            **data,
            "session_rules": len(self.volatile),
            "volatile_rules": json.loads(json.dumps(self.volatile)),
            "session_revision": self.session_revision,
            "permissions_portable": False,
        }

    def remember(
        self, *, tool, arguments, client_id, agent_id, workspace_id, session_id, temporary, mode
    ):
        if mode not in {"session", "always"} or (temporary and mode == "always"):
            raise ToolError("permission_mode_invalid")
        target = arguments.get("target")
        destination = (
            arguments.get("expected_origin")
            or (target.get("bundle_id") if isinstance(target, Mapping) else None)
            or arguments.get("path")
            or arguments.get("bundle_id")
            or tool
        )
        if tool == "desktop_action":
            destination += " · " + str(arguments.get("window_ref", ""))[:12]
        elif tool == "browser_action":
            destination += " · " + str(arguments.get("tab_id", ""))[:12]
        row = {
            "id": uuid4().hex,
            "client_id": client_id,
            "agent_id": agent_id,
            "workspace_id": workspace_id,
            "tool": tool,
            "selector": permission_selector(tool, arguments),
            "destination": str(destination).encode()[:512].decode("utf-8", errors="ignore"),
            "max_input_bytes": (
                4096
                if tool in {"desktop_action", "browser_action"}
                else self._input_size(arguments)
            ),
        }
        if self._input_size(arguments) > row["max_input_bytes"]:
            raise ToolError("permission_input_limit")
        if mode == "session":
            if len(self.volatile) >= 128:
                raise ToolError("permission_capacity")
            self.volatile.append({**row, "session_id": session_id, "temporary": temporary})
            self.session_revision += 1
        else:

            def change(data):
                if len(data["rules"]) >= 128:
                    raise ToolError("permission_capacity")
                data["rules"].append(row)
                data["revision"] += 1
                return data

            self.document.update(change)
        return row["id"]

    def match(self, *, tool, arguments, client_id, agent_id, workspace_id, session_id, temporary):
        digest = permission_selector(tool, arguments)
        candidates = [
            x
            for x in self.volatile
            if x["session_id"] == session_id and x["temporary"] == temporary
        ]
        if not temporary:
            candidates += self.document.read()["rules"]
        return next(
            (
                x["id"]
                for x in candidates
                if (x["tool"], x["selector"], x["client_id"], x["agent_id"], x["workspace_id"])
                == (tool, digest, client_id, agent_id, workspace_id)
                and self._input_size(arguments) <= x["max_input_bytes"]
            ),
            None,
        )

    def revoke(self, identity, expected_revision, expected_session_revision=None):
        if type(expected_revision) is not int:
            raise ToolError("permission_revision_changed")
        if any(x["id"] == identity for x in self.volatile):
            if (
                self.document.read()["revision"] != expected_revision
                or type(expected_session_revision) is not int
                or expected_session_revision != self.session_revision
            ):
                raise ToolError("permission_revision_changed")
            self.volatile = [x for x in self.volatile if x["id"] != identity]
            self.session_revision += 1
            return

        def change(data):
            if data["revision"] != expected_revision:
                raise ToolError("permission_revision_changed")
            if not any(row["id"] == identity for row in data["rules"]):
                raise ToolError("permission_not_found")
            data["rules"] = [x for x in data["rules"] if x["id"] != identity]
            data["revision"] += 1
            return data

        self.document.update(change)

    def clear_session(self, session_id):
        self.session_revision += 1
        self.volatile = [x for x in self.volatile if x["session_id"] != session_id]

    def set_search(self, template, expected_revision):
        if (type(expected_revision) is not int
                or self.document.read()["revision"] != expected_revision):
            raise ToolError("permission_revision_changed")
        self.volatile = [x for x in self.volatile if x["tool"] != "browser_search"]
        self.session_revision += 1

        def change(data):
            if data["revision"] != expected_revision:
                raise ToolError("permission_revision_changed")
            data["search_template"] = template
            data["rules"] = [x for x in data["rules"] if x["tool"] != "browser_search"]
            data["revision"] += 1
            return data

        self.document.update(change)
