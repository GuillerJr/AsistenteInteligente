"""Bound, typed Playwright actions over the reviewed MCP peer.

This adapter is not an egress sandbox. A browser/site can change after the last
check, and an already dispatched action cannot be rolled back. We report that
case as unverified, never as a successful user task.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from aegis_core.failure_boundary import record_boundary_error
from aegis_core.mcp.integrations import (
    BROWSER_CONTEXT_FIELDS,
    BrowserSessionConfiguration,
    IntegrationScope,
    _https_origin,
    guard_integration,
)
from aegis_core.privacy import redact_secrets_for_remote

BrowserCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
_TAB = re.compile(r"^- (\d+):( \(current\))? \[(.*)\]\((https?://[^\s]+|about:blank)\)$")
_REF = re.compile(r"\[ref=((?:f[1-9][0-9]{0,7})?e[1-9][0-9]{0,7})\]")
_CREDENTIAL = re.compile(
    r"password|passwd|contrase[ñn]a|api.?key|access.?token|refresh.?token|client.?secret|"
    r"one.?time|verification.?code|c[oó]digo de (?:seguridad|verificaci[oó]n)|"
    r"credit.?card|card.?number|tarjeta|cvv|\bpin\b|\btoken\b|passcode|\bclave\b|"
    r"private.?key|secret|credential",
    re.IGNORECASE,
)
_LOGIN_ENTRY = re.compile(
    r"\blogin\b|sign[ -]?in|log[ -]?in|inici(?:o|ar|a) (?:de )?sesi[oó]n", re.I,
)
_SENSITIVE = re.compile(_CREDENTIAL.pattern + "|" + _LOGIN_ENTRY.pattern, re.I)
_TRANSFER = re.compile(r"\b(?:download|upload|attach|descarga\w*|subir|adjunt\w*)\b", re.I)
_RESTRICTED_ACTION = re.compile(
    r"\b(?:send|submit|delete|remove|purchase|buy|pay|payment|checkout|grant|allow|authorize|"
    r"install|login|logout|sign[ -]?in|log[ -]?in|confirm|envi\w*|borr\w*|elimin\w*|"
    r"compr\w*|pag\w*|permit\w*|autoriza\w*|instal\w*|confirma\w*)\b|"
    r"inici(?:ar|a) sesi[oó]n",
    re.IGNORECASE,
)


def browser_request_tools(text: str) -> frozenset[str]:
    """Narrow natural activation; ordinary web research keeps its own tools."""
    lowered = text.casefold()
    if not re.search(r"\b(?:navegador|browser|chrome|chromium|edge|pesta[ñn]a|tab)\b|"
                     r"(?:esta|esa) p[aá]gina", lowered):
        return frozenset()
    names = {"browser_snapshot", "browser_tabs"}
    for name, pattern in {
        "browser_click": r"\b(?:clic|click|pulsa|presiona|pulse|press)\b",
        "browser_type": r"\b(?:rellena|escribe|completa|fill|type)\b",
        "browser_select_option": r"\b(?:selecciona|select|elige|desplegable)\b",
        "browser_press_key": r"\b(?:tecla|key|enter|escape|tabulador)\b",
        "browser_navigate": r"\b(?:abre|open|navega|navigate|visita|visit)\b",
        "browser_wait_for": r"\b(?:espera|wait)\b",
    }.items():
        if re.search(pattern, lowered):
            names.add(name)
    if len(names) == 2 and not re.search(
        r"pesta[ñn]as? (?:abiertas?|actual)|(?:esta|esa) p[aá]gina|"
        r"(?:lee|revisa|muestra) (?:el navegador|la pesta[ñn]a)", lowered,
    ):
        return frozenset()
    return frozenset(names)


class BrowserSessionError(ValueError):
    """Safe reason codes only; page content never enters errors or logs."""


def browser_action_schema(schema: Mapping[str, Any], name: str) -> dict[str, Any]:
    result = dict(schema)
    properties = dict(result.get("properties", {}))
    if name != "browser_snapshot":
        properties.update({
            "session_id": {"type": "string", "pattern": r"^[a-f0-9-]{36}$", "maxLength": 36},
            "expected_url": {"type": "string", "maxLength": 2048},
            "snapshot_sha256": {"type": "string", "pattern": r"^[a-f0-9]{64}$", "maxLength": 64},
        })
        result["required"] = sorted(set(result.get("required", [])) | BROWSER_CONTEXT_FIELDS)
    if name in {"browser_click", "browser_press_key", "browser_select_option"}:
        properties["verify_text"] = {"type": "string", "minLength": 1, "maxLength": 256}
        properties["verify_url"] = {"type": "string", "maxLength": 2048}
    if name == "browser_tabs":
        properties["action"] = {"type": "string", "enum": ["list", "new", "select"]}
        properties["index"] = {"type": "integer", "minimum": 0, "maximum": 100}
    if name in {"browser_click", "browser_type", "browser_select_option"}:
        properties["target"] = {
            "type": "string", "pattern": r"^(?:f[1-9][0-9]{0,7})?e[1-9][0-9]{0,7}$",
            "maxLength": 18,
        }
    if name == "browser_type":
        properties["submit"] = {"type": "boolean", "enum": [False]}
        properties["text"] = {"type": "string", "maxLength": 4096}
    if name == "browser_press_key":
        properties["key"] = {"type": "string", "enum": [
            "Escape", "Tab", "Shift+Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
            "Home", "End", "PageUp", "PageDown",
        ]}
    result["properties"] = properties
    result["additionalProperties"] = False
    return result


def _text(result: Mapping[str, Any]) -> str:
    if result.get("isError") is True:
        raise BrowserSessionError("browser_peer_error")
    content = result.get("content")
    if not isinstance(content, list) or any(
        not isinstance(block, dict) or block.get("type") != "text"
        or not isinstance(block.get("text"), str) for block in content
    ):
        raise BrowserSessionError("browser_response_invalid")
    value = "\n".join(block["text"] for block in content)
    if len(value.encode()) > 32768:
        raise BrowserSessionError("browser_response_too_large")
    return value


def _safe_url(url: str, scope: IntegrationScope, *, blank: bool = False) -> None:
    if url == "about:blank" and blank:
        return
    if (redact_secrets_for_remote(url).text != url
            or _https_origin(url) not in scope.browser_origins):
        raise BrowserSessionError("browser_origin_changed")


def _tabs(value: str) -> dict[int, tuple[bool, str]]:
    section = re.search(r"^### Result\n([\s\S]*?)(?=\n### |\Z)", value, re.MULTILINE)
    if section is not None:
        value = section[1]
    found: dict[int, tuple[bool, str]] = {}
    for line in value.splitlines():
        match = _TAB.fullmatch(line)
        if match:
            index = int(match[1])
            if index > 100 or index in found:
                raise BrowserSessionError("browser_tabs_invalid")
            found[index] = (bool(match[2]), match[4])
    if not found or sum(current for current, _ in found.values()) != 1:
        raise BrowserSessionError("browser_tabs_invalid")
    return found


@dataclass(frozen=True)
class BrowserSnapshot:
    url: str
    title: str
    text: str
    refs: dict[str, str]
    sensitive: bool

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(
            {"url": self.url, "title": self.title, "text": self.text},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()


def _node_label(line: str) -> tuple[str, str] | None:
    match = re.match(r'^\s*- ([A-Za-z][A-Za-z0-9_-]*) ("(?:[^"\\]|\\.)*")', line)
    if match is None:
        return None
    try:
        label = json.loads(match[2])
    except ValueError:
        return None
    return match[1], label


def _visible_literals(snapshot: BrowserSnapshot) -> frozenset[str]:
    literals = set()
    for line in snapshot.text.splitlines():
        label = _node_label(line)
        if label is not None and not label[1].startswith(("https://", "http://")):
            literals.add(label[1])
        match = re.match(
            r'^\s*- (?:text|generic|paragraph)(?: \[[^\]]+\])*: (.+)$', line,
        )
        if match is not None:
            literal = match[1]
            if literal.startswith('"'):
                try:
                    literal = json.loads(literal)
                except ValueError:
                    continue
            if isinstance(literal, str) and not literal.startswith(("https://", "http://")):
                literals.add(literal)
    return frozenset(literals)


def _selected_options(snapshot: BrowserSnapshot, target: str) -> tuple[str, ...]:
    lines = snapshot.text.splitlines()
    for position, line in enumerate(lines):
        if "[ref=" + target + "]" not in line:
            continue
        level = len(line) - len(line.lstrip())
        selected = []
        for child in lines[position + 1:]:
            if len(child) - len(child.lstrip()) <= level:
                break
            label = _node_label(child)
            if label is not None and label[0] == "option" and "[selected]" in child:
                selected.append(label[1])
        return tuple(sorted(selected))
    return ()


def _snapshot(value: str, scope: IntegrationScope, *, blank: bool) -> BrowserSnapshot:
    if re.search(r"^### Modal state\b", value, re.MULTILINE):
        raise BrowserSessionError("browser_modal_not_admitted")
    urls = re.findall(r"^- Page URL: (.+)$", value, re.MULTILINE)
    if len(urls) != 1:
        raise BrowserSessionError("browser_snapshot_invalid")
    _safe_url(urls[0], scope, blank=blank)
    titles = re.findall(r"^- Page Title: (.*)$", value, re.MULTILINE)
    # Only an explicit inline snapshot is accepted. Never follow a peer's file
    # links, read logs, generated code, events or other tabs' private titles.
    section = re.search(r"^### Snapshot\n```yaml\n([\s\S]*?)\n```(?:\n|$)", value, re.MULTILINE)
    if section is None:
        raise BrowserSessionError("browser_snapshot_invalid")
    safe_lines, refs = [], {}
    sensitive = bool(re.search(
        r"(?:^|/)(?:login|sign-in|signin|auth|oauth|authorize|password|credentials?)(?:/|$)",
        urlsplit(urls[0]).path, re.I,
    ))
    blocked_level: int | None = None
    for line in section[1].splitlines():
        level = len(line) - len(line.lstrip())
        if blocked_level is not None and level > blocked_level:
            continue
        blocked_level = None
        if _SENSITIVE.search(line):
            label = _node_label(line)
            access_entry = (label is not None and label[0] in {"link", "button"}
                            and _LOGIN_ENTRY.search(label[1])
                            and not _CREDENTIAL.search(line))
            # A public page may contain a login link alongside an ordinary
            # search field. Omit that entry, while credential fields/routes or
            # secret content still block all interaction on the whole page.
            sensitive = sensitive or not access_entry
            blocked_level = level
            safe_lines.append("- [credential field omitted]")
            continue
        sanitized = redact_secrets_for_remote(line).text
        if sanitized != line:
            sensitive = True
            blocked_level = level
            safe_lines.append("- [secret content omitted]")
            continue
        safe_lines.append(sanitized)
        for ref in _REF.findall(line):
            if ref in refs:
                raise BrowserSessionError("browser_snapshot_invalid")
            refs[ref] = line
    text = "\n".join(safe_lines)
    if len(text.encode()) > 24000:
        raise BrowserSessionError("browser_snapshot_too_large")
    title = redact_secrets_for_remote(titles[0] if titles else "").text[:256]
    return BrowserSnapshot(urls[0], title, text, refs, sensitive)


class ReviewedBrowserSession:
    @classmethod
    async def bootstrap_task(
        cls, scope: IntegrationScope, configuration: BrowserSessionConfiguration,
        url: str, call: BrowserCall, *, still_available: Callable[[], bool],
    ) -> tuple[ReviewedBrowserSession, dict[str, Any]]:
        """Use only an explicitly approved URL before a task has any page binding.

        Other tabs' URLs/titles stay local. Duplicates require owner resolution;
        no tab is selected and no page content is read in that case.
        """
        _safe_url(url, scope)

        async def checked(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if not still_available():
                raise BrowserSessionError("browser_session_revoked")
            result = await call(name, arguments)
            if not still_available():
                raise BrowserSessionError("browser_session_revoked")
            return result

        initial = _text(await checked("browser_tabs", {"action": "list"}))
        # The peer's explicit empty-list sentinel is admitted only before any
        # binding exists. Bound sessions still require a unique current tab.
        empty = re.fullmatch(
            r"(?:### Result\n)?No open tabs(?:\.?|\. Navigate to a URL to create one\.)\s*",
            initial,
        )
        tabs = {} if empty else _tabs(initial)
        matches = [index for index, (_, candidate) in tabs.items() if candidate == url]
        if len(matches) > 1:
            raise BrowserSessionError("browser_task_tab_ambiguous")
        if matches:
            index = matches[0]
            if not tabs[index][0]:
                _text(await checked("browser_tabs", {"action": "select", "index": index}))
        else:
            _text(await checked("browser_tabs", {"action": "new", "url": url}))
        selected = _tabs(_text(await checked("browser_tabs", {"action": "list"})))
        matches = [index for index, (current, candidate) in selected.items()
                   if current and candidate == url]
        if len(matches) != 1 or sum(candidate == url for _, candidate in selected.values()) != 1:
            raise BrowserSessionError("browser_task_selection_unverified")
        session = cls(scope, configuration)
        session.tab_index, session.url = matches[0], url
        result = await session.execute("browser_snapshot", {}, checked,
                                       still_available=still_available)
        return session, result

    def __init__(self, scope: IntegrationScope, configuration: BrowserSessionConfiguration) -> None:
        self.scope, self.configuration = scope, configuration
        self.session_id = str(uuid4())
        self.tab_index = configuration.selected_tab_index
        self.url = configuration.selected_tab_url or "about:blank"
        self._lock = asyncio.Lock()
        self._invalidated = False
        self.bound = False

    def invalidate(self) -> None:
        self._invalidated = True
        self.bound = False

    async def _bound_tabs(self, call: BrowserCall) -> dict[int, tuple[bool, str]]:
        tabs = _tabs(_text(await call("browser_tabs", {"action": "list"})))
        if tabs.get(self.tab_index) != (True, self.url):
            raise BrowserSessionError("browser_selected_tab_changed")
        _safe_url(self.url, self.scope, blank=self.configuration.mode == "isolated")
        if sum(url == self.url for _, url in tabs.values()) != 1:
            raise BrowserSessionError("browser_tab_binding_ambiguous")
        return tabs

    async def _capture(self, call: BrowserCall) -> BrowserSnapshot:
        self.bound = False
        await self._bound_tabs(call)
        snapshot = _snapshot(_text(await call("browser_snapshot", {})), self.scope,
                             blank=self.configuration.mode == "isolated")
        if snapshot.url != self.url:
            raise BrowserSessionError("browser_selected_tab_changed")
        await self._bound_tabs(call)
        self.bound = True
        return snapshot

    def _result(self, snapshot: BrowserSnapshot, *, verified: bool, executed: bool,
                outcome: str, tabs: dict[int, tuple[bool, str]] | None = None,
                include_content: bool = True, before_sha256: str | None = None,
                verification_scope: str = "bound_accessibility_snapshot") -> dict[str, Any]:
        data: dict[str, Any] = {
            "session_id": self.session_id, "tab_index": self.tab_index,
            "verified": verified, "action_executed": executed, "outcome": outcome,
            "goal_verified": verified,
            "verification_scope": verification_scope, "page_content_untrusted": True,
        }
        if include_content:
            data.update(expected_url=snapshot.url, snapshot_sha256=snapshot.digest,
                        title=snapshot.title, snapshot=snapshot.text,
                        post_snapshot_sha256=snapshot.digest)
        if before_sha256 is not None:
            data["pre_snapshot_sha256"] = before_sha256
        if tabs is not None:
            safe_tabs = []
            for index, (current, url) in tabs.items():
                try:
                    _safe_url(url, self.scope)
                except ValueError:
                    continue
                safe_tabs.append({"index": index, "current": current, "url": url})
            data["tabs"] = safe_tabs
        result = {
            "content": [{"type": "text", "text": "Bound browser observation: " + outcome}],
            "structuredContent": data,
            "isError": outcome in {"peer_action_failed", "postcondition_unavailable"},
        }
        if len(json.dumps(result, ensure_ascii=False).encode()) > 60000:
            raise BrowserSessionError("browser_output_too_large")
        return result

    async def execute(self, name: str, arguments: Mapping[str, Any], call: BrowserCall,
                      *, still_available: Callable[[], bool]) -> dict[str, Any]:
        if not self.configuration.remote_content_allowed:
            raise BrowserSessionError("browser_remote_consent_required")
        guard_integration(self.scope, name, arguments)
        async with self._lock, asyncio.timeout(15):
            if self._invalidated or not still_available():
                raise BrowserSessionError("browser_session_revoked")
            peer_call = call

            async def checked_call(
                tool_name: str, tool_arguments: dict[str, Any],
            ) -> dict[str, Any]:
                if self._invalidated or not still_available():
                    raise BrowserSessionError("browser_session_revoked")
                value = await peer_call(tool_name, tool_arguments)
                if self._invalidated or not still_available():
                    raise BrowserSessionError("browser_session_revoked")
                return value

            call = checked_call
            before = await self._capture(call)
            if name == "browser_snapshot":
                return self._result(before, verified=True, executed=False, outcome="observed")
            if (arguments.get("session_id") != self.session_id
                    or arguments.get("expected_url") != before.url
                    or arguments.get("snapshot_sha256") != before.digest):
                raise BrowserSessionError("browser_snapshot_stale")
            if before.sensitive and not (
                name == "browser_tabs" and arguments.get("action") == "list"
            ):
                raise BrowserSessionError("browser_sensitive_page_action_excluded")
            if name == "browser_close":
                raise BrowserSessionError("browser_close_excluded")
            target = arguments.get("target")
            if target is not None:
                row = before.refs.get(target)
                if row is None:
                    raise BrowserSessionError("browser_target_missing_or_sensitive")
                if _TRANSFER.search(row):
                    raise BrowserSessionError("browser_file_transfer_excluded")
                if _RESTRICTED_ACTION.search(row):
                    raise BrowserSessionError("browser_restricted_action_excluded")
                if _RESTRICTED_ACTION.search(arguments.get("element", "")):
                    raise BrowserSessionError("browser_restricted_action_excluded")
                if name == "browser_type" and ("textbox" not in row or 'textbox "' not in row):
                    raise BrowserSessionError("browser_fill_target_not_named_textbox")
                if name == "browser_select_option" and "combobox" not in row:
                    raise BrowserSessionError("browser_select_target_not_combobox")
                if name == "browser_select_option":
                    values = arguments["values"]
                    if any(_RESTRICTED_ACTION.search(value) or _TRANSFER.search(value)
                           for value in values):
                        raise BrowserSessionError("browser_restricted_option_excluded")
                    lines = before.text.splitlines()
                    position = lines.index(row)
                    level = len(row) - len(row.lstrip())
                    restricted = False
                    for option in lines[position + 1:]:
                        if len(option) - len(option.lstrip()) <= level:
                            break
                        label = _node_label(option)
                        if label is None or label[0] != "option":
                            continue
                        if _RESTRICTED_ACTION.search(label[1]) or _TRANSFER.search(label[1]):
                            restricted = True
                    if restricted:
                        # The snapshot does not expose HTML option values. If a
                        # restricted option exists, even a benign-looking value
                        # can belong to that option. There is no verifiable
                        # value-to-label mapping before the action is sent.
                        raise BrowserSessionError("browser_restricted_option_excluded")
            if name == "browser_press_key" and before.sensitive:
                raise BrowserSessionError("browser_key_on_sensitive_page_excluded")
            tabs = await self._bound_tabs(call)
            action = arguments.get("action")
            if name == "browser_tabs" and action == "list":
                return self._result(before, verified=True, executed=False,
                                    outcome="observed", tabs=tabs)
            if name == "browser_tabs" and action == "select":
                if tabs.get(arguments["index"], (None, None))[1] != arguments["url"]:
                    raise BrowserSessionError("browser_tab_target_changed")
                if sum(url == arguments["url"] for _, url in tabs.values()) != 1:
                    raise BrowserSessionError("browser_tab_binding_ambiguous")
            outgoing = {key: value for key, value in arguments.items()
                        if key not in BROWSER_CONTEXT_FIELDS | {"verify_text", "verify_url"}}
            if name == "browser_tabs" and action == "select":
                outgoing.pop("url")
            if self._invalidated or not still_available():
                raise BrowserSessionError("browser_session_revoked")
            # No retry after dispatch: even a failed post-check can follow a real
            # form submission/navigation. A new owner approval is required.
            dispatched = False
            try:
                dispatched = True
                result = await call(name, outgoing)
                if result.get("isError") is True:
                    self.invalidate()
                    return self._result(before, verified=False, executed=True,
                                        outcome="peer_action_failed", include_content=False,
                                        before_sha256=before.digest)
                action_text = _text(result)
                if re.search(r"^### (?:Modal state|Events)\b", action_text, re.MULTILINE):
                    raise BrowserSessionError("browser_action_requires_local_review")
                if self._invalidated or not still_available():
                    raise BrowserSessionError("browser_session_revoked")
                if (name == "browser_navigate" or "verify_url" in arguments
                        or (name == "browser_tabs" and action in {"select", "new"})):
                    next_tabs = _tabs(_text(await call("browser_tabs", {"action": "list"})))
                    current = [(index, url) for index, (selected, url) in next_tabs.items()
                               if selected]
                    index, url = current[0]
                    if url != arguments.get("verify_url", arguments.get("url")):
                        raise BrowserSessionError("browser_navigation_postcondition_failed")
                    _safe_url(url, self.scope)
                    self.tab_index, self.url = index, url
                after = await self._capture(call)
                verification_scope = "action_performed"
                if name == "browser_type":
                    row = after.refs.get(target, "")
                    wanted = arguments["text"]
                    value = re.search(r"\[ref=" + re.escape(target) + r"\][^:]*:\s*(.*)$", row)
                    actual = value[1] if value is not None else None
                    if actual is not None and actual.startswith('"'):
                        try:
                            actual = json.loads(actual)
                        except ValueError:
                            actual = None
                    verified = (actual == wanted
                                and _node_label(row) == _node_label(before.refs[target]))
                    verification_scope = "field_value"
                elif name == "browser_select_option":
                    selected = _selected_options(after, target)
                    expected = tuple(sorted(arguments["values"]))
                    if "verify_text" in arguments and len(expected) == 1:
                        expected = (arguments["verify_text"],)
                    verified = (bool(selected) and selected == expected
                                and selected != _selected_options(before, target)
                                and _node_label(after.refs.get(target, ""))
                                == _node_label(before.refs[target]))
                    verification_scope = "selected_accessible_options"
                elif "verify_text" in arguments:
                    literal = arguments["verify_text"]
                    verified = (literal in _visible_literals(after)
                                and literal not in _visible_literals(before))
                    if not verified and target is not None:
                        previous, current = before.refs[target], after.refs.get(target, "")
                        label = _node_label(current)
                        verified = (label is not None and label == _node_label(previous)
                                    and literal == label[1]
                                    and any(marker in current and marker not in previous
                                            for marker in ("[selected]", "[expanded]")))
                        if verified:
                            verification_scope = "control_state"
                    if verification_scope != "control_state":
                        verification_scope = "new_accessible_literal"
                elif "verify_url" in arguments:
                    verified = (after.url == arguments["verify_url"] and after.url != before.url)
                    verification_scope = "navigation_url"
                elif name == "browser_wait_for":
                    verified = ("time" in arguments
                                or ("text" in arguments
                                    and arguments["text"] in _visible_literals(after))
                                or ("textGone" in arguments
                                    and arguments["textGone"] not in _visible_literals(after)))
                    verification_scope = ("elapsed_wait" if "time" in arguments
                                          else "accessible_literal_condition")
                elif name == "browser_navigate" or name == "browser_tabs":
                    verified = after.url == arguments["url"]
                    verification_scope = "navigation_url"
                else:
                    verified = False
                return self._result(after, verified=verified, executed=True,
                                    outcome="verified" if verified else "action_performed",
                                    before_sha256=before.digest,
                                    verification_scope=verification_scope)
            except Exception as error:
                record_boundary_error(error)
                if dispatched:
                    self.invalidate()
                    return self._result(before, verified=False, executed=True,
                                        outcome="postcondition_unavailable", include_content=False,
                                        before_sha256=before.digest)
                raise
            except BaseException:
                if dispatched:
                    self.invalidate()
                raise
