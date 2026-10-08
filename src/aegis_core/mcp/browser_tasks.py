"""Ephemeral browser access approved for one trusted request, never a saved grant."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.browser_preferences import (
    BrowserAutomationPreferences,
    browser_automation_path,
    load_browser_automation_preferences,
)
from aegis_core.config import Settings
from aegis_core.contracts import (
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult
from aegis_core.mcp.browser_session import (
    BrowserSessionError,
    ReviewedBrowserSession,
)
from aegis_core.mcp.client import (
    McpHost,
    McpServerConfiguration,
    StdioMcpClient,
)
from aegis_core.mcp.integrations import BrowserSessionConfiguration, IntegrationScope, _https_origin
from aegis_core.mcp.local_peers import browser_launch_arguments, local_peer_configuration
from aegis_core.privacy import redact_secrets_for_remote
from aegis_core.request_intent import mask_quoted_text
from aegis_core.tools.audit import AuditSink
from aegis_core.tools.broker import PolicyContext, PolicyViolation, ToolDefinition

# Exact upstream contracts reviewed from the pinned 0.0.80 peer. Optional fields,
# defaults and excluded dangerous branches are included in these fingerprints.
BROWSER_TASK_SCHEMA_PINS = {
    "browser_press_key": "344b4cfba679ad3fac16c9fe981063e68fe1a2f35c25d01fa1c77f7089753790",
    "browser_type": "8dc78dab8c6762f014ff72adbf4d6728a4514fbaabf6d6c20b921944e713a142",
    "browser_navigate": "48e389ba93a30bbb05632d8a954f2c52aa2ab73b20ddf5d67b11c134567e3d07",
    "browser_snapshot": "1923030f7325106aa9ebc001e64b64c3ea8ab72f61e559dd9e0b58ac3191f535",
    "browser_click": "a88890583405f9f288a1f9207df170d7289988c0b16ae07a1dc4a8608210487d",
    "browser_select_option": "ecfd4a3248db54359ebc66b3e1e0687cd6c7e9ea072e4ff65a836edbdf6f483b",
    "browser_tabs": "065925d8a1c0cf40230c42ac73ed1f86d79f7d959b440f6e31118b71de17387a",
    "browser_wait_for": "06991dc1b8243f8cea0ce2252ab307d0c88b0051c69cb34f51df30a432f23d2f",
}
_OPERATIONS = {
    "snapshot": "browser_snapshot",
    "navigate": "browser_navigate",
    "click": "browser_click",
    "fill": "browser_type",
    "select": "browser_select_option",
    "key": "browser_press_key",
    "tabs": "browser_tabs",
    "wait": "browser_wait_for",
}
_KEYS = Literal[
    "Escape",
    "Tab",
    "Shift+Tab",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
    "PageUp",
    "PageDown",
]
_URLS = re.compile(r'https?://[^\s<>"\'«»\u201c\u201d\u2018\u2019]+')


def is_browser_task_request(request: UserRequest | str) -> bool:
    original = request.text if isinstance(request, UserRequest) else request
    text = mask_quoted_text(original.casefold()).strip()
    if re.match(r"(?:¿?\s*)?(?:c[oó]mo\b|how\b|qu[eé]\b|what\b|explica\w*\b|explain\b)", text):
        return False
    if re.search(r"\b(?:no abras|no uses|do not open|do not use|don't open)\b", text):
        return False
    if re.search(r"\b(?:investiga|research|busca informaci[oó]n|search information)\b", text):
        return False
    browser = re.search(
        r"\b(?:navegador|browser|chrome|chromium|edge|pesta[ñn]a|tab|p[aá]gina|sitio web|"
        r"website|webpage)\b",
        text,
    )
    operation = re.search(
        r"\b(?:abre|abrir|open|visita|visit|ve a|navega|navegar|navigate|usa|use|clic|click|"
        r"pulsa|rellena|fill|"
        r"escribe|type|selecciona|select|elige|espera|wait|revisa|lee)\b",
        text,
    )
    browser_search = browser and re.search(r"\b(?:busca|buscar|search)\b", text)
    return bool(browser_search or (operation and (browser or explicit_browser_urls(original))))


def explicit_browser_urls(text: str) -> frozenset[str]:
    urls = set()
    for match in _URLS.finditer(text):
        url = match[0].rstrip(".,;:!?)]}")
        try:
            url = canonical_browser_url(url)
        except ValueError:
            # Invalid explicit destinations cannot disappear and thereby turn
            # this into permission to propose an unrelated site.
            pass
        urls.add(url)
    return frozenset(urls)


def canonical_browser_url(url: str) -> str:
    origin = _https_origin(url)
    parsed = urlsplit(url)
    return urlunsplit(
        ("https", urlsplit(origin).netloc, parsed.path or "/", parsed.query, parsed.fragment)
    )


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BrowserTaskOpenArguments(_Arguments):
    url: str = Field(min_length=1, max_length=2048)
    objective: str = Field(min_length=1, max_length=8192)
    mode: Literal["extension", "chrome", "isolated"] | None = None

    @field_validator("url")
    @classmethod
    def canonical_url(cls, value: str) -> str:
        return canonical_browser_url(value)

    @model_validator(mode="after")
    def bounded(self):
        _https_origin(self.url)
        if (
            len(self.objective.encode()) > 8192
            or redact_secrets_for_remote(self.url).text != self.url
        ):
            raise ValueError("browser_task_open_arguments_invalid")
        return self


class BrowserTaskCloseArguments(_Arguments):
    session_id: UUID


class BrowserTaskActionArguments(BrowserTaskCloseArguments):
    expected_url: str = Field(min_length=1, max_length=2048)
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation: Literal["snapshot", "navigate", "click", "fill", "select", "key", "tabs", "wait"]
    url: str | None = Field(default=None, max_length=2048)
    target: str | None = Field(default=None, pattern=r"^(?:f[1-9][0-9]{0,7})?e[1-9][0-9]{0,7}$")
    element: str | None = Field(default=None, max_length=256)
    text: str | None = Field(default=None, max_length=4096)
    values: list[str] | None = Field(default=None, min_length=1, max_length=8)
    key: _KEYS | None = None
    tab_action: Literal["list", "new", "select"] | None = None
    index: int | None = Field(default=None, ge=0, le=100, strict=True)
    wait_seconds: float | None = Field(default=None, gt=0, le=5, strict=True)
    wait_text: str | None = Field(default=None, min_length=1, max_length=256)
    wait_text_gone: str | None = Field(default=None, min_length=1, max_length=256)
    verify_text: str | None = Field(default=None, min_length=1, max_length=256)
    verify_url: str | None = Field(default=None, max_length=2048)

    @field_validator("url", "expected_url", "verify_url")
    @classmethod
    def canonical_urls(cls, value: str | None) -> str | None:
        return canonical_browser_url(value) if value is not None else None

    @model_validator(mode="after")
    def operation_shape(self):
        allowed, required = {
            "snapshot": (set(), set()),
            "navigate": ({"url"}, {"url"}),
            "click": ({"target", "element", "verify_text", "verify_url"}, {"target"}),
            "fill": ({"target", "element", "text"}, {"target", "text"}),
            "select": (
                {"target", "element", "values", "verify_text", "verify_url"},
                {"target", "values"},
            ),
            "key": ({"key", "verify_text", "verify_url"}, {"key"}),
            "tabs": ({"tab_action", "index", "url"}, {"tab_action"}),
            "wait": ({"wait_seconds", "wait_text", "wait_text_gone"}, set()),
        }[self.operation]
        values = self.model_dump(exclude_none=True)
        given = set(values) - {"session_id", "expected_url", "snapshot_sha256", "operation"}
        if given - allowed or not required.issubset(given):
            raise ValueError("browser_task_operation_arguments_invalid")
        if self.operation == "wait" and len(given) != 1:
            raise ValueError("browser_task_wait_condition_required")
        return self

    def remote_arguments(self) -> dict[str, Any]:
        value = self.model_dump(mode="json", exclude_none=True)
        value.pop("operation")
        for local, remote in (
            ("tab_action", "action"),
            ("wait_seconds", "time"),
            ("wait_text", "text"),
            ("wait_text_gone", "textGone"),
        ):
            if local in value:
                value[remote] = value.pop(local)
        if self.operation == "fill":
            value["submit"] = False
        return {} if self.operation == "snapshot" else value


class BrowserPeer(Protocol):
    available: bool

    async def start(self) -> tuple[dict[str, Any], ...]: ...
    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]: ...
    async def close(self) -> None: ...


@dataclass
class _Task:
    request_id: UUID
    objective: str
    configuration: McpServerConfiguration
    preference_mode: str
    expires: float
    client: BrowserPeer | None = None
    session: ReviewedBrowserSession | None = None
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    snapshot: dict[str, Any] | None = None
    actions: int = 0
    revoked: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timer: asyncio.Task | None = None
    close_task: asyncio.Task | None = None
    cleanup_scheduled: bool = False


class BrowserTaskService:
    MAX_SESSIONS = 4
    MAX_ACTIONS = 24
    TTL_SECONDS = 600.0
    MAX_PENDING_CALLS = 4096

    def __init__(
        self,
        settings: Settings,
        *,
        audit_sink: AuditSink | None = None,
        preferences_loader: Callable[[], BrowserAutomationPreferences] | None = None,
        client_factory: Callable[[McpServerConfiguration], BrowserPeer] = StdioMcpClient,
        clock: Callable[[], float] = time.monotonic,
        schema_pins: Mapping[str, str] | None = None,
    ) -> None:
        # The owning executor/broker records authorizations and executions using
        # this same sink. The service never logs URLs, snapshots or form data.
        self._audit_sink = audit_sink
        self.settings = settings
        self._preferences = preferences_loader or (
            lambda: load_browser_automation_preferences(browser_automation_path(settings))
        )
        self._factory, self._clock = client_factory, clock
        self._pins = dict(BROWSER_TASK_SCHEMA_PINS if schema_pins is None else schema_pins)
        self._tasks: dict[str, _Task] = {}
        self._open_calls: dict[tuple[UUID, str], float] = {}
        self._finished: dict[UUID, float] = {}
        self._approved_origins: dict[UUID, dict[str, float]] = {}
        self._cleanup: set[asyncio.Task] = set()
        self._closed = False
        self._pin_error: str | None = None
        self._base: McpServerConfiguration | None = None
        try:
            packages = settings.mcp_node_packages_path
            version_file = packages / "node_modules/@playwright/mcp/package.json"
            if version_file.stat().st_size > 65536:
                raise ValueError
            manifest = json.loads(version_file.read_text())
            if not isinstance(manifest, dict) or manifest.get("version") != "0.0.80":
                raise ValueError
            scope = IntegrationScope(provider="playwright", browser_origins={"https://example.com"})
            self._base = local_peer_configuration(
                scope,
                node=settings.mcp_node_executable_path,
                packages=packages,
                working_directory=packages,
                browser_session=BrowserSessionConfiguration(
                    mode="extension",
                    selected_tab_url="https://example.com",
                    remote_content_allowed=True,
                ),
            )
            if set(self._pins) != set(_OPERATIONS.values()):
                raise ValueError
        except (OSError, ValueError, TypeError, RecursionError):
            self._pin_error = "browser_task_peer_unavailable"
            self._base = None

    def _prefs(self) -> BrowserAutomationPreferences:
        return BrowserAutomationPreferences.model_validate(self._preferences().model_dump())

    def _prune_retained(self) -> None:
        now = self._clock()
        # Broker approvals last at most five minutes. Retain consumed-call and
        # finished-request tombstones for ten minutes, never evict live grants.
        self._finished = {
            key: deadline for key, deadline in self._finished.items() if deadline > now
        }
        self._open_calls = {
            key: deadline for key, deadline in self._open_calls.items() if deadline > now
        }
        self._approved_origins = {
            request_id: {origin: deadline for origin, deadline in origins.items() if deadline > now}
            for request_id, origins in self._approved_origins.items()
            if any(deadline > now for deadline in origins.values())
        }

    def _valid(self, task: _Task, context: PolicyContext | None = None) -> bool:
        try:
            prefs = self._prefs()
            return (
                not self._closed
                and not task.revoked
                and self._clock() < task.expires
                and prefs.enabled
                and prefs.mode == task.preference_mode
                and task.client is not None
                and task.client.available
                and (
                    context is None
                    or (
                        context.request_id == task.request_id
                        and context.request_text == task.objective
                        and not context.is_security_compromised()
                    )
                )
            )
        except (ValueError, OSError, TypeError, AttributeError):
            return False

    @staticmethod
    def _trusted(context: PolicyContext) -> tuple[UUID, str]:
        if (
            not isinstance(context.request_id, UUID)
            or not isinstance(context.request_text, str)
            or not context.request_text
            or context.is_security_compromised()
        ):
            raise PolicyViolation("browser_task_request_context_required")
        return context.request_id, context.request_text

    def _open_guard(self, arguments: BaseModel, context: PolicyContext) -> BaseModel:
        request_id, text = self._trusted(context)
        assert isinstance(arguments, BrowserTaskOpenArguments)
        if self._base is None or self._closed:
            raise PolicyViolation("browser_task_peer_unavailable")
        prefs = self._prefs()
        if not prefs.enabled or not is_browser_task_request(text) or arguments.objective != text:
            raise PolicyViolation("browser_task_original_objective_required")
        explicit = explicit_browser_urls(text)
        if explicit and arguments.url not in explicit:
            raise PolicyViolation("browser_task_original_url_required")
        self._prune_retained()
        if _https_origin(arguments.url) in self._approved_origins.get(request_id, {}):
            raise PolicyViolation("browser_task_origin_already_approved")
        if arguments.mode is not None and arguments.mode != prefs.mode:
            expected = {
                "isolated": r"\b(?:aislado|separado|isolated|separate)\b",
                "extension": r"\b(?:extensi[oó]n|extension)\b",
                "chrome": r"\b(?:chrome directo|direct chrome|chrome directly|"
                r"depuraci[oó]n|debugging|cdp)\b",
            }[arguments.mode]
            if not re.search(expected, mask_quoted_text(text.casefold())):
                raise PolicyViolation("browser_task_mode_not_requested")
        return arguments.model_copy(update={"mode": arguments.mode or prefs.mode})

    def _action_guard(self, arguments: BaseModel, context: PolicyContext) -> BaseModel:
        request_id, text = self._trusted(context)
        assert isinstance(arguments, BrowserTaskCloseArguments)
        task = self._tasks.get(str(arguments.session_id))
        if task is None or task.request_id != request_id or not self._valid(task, context):
            raise PolicyViolation("browser_task_session_unavailable")
        if isinstance(arguments, BrowserTaskActionArguments):
            for literal in (
                arguments.text,
                arguments.verify_text,
                arguments.wait_text,
                arguments.wait_text_gone,
            ):
                if literal is not None and literal not in text:
                    raise PolicyViolation("browser_task_literal_not_requested")
            if (
                arguments.verify_url is not None
                and arguments.verify_url not in explicit_browser_urls(text)
            ):
                raise PolicyViolation("browser_task_literal_not_requested")
            if task.actions >= self.MAX_ACTIONS:
                raise PolicyViolation("browser_task_action_limit")
            remote = _OPERATIONS[arguments.operation]
            payload = arguments.remote_arguments()
            Draft202012Validator(task.schemas[remote]).validate(payload)
            if (
                task.snapshot is None
                or arguments.expected_url != task.snapshot.get("expected_url")
                or arguments.snapshot_sha256 != task.snapshot.get("snapshot_sha256")
            ):
                raise PolicyViolation("browser_task_snapshot_stale")
        return arguments

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        roles = frozenset({AgentRole.PLANNER, AgentRole.CODE_SECURITY})
        return tuple(
            ToolDefinition(
                name=name,
                description=description,
                arguments_model=model,
                capability=Capability.APPLICATION_CONTROL,
                risk=risk,
                allowed_roles=roles,
                requires_confirmation=confirm,
                argument_guard=guard,
                provider_label="Navegador por tarea",
                external_destination="NVIDIA / navegador local",
            )
            for name, description, model, risk, confirm, guard in (
                (
                    "browser_task_open",
                    "Approve the exact URL and original objective for this task. "
                    "Read page content with NVIDIA; find/open the tab automatically. "
                    "No persistent access.",
                    BrowserTaskOpenArguments,
                    RiskLevel.HIGH,
                    True,
                    self._open_guard,
                ),
                (
                    "browser_task_action",
                    "One typed action in this request's approved browser session. "
                    "Use the latest snapshot binding and literals from the original instruction. "
                    "No code, credentials, sends, deletion, purchases or file transfers. "
                    "Stop if unverified.",
                    BrowserTaskActionArguments,
                    RiskLevel.MEDIUM,
                    False,
                    self._action_guard,
                ),
                (
                    "browser_task_close",
                    "Disconnect this request's browser access. Never close personal "
                    "browser windows or tabs.",
                    BrowserTaskCloseArguments,
                    RiskLevel.MEDIUM,
                    False,
                    self._action_guard,
                ),
            )
        )

    def handlers(self) -> dict[str, Any]:
        return {
            name: self._handle
            for name in (
                "browser_task_open",
                "browser_task_action",
                "browser_task_close",
            )
        }

    def names_for_request(self, request: UserRequest) -> frozenset[str]:
        self._prune_retained()
        if request.request_id in self._finished:
            return frozenset()
        try:
            if not self._prefs().enabled or self._closed:
                return frozenset()
        except (ValueError, OSError, TypeError):
            return frozenset()
        approved = self._approved_origins.get(request.request_id, {})
        remaining = False
        for url in explicit_browser_urls(request.text):
            try:
                remaining |= _https_origin(url) not in approved
            except ValueError:
                continue
        active = self.context_for_request(request.request_id) is not None
        names = {"browser_task_action", "browser_task_close"} if active else set()
        if is_browser_task_request(request) and (remaining or not approved):
            names.add("browser_task_open")
        return frozenset(names)

    def context_for_request(self, request_id: UUID) -> dict[str, Any] | None:
        for task in self._tasks.values():
            if task.request_id != request_id or task.snapshot is None:
                continue
            if self._valid(task) and task.session is not None and not task.session._invalidated:
                value = copy.deepcopy(task.snapshot)
                value.pop("tabs", None)
                value["snapshot_truncated"] = False
                if len(json.dumps(value, ensure_ascii=False).encode()) > 15360:
                    lines = value.pop("snapshot", "").splitlines()
                    value["snapshot"] = ""
                    value["snapshot_truncated"] = True
                    kept = []
                    for line in lines:
                        value["snapshot"] = "\n".join((*kept, line))
                        if len(json.dumps(value, ensure_ascii=False).encode()) > 15360:
                            value["snapshot"] = "\n".join(kept)
                            break
                        kept.append(line)
                return value
            self._schedule_drop(task)
        return None

    def status(self) -> dict[str, Any]:
        try:
            prefs = self._prefs()
            enabled, mode = prefs.enabled, prefs.mode
            reason = self._pin_error or (None if enabled else "browser_task_disabled")
        except (ValueError, OSError, TypeError):
            enabled, mode, reason = False, "invalid", "browser_task_preferences_invalid"
        return {
            "scope": "browser_task_runtime",
            "enabled": enabled,
            "mode": mode,
            "node_configured": self._base is not None,
            "active_sessions": sum(self._valid(task) for task in self._tasks.values()),
            "reason_code": reason,
            "contains_user_content": False,
        }

    def ipc_handlers(self) -> dict[str, Any]:
        async def handle(request: IpcRequest) -> IpcHandlerResult:
            if request.method != "browser.task.status":
                return IpcHandlerResult(ok=False, error_code="method_not_found")
            if request.payload:
                return IpcHandlerResult(ok=False, error_code="invalid_payload")
            return IpcHandlerResult(ok=True, payload=self.status())

        return {"browser.task.status": handle}

    def _configuration(self, arguments: BrowserTaskOpenArguments) -> McpServerConfiguration:
        assert self._base is not None
        scope = IntegrationScope(
            provider="playwright", browser_origins={_https_origin(arguments.url)}
        )
        mode = arguments.mode or self._prefs().mode
        session = BrowserSessionConfiguration(
            mode="cdp" if mode == "chrome" else mode,
            cdp_endpoint="chrome" if mode == "chrome" else None,
            remote_content_allowed=True,
            selected_tab_url=arguments.url if mode in {"extension", "chrome"} else None,
            visible=mode == "isolated",
        )
        return McpServerConfiguration.model_validate(
            {
                **self._base.model_dump(),
                "integration": scope,
                "browser_session": session,
                "arguments": (self._base.arguments[0], *browser_launch_arguments(scope, session)),
                "approved_tools": self._pins,
                "enabled": True,
            }
        )

    async def _expire(self, task: _Task) -> None:
        try:
            while True:
                remaining = task.expires - self._clock()
                if remaining <= 0 or task.revoked or self._closed:
                    break
                try:
                    prefs = self._prefs()
                    if not prefs.enabled or prefs.mode != task.preference_mode:
                        break
                except (ValueError, OSError, TypeError, AttributeError):
                    break
                await asyncio.sleep(min(1.0, remaining))
            await self._drop(task)
        except asyncio.CancelledError:
            raise

    def _schedule_drop(self, task: _Task) -> None:
        if task.cleanup_scheduled:
            return
        task.revoked = True
        if task.session is not None:
            task.session.invalidate()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task.cleanup_scheduled = True
        cleanup = loop.create_task(self._drop(task))
        self._cleanup.add(cleanup)
        cleanup.add_done_callback(self._cleanup.discard)

    async def _drop(self, task: _Task) -> None:
        task.revoked = True
        if task.session is not None:
            task.session.invalidate()
        for identifier, candidate in tuple(self._tasks.items()):
            if candidate is task:
                self._tasks.pop(identifier, None)
        if task.timer is not None and task.timer is not asyncio.current_task():
            task.timer.cancel()
        if task.client is not None:
            if task.close_task is None:
                task.close_task = asyncio.create_task(task.client.close())
            try:
                await asyncio.shield(task.close_task)
            except Exception:
                pass

    async def finish_request(self, request_id: UUID) -> None:
        self._prune_retained()
        owned = any(task.request_id == request_id for task in self._tasks.values())
        attempted = any(key[0] == request_id for key in self._open_calls)
        if owned or attempted:
            self._finished[request_id] = self._clock() + self.TTL_SECONDS
        self._open_calls = {
            key: deadline for key, deadline in self._open_calls.items() if key[0] != request_id
        }
        self._approved_origins.pop(request_id, None)
        await asyncio.gather(
            *(
                self._drop(task)
                for task in tuple(self._tasks.values())
                if task.request_id == request_id
            )
        )

    async def close(self) -> None:
        self._closed = True
        await asyncio.gather(*(self._drop(task) for task in tuple(self._tasks.values())))
        if self._cleanup:
            await asyncio.gather(*tuple(self._cleanup), return_exceptions=True)

    async def _handle(
        self, authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        task: _Task | None = None
        dispatched = False
        try:
            self._prune_retained()
            if authorization.decision is not PolicyDecision.ALLOW:
                raise PolicyViolation("browser_task_authorization_required")
            request_id, _ = self._trusted(context)
            if request_id in self._finished:
                raise PolicyViolation("browser_task_request_finished")
            if authorization.tool_name == "browser_task_open":
                if authorization.reason_code != "confirmation_consumed":
                    raise PolicyViolation("confirmation_not_consumed")
                args = BrowserTaskOpenArguments.model_validate(authorization.normalized_arguments)
                args = self._open_guard(args, context)
                if (request_id, authorization.call_id) in self._open_calls:
                    raise PolicyViolation("browser_task_open_already_consumed")
                if len(self._tasks) >= self.MAX_SESSIONS:
                    raise PolicyViolation("browser_task_capacity")
                if len(self._open_calls) + len(self._finished) >= self.MAX_PENDING_CALLS:
                    raise PolicyViolation("browser_task_pending_capacity")
                self._open_calls[(request_id, authorization.call_id)] = (
                    self._clock() + self.TTL_SECONDS
                )
                self._approved_origins.setdefault(request_id, {})[_https_origin(args.url)] = (
                    self._clock() + self.TTL_SECONDS
                )
                configuration = self._configuration(args)
                task = _Task(
                    request_id,
                    args.objective,
                    configuration,
                    self._prefs().mode,
                    self._clock() + self.TTL_SECONDS,
                )
                reservation = str(uuid4())
                self._tasks[reservation] = task
                task.timer = asyncio.create_task(self._expire(task))
                task.client = self._factory(configuration)
                tools = await task.client.start()
                bindings = McpHost._admit_tools(configuration, tools, set())
                task.schemas = {binding.remote_name: binding.input_schema for binding in bindings}
                if set(task.schemas) != set(_OPERATIONS.values()):
                    raise BrowserSessionError("browser_task_schema_not_admitted")

                async def bootstrap_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                    nonlocal dispatched
                    if not self._valid(task, context):
                        raise BrowserSessionError("browser_session_revoked")
                    payload = arguments
                    if name == "browser_tabs":
                        # Local bootstrap cannot have a snapshot binding yet; its
                        # only arguments are constructed here from the approved URL.
                        payload = {
                            **arguments,
                            "session_id": reservation,
                            "expected_url": args.url,
                            "snapshot_sha256": "0" * 64,
                        }
                        if arguments.get("action") == "select":
                            payload["url"] = args.url
                    Draft202012Validator(task.schemas[name]).validate(payload)
                    if name == "browser_tabs" and arguments.get("action") != "list":
                        dispatched = True
                    result = await task.client.call_tool(name, arguments)
                    if (
                        args.mode == "chrome"
                        and result.get("isError") is True
                        and name == "browser_tabs"
                    ):
                        content = result.get("content", [])
                        if isinstance(content, list) and any(
                            isinstance(block, dict)
                            and block.get("type") == "text"
                            and isinstance(block.get("text"), str)
                            and len(block["text"].encode()) <= 32768
                            and "Could not connect to chrome" in block["text"]
                            for block in content
                        ):
                            raise BrowserSessionError("browser_task_chrome_connection_failed")
                    return result

                task.session, result = await ReviewedBrowserSession.bootstrap_task(
                    configuration.integration,
                    configuration.browser_session,
                    args.url,
                    bootstrap_call,
                    still_available=lambda: self._valid(task, context),
                )
                await asyncio.gather(
                    *(
                        self._drop(previous)
                        for previous in tuple(self._tasks.values())
                        if previous is not task and previous.request_id == request_id
                    )
                )
                if not self._valid(task, context):
                    raise BrowserSessionError("browser_session_revoked")
                self._tasks.pop(reservation, None)
                self._tasks[task.session.session_id] = task
                result["structuredContent"].update(
                    goal_verified=False, verification_scope="browser_task_access"
                )
            elif authorization.tool_name in {"browser_task_action", "browser_task_close"}:
                model = (
                    BrowserTaskActionArguments
                    if authorization.tool_name == "browser_task_action"
                    else BrowserTaskCloseArguments
                )
                args = model.model_validate(authorization.normalized_arguments)
                candidate = self._tasks.get(str(args.session_id))
                if candidate is not None and candidate.request_id == request_id:
                    task = candidate
                self._action_guard(args, context)
                if authorization.tool_name == "browser_task_close":
                    await self._drop(task)
                    return self._result(
                        authorization,
                        {
                            "structuredContent": {
                                "closed": True,
                                "session_id": str(args.session_id),
                                "verified": True,
                                "goal_verified": False,
                                "verification_scope": "browser_task_disconnect",
                            },
                            "isError": False,
                        },
                    )
                async with task.lock:
                    self._action_guard(args, context)
                    task.actions += 1
                    assert task.session is not None and task.client is not None

                    async def action_call(
                        name: str, arguments: Mapping[str, Any]
                    ) -> dict[str, Any]:
                        nonlocal dispatched
                        if name not in {"browser_snapshot", "browser_tabs"} or (
                            name == "browser_tabs" and arguments.get("action") != "list"
                        ):
                            dispatched = True
                        return await task.client.call_tool(name, arguments)

                    result = await task.session.execute(
                        _OPERATIONS[args.operation],
                        args.remote_arguments(),
                        action_call,
                        still_available=lambda: self._valid(task, context),
                    )
                    if args.operation == "snapshot" or (
                        args.operation == "tabs" and args.tab_action == "list"
                    ):
                        result["structuredContent"]["goal_verified"] = False
                    if result.get("isError") or not result["structuredContent"].get("verified"):
                        await self._drop(task)
            else:
                raise PolicyViolation("browser_task_unknown_tool")
            data = result["structuredContent"]
            # Listing all tabs is a local binding check, not data authorized for
            # transmission. Keep only the selected page in the result/context.
            data.pop("tabs", None)
            data.update(
                task_request_id=str(request_id),
                remaining_actions=self.MAX_ACTIONS - task.actions,
                expires_in_seconds=max(0, int(task.expires - self._clock())),
                scope="browser_task",
            )
            if self._valid(task, context):
                task.snapshot = copy.deepcopy(data)
            return self._result(authorization, result)
        except asyncio.CancelledError:
            if task is not None:
                await asyncio.shield(self._drop(task))
            raise
        except Exception as error:
            if task is not None:
                await self._drop(task)
            code = str(error) if isinstance(error, (PolicyViolation, BrowserSessionError)) else ""
            if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", code):
                code = "browser_task_execution_failed"
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=authorization.tool_name,
                success=False,
                error_code=code,
                metadata={
                    "verified": False,
                    "goal_verified": False,
                    "action_dispatched": dispatched,
                },
            )

    @staticmethod
    def _result(authorization: ToolAuthorization, result: dict[str, Any]) -> ToolExecutionResult:
        data = result.get("structuredContent", {})
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=result.get("isError") is not True,
            output=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            error_code="browser_task_action_unverified" if result.get("isError") else None,
            metadata={
                "source": "browser_task",
                "verified": data.get("verified") is True,
                "goal_verified": data.get("goal_verified") is True,
                "action_performed": data.get("action_executed") is True,
                "verification_scope": data.get("verification_scope", "unverified"),
                "outcome": data.get("outcome", "closed" if data.get("closed") else "unverified"),
            },
        )
