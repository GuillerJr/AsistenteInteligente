"""Scoped product interaction data; no conversation history or effect authority.

The host supplies a live ACL/scope check on every call. Answers stay in RAM and
are never written to SQLite, receipts, logs, or conversation messages by this
module. Sensitive input is rejected: credentials belong to a separate owner
surface. An ordinary answer is data and cannot grant a tool approval.

SQLite contains only our own bounded UI metadata. A dedicated StateStore supplies
private file identity checks, locking, lazy disk access and separate :memory:
storage for temporary sessions; its conversation tables are not used here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from jarvis_core.product_interactions_notifications import ProductNotifications
from jarvis_core.state import StateStore


class ProductInteractionError(ValueError):
    """Stable public codes contain no supplied content, paths or answer values."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code):
    raise ProductInteractionError("product_" + code)


def _text(value, maximum=1024, *, empty=False):
    try:
        valid = (type(value) is str and "\0" not in value
                 and (empty or bool(value.strip())) and len(value.encode()) <= maximum)
    except UnicodeError:
        valid = False
    if not valid:
        _fail("text_invalid")
    return value


def _identity(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None:
        _fail("identity_invalid")
    return value


def _revision(value):
    if type(value) is not int or not 0 <= value < 2**52:
        _fail("revision_invalid")
    return value


def _encoded(value):
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
        if len(data.encode()) > 65536:
            _fail("payload_limit")
        return data
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("payload_invalid")


@dataclass(frozen=True)
class InteractionScope:
    session_id: str
    run_id: str
    agent_id: str
    workspace_id: str
    temporary: bool = False
    generation: int = 0

    def __post_init__(self):
        for value in (self.session_id, self.run_id, self.agent_id, self.workspace_id):
            _identity(value)
        if type(self.temporary) is not bool:
            _fail("scope_invalid")
        _revision(self.generation)


@dataclass(frozen=True)
class InteractionActor:
    client_id: str
    role: str = "owner"

    def __post_init__(self):
        _identity(self.client_id)
        if type(self.role) is not str or self.role not in {"owner", "read"}:
            _fail("role_invalid")


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS product_format (version INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS product_records ("
    "id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, session_key TEXT NOT NULL, "
    "scope_json TEXT NOT NULL, client_id TEXT NOT NULL, kind TEXT NOT NULL, "
    "revision INTEGER NOT NULL, status TEXT NOT NULL, expires_ns INTEGER NOT NULL, "
    "payload_json TEXT NOT NULL, created_ns INTEGER NOT NULL, updated_ns INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS product_scope ON product_records(scope_key,client_id,kind)",
    "CREATE INDEX IF NOT EXISTS product_session ON product_records(session_key,client_id,kind)",
)
_SYMBOLS = frozenset({
    "text.alignleft", "list.bullet", "checkmark.circle", "chart.bar", "clock",
    "info.circle", "exclamationmark.triangle", "sparkles", "square.grid.2x2",
    "doc.text", "questionmark.circle", "bell", "circle",
})
_COLORS = frozenset({"primary", "secondary", "accent", "success", "warning", "error"})
_TERMINAL_PROGRESS = frozenset({"completed", "failed", "cancelled"})


class ProductInteractions(ProductNotifications):
    """Host-only callable domain. Neither scope nor actor is an RPC credential.

    ``assert_current(scope, actor, write)`` MUST independently authenticate the
    admitted client and actual role, verify the selected session's agent and
    workspace, the run relationship and generation, and reject stale/revoked
    scopes. Reads may inspect a completed run; writes need host-selected policy.
    ``resolve_source`` only returns explicitly selected internal source metadata
    from this exact scope. It must never list or search other conversations.
    """

    def __init__(self, path: str | Path, *, assert_current: Callable,
                 resolve_source: Callable | None = None, clock: Callable = time.time_ns):
        if not callable(assert_current) or (resolve_source is not None
                                            and not callable(resolve_source)):
            _fail("guard_required")
        self._state = StateStore(path)
        self._assert_current = assert_current
        self._resolve_source = resolve_source
        self._clock = clock
        self._ready = set()
        self._answers = {}
        self._answer_owners = {}
        self._revoked_clients = set()

    def close(self):
        self._answers.clear()
        self._answer_owners.clear()
        self._state.close()

    def forget_client(self, client_id):
        """Trusted host hook after client revocation, never an RPC/tool endpoint.

        The host authority remains the durable revocation authority. This local
        hook immediately drops volatile values even if no caller polls again.
        """
        _identity(client_id)
        self._revoked_clients.add(client_id)
        for identity, (_, owner) in tuple(self._answer_owners.items()):
            if owner == client_id:
                self._answers.pop(identity, None)
                self._answer_owners.pop(identity, None)

    def _discard_answer(self, identity):
        self._answer_owners.pop(identity, None)
        return self._answers.pop(identity, None)

    def _guard(self, scope, actor, write=False):
        if type(scope) is not InteractionScope or type(actor) is not InteractionActor:
            _fail("scope_invalid")
        if actor.client_id in self._revoked_clients:
            _fail("client_revoked")
        if write and actor.role != "owner":
            _fail("owner_required")
        self._assert_current(scope, actor, write)

    def _now(self):
        value = self._clock()
        if type(value) is not int or not 0 <= value < 2**63:
            _fail("clock_invalid")
        return value

    @staticmethod
    def _scope_key(scope):
        return hashlib.sha256(_encoded(asdict(scope)).encode()).hexdigest()

    @staticmethod
    def _session_key(scope):
        data = {key: value for key, value in asdict(scope).items()
                if key not in {"run_id", "generation"}}
        return hashlib.sha256(_encoded(data).encode()).hexdigest()

    def _expiry(self, ttl_seconds, maximum=86400):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= maximum:
            _fail("expiry_invalid")
        return self._now() + ttl_seconds * 1_000_000_000

    @contextmanager
    def _tx(self, scope, actor, *, write=False):
        self._guard(scope, actor, write)
        with self._state._transaction(scope.temporary) as connection:
            marker = id(connection)
            if marker not in self._ready:
                for statement in _SCHEMA:
                    connection.execute(statement)
                versions = connection.execute("SELECT version FROM product_format").fetchall()
                if not versions:
                    connection.execute("INSERT INTO product_format VALUES (1)")
                elif len(versions) != 1 or versions[0][0] != 1:
                    _fail("format_invalid")
                # Restart cannot recover volatile answers or retry an uncertain
                # native delivery. Preserve its fact and require owner attention.
                connection.execute(
                    "UPDATE product_records SET status='interrupted',revision=revision+1 "
                    "WHERE kind='question' AND status='pending'")
                connection.execute(
                    "UPDATE product_records SET status='unknown',revision=revision+1 "
                    "WHERE kind='notification' AND status='dispatching'")
            if write and connection.execute(
                "SELECT 1 FROM product_records WHERE scope_key=? AND client_id=? "
                "AND kind='scope_revocation'",
                (self._scope_key(scope), actor.client_id),
            ).fetchone() is not None:
                _fail("scope_revoked")
            yield connection
            self._guard(scope, actor, write)
        self._ready.add(marker)

    def _put(self, connection, scope, actor, kind, payload, *, identity=None,
             status="active", expires_ns=0):
        if connection.execute("SELECT count(*) FROM product_records").fetchone()[0] >= 10000:
            _fail("capacity")
        if connection.execute(
            "SELECT count(*) FROM product_records WHERE scope_key=? AND client_id=?",
            (self._scope_key(scope), actor.client_id),
        ).fetchone()[0] >= 512:
            _fail("scope_capacity")
        now = self._now()
        identity = uuid4().hex if identity is None else identity
        connection.execute(
            "INSERT INTO product_records VALUES (?,?,?,?,?,?,1,?,?,?,?,?)",
            (identity, self._scope_key(scope), self._session_key(scope),
             _encoded(asdict(scope)), actor.client_id, kind, status,
             expires_ns, _encoded(payload), now, now),
        )
        return self._row(connection, scope, actor, identity, kind)

    def _row(self, connection, scope, actor, identity, kind=None, *, session=False):
        _identity(identity)
        key = "session_key" if session else "scope_key"
        binding = self._session_key(scope) if session else self._scope_key(scope)
        row = connection.execute(
            f"SELECT * FROM product_records WHERE id=? AND {key}=? AND client_id=?",
            (identity, binding, actor.client_id),
        ).fetchone()
        if row is None or (kind is not None and row["kind"] != kind):
            _fail("not_found")
        return dict(row)

    @staticmethod
    def _cas(row, expected_revision):
        _revision(expected_revision)
        if row["revision"] != expected_revision:
            _fail("revision_changed")

    def _change(self, connection, row, *, payload=None, status=None, expires_ns=None):
        payload_json = row["payload_json"] if payload is None else _encoded(payload)
        status = row["status"] if status is None else status
        expires_ns = row["expires_ns"] if expires_ns is None else expires_ns
        changed = connection.execute(
            "UPDATE product_records SET revision=revision+1,status=?,payload_json=?,"
            "expires_ns=?,updated_ns=? WHERE id=? AND revision=?",
            (status, payload_json, expires_ns, self._now(), row["id"], row["revision"]),
        ).rowcount
        if changed != 1:
            _fail("revision_changed")
        return dict(row, revision=row["revision"] + 1, status=status,
                    payload_json=payload_json, expires_ns=expires_ns)

    def _view(self, row, scope=None, actor=None):
        view = {key: row[key] for key in ("id", "kind", "revision", "status", "expires_ns")}
        if row["expires_ns"] and row["expires_ns"] <= self._now():
            if row["status"] in {"pending", "active", "queued"}:
                view["status"] = "expired"
        view["payload"] = json.loads(row["payload_json"])
        if row["kind"] == "panel" and scope is not None and actor is not None:
            for widget in view["payload"]["widgets"]:
                if "source_id" in widget:
                    widget["source"] = self._source(scope, actor, widget["source_id"])
        if row["kind"] == "question":
            view["answers_available"] = row["id"] in self._answers
        return view

    def snapshot(self, scope, actor):
        with self._tx(scope, actor) as connection:
            rows = connection.execute(
                "SELECT * FROM product_records WHERE scope_key=? AND client_id=? "
                "AND kind!='notification_preferences' ORDER BY created_ns,id LIMIT 512",
                (self._scope_key(scope), actor.client_id),
            ).fetchall()
            return {"scope": asdict(scope),
                    "interactions": [self._view(row, scope, actor) for row in rows],
                    "notification_preferences": self._preferences(connection, scope, actor)}

    def get(self, scope, actor, identity):
        with self._tx(scope, actor) as connection:
            return self._view(self._row(connection, scope, actor, identity), scope, actor)

    @staticmethod
    def _questions(questions):
        if type(questions) is not list or not 1 <= len(questions) <= 3:
            _fail("questions_invalid")
        output, seen = [], set()
        for question in questions:
            if (type(question) is not dict
                    or set(question) - {"id", "header", "prompt", "options", "sensitive"}
                    or {"id", "header", "prompt", "options"} - set(question)):
                _fail("questions_invalid")
            if type(question.get("sensitive", False)) is not bool:
                _fail("questions_invalid")
            if question.get("sensitive", False):
                _fail("sensitive_input_requires_owner_surface")
            identity = _identity(question["id"])
            if identity in seen:
                _fail("questions_invalid")
            seen.add(identity)
            options = question["options"]
            if type(options) is not list or not 2 <= len(options) <= 3:
                _fail("options_invalid")
            normalized, option_ids, labels = [], set(), set()
            for option in options:
                if (type(option) is not dict or set(option) != {"id", "label", "description"}
                        or option["id"] == "freeform"):
                    _fail("options_invalid")
                option_id = _identity(option["id"])
                label = _text(option["label"], 160)
                if option_id in option_ids or label.casefold().strip() in labels:
                    _fail("options_invalid")
                option_ids.add(option_id)
                labels.add(label.casefold().strip())
                normalized.append({"id": option_id, "label": label,
                                   "description": _text(option["description"], 512)})
            output.append({"id": identity, "header": _text(question["header"], 48),
                           "prompt": _text(question["prompt"], 2048),
                           "options": normalized, "allow_freeform": True})
        return output

    def create_question(self, scope, actor, questions, *, ttl_seconds=900):
        payload = {"questions": self._questions(questions), "grants_tool_approval": False}
        expiry = self._expiry(ttl_seconds, 3600)
        with self._tx(scope, actor, write=True) as connection:
            return self._view(self._put(connection, scope, actor, "question", payload,
                                        status="pending", expires_ns=expiry))

    def _pending_question(self, connection, scope, actor, identity, expected_revision):
        row = self._row(connection, scope, actor, identity, "question")
        self._cas(row, expected_revision)
        if row["expires_ns"] <= self._now():
            self._discard_answer(identity)
            _fail("question_expired")
        if row["status"] != "pending":
            _fail("question_closed")
        return row

    def answer_question(self, scope, actor, identity, expected_revision, answers):
        normalized = {}
        with self._tx(scope, actor, write=True) as connection:
            row = self._pending_question(connection, scope, actor, identity, expected_revision)
            questions = json.loads(row["payload_json"])["questions"]
            if type(answers) is not dict or set(answers) != {q["id"] for q in questions}:
                _fail("answers_invalid")
            for question in questions:
                answer = answers[question["id"]]
                if type(answer) is not dict:
                    _fail("answers_invalid")
                if set(answer) == {"option_id"}:
                    if (type(answer["option_id"]) is not str or answer["option_id"]
                            not in {x["id"] for x in question["options"]}):
                        _fail("answers_invalid")
                    normalized[question["id"]] = {"option_id": answer["option_id"]}
                elif set(answer) == {"text"}:
                    normalized[question["id"]] = {"text": _text(answer["text"], 4096)}
                else:
                    _fail("answers_invalid")
            # Metadata records completion only; no answer or answer digest is durable.
            row = self._change(connection, row, status="answered")
        self._answers[identity] = normalized
        self._answer_owners[identity] = (self._scope_key(scope), actor.client_id)
        return {"id": identity, "revision": row["revision"], "status": "answered",
                "grants_tool_approval": False}

    def consume_answers(self, scope, actor, identity, expected_revision):
        with self._tx(scope, actor, write=True) as connection:
            row = self._row(connection, scope, actor, identity, "question")
            self._cas(row, expected_revision)
            if row["expires_ns"] <= self._now():
                self._discard_answer(identity)
                _fail("question_expired")
            if row["status"] != "answered" or identity not in self._answers:
                _fail("answers_unavailable")
            row = self._change(connection, row, status="consumed")
        return {"id": identity, "revision": row["revision"], "status": "consumed",
                "answers": self._discard_answer(identity), "grants_tool_approval": False}

    async def wait_answers(self, scope, actor, identity):
        """Await the bound owner response; cancellation closes the question.

        The host must keep this return value out of action receipts/history when
        routing it to the model. No authority is consulted or granted here.
        """
        try:
            while True:
                row = self.get(scope, actor, identity)
                if row["status"] == "answered":
                    return self.consume_answers(scope, actor, identity, row["revision"])
                if row["status"] != "pending":
                    _fail("question_closed")
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            try:
                row = self.get(scope, actor, identity)
                if row["status"] in {"pending", "answered"}:
                    self.cancel(scope, actor, identity, row["revision"])
            except (ProductInteractionError, ValueError):
                pass
            self._discard_answer(identity)
            raise
        except ProductInteractionError:
            self._discard_answer(identity)
            raise

    def cancel(self, scope, actor, identity, expected_revision):
        with self._tx(scope, actor, write=True) as connection:
            row = self._row(connection, scope, actor, identity)
            self._cas(row, expected_revision)
            if row["kind"] not in {"question", "progress", "presence", "suggestions", "panel"}:
                _fail("cancel_invalid")
            if row["status"] in {"cancelled", "consumed", "completed", "failed"}:
                _fail("interaction_closed")
            row = self._change(connection, row, status="cancelled")
        self._discard_answer(identity)
        return self._view(row)

    def _singleton(self, connection, scope, actor, kind):
        row = connection.execute(
            "SELECT * FROM product_records WHERE scope_key=? AND client_id=? AND kind=?",
            (self._scope_key(scope), actor.client_id, kind),
        ).fetchone()
        return None if row is None else dict(row)

    def _publish(self, scope, actor, kind, payload, expected_revision, *, status="active",
                 expires_ns=0):
        _revision(expected_revision)
        with self._tx(scope, actor, write=True) as connection:
            row = self._singleton(connection, scope, actor, kind)
            if row is None:
                if expected_revision != 0:
                    _fail("revision_changed")
                row = self._put(connection, scope, actor, kind, payload, status=status,
                                expires_ns=expires_ns)
            else:
                self._cas(row, expected_revision)
                if kind == "progress" and row["status"] in _TERMINAL_PROGRESS:
                    _fail("progress_closed")
                row = self._change(connection, row, payload=payload, status=status,
                                   expires_ns=expires_ns)
            return self._view(row, scope, actor)

    def publish_progress(self, scope, actor, *, expected_revision, label, value=None,
                         status="running"):
        if type(status) is not str or status not in {"running", "waiting", *_TERMINAL_PROGRESS}:
            _fail("progress_invalid")
        if value is not None and (type(value) not in {int, float} or not math.isfinite(value)
                                  or not 0 <= value <= 100):
            _fail("progress_invalid")
        return self._publish(scope, actor, "progress", {"label": _text(label, 512),
                             "value": value}, expected_revision, status=status)

    def publish_presence(self, scope, actor, *, expected_revision, state, ttl_seconds=60):
        if type(state) is not str or state not in {"working", "waiting_user", "idle"}:
            _fail("presence_invalid")
        return self._publish(scope, actor, "presence", {"state": state}, expected_revision,
                             expires_ns=self._expiry(ttl_seconds, 300))

    def publish_suggestions(self, scope, actor, *, expected_revision, suggestions,
                            ttl_seconds=300):
        if type(suggestions) is not list or len(suggestions) > 3:
            _fail("suggestions_invalid")
        normalized, seen = [], set()
        for item in suggestions:
            if type(item) is not dict or set(item) != {"id", "label", "text"}:
                _fail("suggestions_invalid")
            identity = _identity(item["id"])
            if identity in seen:
                _fail("suggestions_invalid")
            seen.add(identity)
            normalized.append({"id": identity, "label": _text(item["label"], 160),
                               "text": _text(item["text"], 2048)})
        return self._publish(scope, actor, "suggestions",
                             {"suggestions": normalized, "execution": "draft_only"},
                             expected_revision, expires_ns=self._expiry(ttl_seconds, 3600))

    def select_suggestion(self, scope, actor, identity, expected_revision, suggestion_id):
        with self._tx(scope, actor, write=True) as connection:
            row = self._row(connection, scope, actor, identity, "suggestions")
            self._cas(row, expected_revision)
            if self._view(row)["status"] != "active":
                _fail("suggestions_closed")
            item = next((x for x in json.loads(row["payload_json"])["suggestions"]
                         if x["id"] == suggestion_id), None)
            if item is None:
                _fail("suggestion_not_found")
            return {"draft_text": item["text"], "execution": "draft_only",
                    "grants_tool_approval": False}

    def _source(self, scope, actor, source_id):
        if self._resolve_source is None:
            _fail("source_not_selected")
        source = self._resolve_source(scope, actor, source_id)
        if (type(source) is not dict or set(source) != {"type", "label", "text"}
                or type(source["type"]) is not str
                or source["type"] not in {
                    "sessions", "runs", "goals", "usage", "inbox", "projects",
                }):
            _fail("source_invalid")
        return {"id": source_id, "type": source["type"],
                "label": _text(source["label"], 256),
                "text": _text(source["text"], 4096, empty=True)}

    def _panel(self, scope, actor, panel):
        if type(panel) is not dict or set(panel) != {"title", "layout", "theme", "widgets"}:
            _fail("panel_invalid")
        if type(panel["layout"]) is not str or panel["layout"] not in {"stack", "grid", "columns"}:
            _fail("layout_invalid")
        theme = panel["theme"]
        if (type(theme) is not dict or set(theme) != {"appearance", "accent"}
                or type(theme["appearance"]) is not str
                or theme["appearance"] not in {"system", "light", "dark", "high_contrast"}
                or type(theme["accent"]) is not str or theme["accent"] not in _COLORS):
            _fail("theme_invalid")
        if type(panel["widgets"]) is not list or len(panel["widgets"]) > 24:
            _fail("widgets_invalid")
        widgets, seen = [], set()
        for widget in panel["widgets"]:
            if (type(widget) is not dict
                    or set(widget) - {"id", "type", "title", "symbol", "color", "text",
                                      "value", "items", "source_id"}
                    or {"id", "type", "title"} - set(widget)):
                _fail("widget_invalid")
            identity = _identity(widget["id"])
            if identity in seen or type(widget["type"]) is not str or widget["type"] not in {
                "text", "stat", "list", "checklist", "progress", "notice",
            }:
                _fail("widget_invalid")
            seen.add(identity)
            result = {"id": identity, "type": widget["type"],
                      "title": _text(widget["title"], 256),
                      "symbol": widget.get("symbol", "circle"),
                      "color": widget.get("color", "primary")}
            if (type(result["symbol"]) is not str or type(result["color"]) is not str
                    or result["symbol"] not in _SYMBOLS or result["color"] not in _COLORS):
                _fail("widget_style_invalid")
            if "source_id" in widget:
                _identity(widget["source_id"])
                # The callback is a host selection lookup, never a URI loader.
                self._source(scope, actor, widget["source_id"])
                # Do not retain source contents; each native view resolves the
                # selection again, so deselection/revocation cannot leak a copy.
                result["source_id"] = widget["source_id"]
            if "text" in widget:
                result["text"] = _text(widget["text"], 4096, empty=True)
            if "value" in widget:
                value = widget["value"]
                if widget["type"] == "progress":
                    if (type(value) not in {int, float} or not math.isfinite(value)
                            or not 0 <= value <= 100):
                        _fail("widget_value_invalid")
                else:
                    value = _text(value, 256, empty=True)
                result["value"] = value
            if "items" in widget:
                items = widget["items"]
                if (widget["type"] not in {"list", "checklist"}
                        or type(items) is not list or len(items) > 50):
                    _fail("widget_items_invalid")
                if widget["type"] == "list":
                    result["items"] = [_text(item, 512) for item in items]
                else:
                    result["items"] = []
                    for item in items:
                        if (type(item) is not dict or set(item) != {"text", "checked"}
                                or type(item["checked"]) is not bool):
                            _fail("widget_items_invalid")
                        result["items"].append({"text": _text(item["text"], 512),
                                                "checked": item["checked"]})
            widgets.append(result)
        return {"title": _text(panel["title"], 256), "layout": panel["layout"],
                "theme": dict(theme), "widgets": widgets, "rendering": "native_declarative"}

    def set_panel(self, scope, actor, *, expected_revision, panel):
        self._guard(scope, actor, True)
        payload = self._panel(scope, actor, panel)
        return self._publish(scope, actor, "panel", payload, expected_revision)

    def toggle_widget_item(self, scope, actor, identity, expected_revision, *, widget_id,
                           item_index, checked):
        """Apply an explicit native checklist edit with the panel's exact revision."""
        _identity(widget_id)
        if type(item_index) is not int or item_index < 0 or type(checked) is not bool:
            _fail("widget_item_invalid")
        with self._tx(scope, actor, write=True) as connection:
            row = self._row(connection, scope, actor, identity, "panel")
            self._cas(row, expected_revision)
            if row["status"] != "active":
                _fail("panel_closed")
            payload = json.loads(row["payload_json"])
            widget = next((x for x in payload["widgets"] if x["id"] == widget_id), None)
            if (widget is None or widget["type"] != "checklist"
                    or item_index >= len(widget.get("items", []))):
                _fail("widget_item_invalid")
            widget["items"][item_index]["checked"] = checked
            row = self._change(connection, row, payload=payload)
            return self._view(row, scope, actor)

    def revoke_scope(self, scope, actor):
        """Owner revocation clears volatile answers and cancels pending UI data.

        This is explicit product-data revocation, independent from tool authority.
        A host client revocation must additionally make assert_current fail.
        """
        with self._tx(scope, actor, write=True) as connection:
            rows = connection.execute(
                "SELECT id FROM product_records WHERE scope_key=? AND client_id=?",
                (self._scope_key(scope), actor.client_id),
            ).fetchall()
            connection.execute(
                "UPDATE product_records SET status='revoked',revision=revision+1,updated_ns=? "
                "WHERE scope_key=? AND client_id=? AND kind!='notification_preferences' "
                "AND status NOT IN ('consumed','resumed','revoked')",
                (self._now(), self._scope_key(scope), actor.client_id),
            )
            self._put(connection, scope, actor, "scope_revocation", {}, status="revoked")
        for row in rows:
            self._discard_answer(row["id"])
        return {"revoked": True, "scope": asdict(scope)}
