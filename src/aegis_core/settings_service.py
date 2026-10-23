"""Owner-facing settings over authenticated local IPC, separate from agent tools."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from aegis_core.async_tasks import run_blocking_owned
from aegis_core.browser_preferences import (
    BrowserAutomationMode,
    BrowserAutomationPreferences,
    browser_automation_path,
    load_browser_automation_preferences,
    save_browser_automation_preferences,
)
from aegis_core.config import Settings
from aegis_core.failure_boundary import record_boundary_error
from aegis_core.google_account_settings import (
    GoogleAccountAuthorization,
    probe_google_account,
    revoke_google_account,
)
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.mcp.client import (
    McpHostConfiguration,
    McpServerConfiguration,
    _validate_executable,
    load_mcp_configuration,
    validate_integration_process,
)
from aegis_core.mcp.discovery import discover_configuration
from aegis_core.mcp.host_manager import McpHostManager
from aegis_core.mcp.integrations import BrowserSessionConfiguration, IntegrationId, IntegrationScope
from aegis_core.mcp.setup import prepare_browser_profile
from aegis_core.secrets import MacOSKeychain
from aegis_core.settings_store import (
    ModelPreferences,
    SelectableModel,
    default_model_preferences,
    load_model_preferences,
    write_private_json,
)


class ServerChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    enabled: bool = Field(default=False, strict=True)
    confirm: bool = Field(default=False, strict=True)


class GoogleConnect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    email: str = Field(min_length=3, max_length=254)
    confirm: bool = Field(strict=True)


class BrowserPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origins: frozenset[str] = Field(min_length=1, max_length=16)
    session: BrowserSessionConfiguration
    confirm: bool = Field(default=False, strict=True)


class BrowserAutomationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)
    mode: BrowserAutomationMode
    confirm: bool = Field(default=False, strict=True)


class SettingsIpcService:
    METHODS = frozenset(
        {
            "settings.status",
            "settings.models.save",
            "settings.models.test",
            "settings.credential.set",
            "settings.mcp.set_enabled",
            "settings.mcp.remove",
            "settings.mcp.import",
            "settings.browser.prepare",
            "settings.browser.save",
            "settings.google.connect",
            "settings.google.disconnect",
            "settings.google.test",
            "settings.operation",
            "settings.cancel",
        }
    )

    def __init__(
        self, settings: Settings, host: McpHostManager, keychain: MacOSKeychain | None,
    ) -> None:
        self.settings = settings
        self.host = host
        self.keychain = keychain
        self.restart_required = False
        self.operation: dict = {"state": "idle"}
        self.task: asyncio.Task | None = None

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def close(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def _configuration(self) -> McpHostConfiguration:
        return load_mcp_configuration(self.settings.mcp_configuration_path)

    def _server(self, identifier: str) -> McpServerConfiguration:
        return next(s for s in self._configuration().servers if s.server_id == identifier)

    def _save_server(self, server: McpServerConfiguration | None, identifier: str) -> None:
        current = self._configuration()
        servers = tuple(s for s in current.servers if s.server_id != identifier)
        if server is not None:
            servers += (server,)
        updated = McpHostConfiguration(servers=servers)
        write_private_json(self.settings.mcp_configuration_path, updated.model_dump(mode="json"))
        self.restart_required = True

    @staticmethod
    def _admit(server: McpServerConfiguration) -> None:
        if not server.approved_tools or (
            not server.application_bundle_ids and not server.allow_explicit_requests
        ):
            raise ValueError("Reviewed tools and application scope are required")
        if server.integration is not None and server.integration.blockers():
            raise ValueError("Integration scope is incomplete")
        if server.home_assistant is None:
            _validate_executable(server)
            validate_integration_process(server)

    async def status(self) -> dict:
        valid = True
        try:
            preferences = load_model_preferences(self.settings.model_preferences_path)
        except (OSError, ValueError):
            preferences, valid = default_model_preferences(), False
        try:
            configured = (
                await asyncio.to_thread(self.keychain.is_configured)
                if self.keychain is not None else False
            )
            credential = "configured" if configured else "missing"
        except Exception as error:
            record_boundary_error(error)
            credential = "unavailable"
        runtime = self.host.status()
        states = {s["server_id"]: s for s in runtime.get("servers", [])}
        servers = [
            {
                "server_id": s.server_id,
                "enabled": s.enabled,
                "provider": s.integration.provider.value if s.integration else "custom",
                "accounts": sorted(s.integration.google_accounts) if s.integration else [],
                "approved_tools": len(s.approved_tools),
                "state": states.get(s.server_id, {}).get("state", "disabled"),
                "available_tools": states.get(s.server_id, {}).get("available_tools", 0),
                "google_configured": bool(s.environment.get("GOOGLE_OAUTH_CLIENT_ID")),
                "browser_mode": s.browser_session.mode if s.browser_session else None,
                "browser_origins": sorted(s.integration.browser_origins) if s.integration else [],
                "browser_remote_content_allowed": (
                    s.browser_session.remote_content_allowed if s.browser_session else False
                ),
            }
            for s in self._configuration().servers
        ]
        try:
            browser = load_browser_automation_preferences(browser_automation_path(self.settings))
            browser_status = {
                "enabled": browser.enabled,
                "mode": browser.mode,
                "connection": "ready" if browser.enabled else "disabled",
            }
        except (OSError, ValueError):
            browser_status = {"enabled": False, "mode": "extension", "connection": "invalid"}
        return {
            "models": preferences.model_dump(mode="json"),
            "models_valid": valid,
            "credential": credential,
            "servers": servers,
            "restart_required": self.restart_required,
            "memory_namespace": self.settings.memory_rag_namespace,
            "browser_automation": browser_status,
        }

    def _launch_operation(self, work) -> dict:
        if self.task is not None and not self.task.done():
            raise ValueError("An operation is already running")
        operation = {"id": str(uuid4()), "state": "starting"}
        self.operation = operation

        async def run() -> None:
            try:
                await work(operation)
            except asyncio.CancelledError:
                operation.update(state="cancelled")
                raise
            except Exception as error:
                record_boundary_error(error)
                operation.update(state="error", reason="settings_operation_failed")

        self.task = asyncio.create_task(run())
        return dict(self.operation)

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            method, payload = request.method, request.payload
            if method not in self.METHODS:
                return IpcHandlerResult(ok=False, error_code="method_not_found")
            if self.keychain is None and method in {
                "settings.models.save", "settings.models.test", "settings.credential.set",
            }:
                return IpcHandlerResult(ok=False, error_code="inference_provider_changed")
            if (
                self.task is not None
                and not self.task.done()
                and method not in {"settings.status", "settings.operation", "settings.cancel"}
            ):
                return IpcHandlerResult(ok=False, error_code="settings_operation_busy")
            if method in {"settings.status", "settings.operation", "settings.cancel"}:
                if payload:
                    raise ValueError("Unexpected payload")
                if method == "settings.status":
                    result = await self.status()
                elif method == "settings.operation":
                    result = dict(self.operation)
                else:
                    await self.close()
                    result = {"state": "cancelled"}
            elif method == "settings.models.save":
                preferences = ModelPreferences.model_validate(payload)
                write_private_json(
                    self.settings.model_preferences_path, preferences.model_dump(mode="json")
                )
                self.restart_required = True
                result = {"restart_required": True}
            elif method == "settings.models.test":
                model = SelectableModel.model_validate(payload)
                result = self._launch_operation(lambda progress: self._test_model(model, progress))
            elif method == "settings.credential.set":
                if set(payload) != {"secret"} or not isinstance(payload["secret"], str):
                    raise ValueError("Invalid credential payload")
                # Move Keychain work off the IPC handler; never echo the value or exception.
                secret = payload["secret"]

                async def save_secret(progress: dict) -> None:
                    await run_blocking_owned(self.keychain.set, secret)
                    progress.update(state="done")

                result = self._launch_operation(save_secret)
            elif method in {
                "settings.mcp.set_enabled",
                "settings.mcp.remove",
                "settings.google.disconnect",
            }:
                change = ServerChange.model_validate(payload)
                server = self._server(change.server_id)
                if method != "settings.mcp.set_enabled" or change.enabled:
                    if not change.confirm:
                        raise ValueError("Explicit confirmation required")
                if method == "settings.mcp.set_enabled":
                    if change.enabled:
                        if (
                            server.browser_session is not None
                            and not server.browser_session.remote_content_allowed
                        ):
                            raise ValueError("Browser remote content consent is required")
                        self._admit(server)
                    self._save_server(
                        server.model_copy(update={"enabled": change.enabled}), server.server_id
                    )
                    if not change.enabled:
                        result = self._launch_operation(
                            lambda progress: self._suspend(server, progress)
                        )
                    else:
                        result = {"restart_required": True}
                elif method == "settings.mcp.remove":
                    self._save_server(None, server.server_id)
                    result = self._launch_operation(
                        lambda progress: self._suspend(server, progress)
                    )
                else:
                    if (
                        not server.integration
                        or server.integration.provider != IntegrationId.GOOGLE
                    ):
                        raise ValueError("Not a Google integration")
                    self._save_server(
                        server.model_copy(update={"enabled": False}), server.server_id
                    )

                    async def disconnect(progress: dict) -> None:
                        await self.host.suspend_for_settings(server.server_id)
                        for account in server.integration.google_accounts:
                            await revoke_google_account(server, account)
                        progress.update(state="done", restart_required=True)

                    result = self._launch_operation(disconnect)
            elif method == "settings.mcp.import":
                if set(payload) != {"path"} or not isinstance(payload["path"], str):
                    raise ValueError("Invalid import")
                path = Path(payload["path"])
                if not path.is_absolute():
                    raise ValueError("Absolute path required")
                imported = load_mcp_configuration(path)
                current = self._configuration()
                if not imported.servers:
                    raise ValueError("Empty import")
                ids = {s.server_id for s in current.servers}
                if any(s.server_id in ids for s in imported.servers):
                    raise ValueError("Duplicate integration")
                for server in imported.servers:
                    self._admit(server)
                updated = McpHostConfiguration(
                    servers=current.servers
                    + tuple(s.model_copy(update={"enabled": False}) for s in imported.servers)
                )
                write_private_json(
                    self.settings.mcp_configuration_path, updated.model_dump(mode="json")
                )
                self.restart_required = True
                result = {"restart_required": True}
            elif method == "settings.browser.save":
                browser_change = BrowserAutomationChange.model_validate(payload)
                if not browser_change.confirm:
                    raise ValueError("Explicit browser preference confirmation required")
                browser = BrowserAutomationPreferences(
                    enabled=browser_change.enabled, mode=browser_change.mode
                )
                save_browser_automation_preferences(browser_automation_path(self.settings), browser)
                result = {
                    "enabled": browser.enabled,
                    "mode": browser.mode,
                    "connection": "ready" if browser.enabled else "disabled",
                    "restart_required": False,
                }
            elif method == "settings.browser.prepare":
                prepare = BrowserPrepare.model_validate(payload)
                if not prepare.confirm:
                    raise ValueError("Explicit browser profile confirmation required")
                scope = IntegrationScope(
                    provider=IntegrationId.PLAYWRIGHT, browser_origins=prepare.origins
                )
                # Validate the reviewed URL against the exact origins before starting a peer.
                if prepare.session.selected_tab_url is not None:
                    from aegis_core.mcp.integrations import _https_origin

                    if _https_origin(prepare.session.selected_tab_url) not in scope.browser_origins:
                        raise ValueError("Browser tab is outside reviewed origins")
                result = self._launch_operation(
                    lambda progress: self._prepare_browser(prepare, progress)
                )
            elif method == "settings.google.test":
                if set(payload) != {"server_id"}:
                    raise ValueError("Invalid Google connection test")
                server = self._server(payload["server_id"])
                if not server.integration or server.integration.provider != IntegrationId.GOOGLE:
                    raise ValueError("Not a Google integration")

                async def probe(progress: dict) -> None:
                    for account in server.integration.google_accounts:
                        await probe_google_account(server, account)
                    progress.update(state="done")

                result = self._launch_operation(probe)
            elif method == "settings.google.connect":
                connect = GoogleConnect.model_validate(payload)
                if not connect.confirm:
                    raise ValueError("Explicit confirmation required")
                server = self._server(connect.server_id)
                if not server.integration or server.integration.provider != IntegrationId.GOOGLE:
                    raise ValueError("Not a Google integration")
                email = connect.email.strip().lower()
                IntegrationScope(provider=IntegrationId.GOOGLE, google_accounts={email})

                def commit(account: str) -> None:
                    current = self._server(server.server_id)
                    updated = current.model_copy(
                        update={
                            "integration": current.integration.model_copy(
                                update={"google_accounts": frozenset({account})}
                            ),
                            "enabled": True,
                        }
                    )
                    self._save_server(updated, server.server_id)

                async def authorize(progress: dict) -> None:
                    self.restart_required = True
                    await self.host.suspend_for_settings(server.server_id)
                    await GoogleAccountAuthorization(server, email, progress, commit).run()

                result = self._launch_operation(authorize)
            else:
                raise ValueError("Unsupported settings action")
        except (ValueError, OSError, StopIteration, KeyError):
            return IpcHandlerResult(ok=False, error_code="settings_invalid_change")
        except Exception as error:
            record_boundary_error(error)
            return IpcHandlerResult(ok=False, error_code="settings_unavailable")
        return IpcHandlerResult(ok=True, payload=result)

    async def _suspend(self, server: McpServerConfiguration, progress: dict) -> None:
        await self.host.suspend_for_settings(server.server_id)
        progress.update(state="done", restart_required=True)

    async def _prepare_browser(self, prepare: BrowserPrepare, progress: dict) -> None:
        """Review pinned schemas only; this operation cannot access any browser page."""
        parent = self.settings.mcp_configuration_path.parent / "browser-profiles"
        if parent.is_symlink() or parent.parent.resolve(strict=True) != parent.parent:
            raise ValueError("Invalid browser profiles directory")
        parent.mkdir(mode=0o700, exist_ok=True)
        if len(tuple(parent.iterdir())) >= 64:
            progress.update(state="error", reason="browser_profile_capacity")
            return
        directory = parent / str(uuid4())
        saved = False
        try:
            prepared = await run_blocking_owned(
                prepare_browser_profile,
                directory,
                packages=self.settings.mcp_node_packages_path,
                origins=prepare.origins,
                session=prepare.session,
                node=self.settings.mcp_node_executable_path,
            )
            progress.update(state="reviewing_browser_tools")
            discovered = await discover_configuration(Path(prepared["candidate_path"]))
            hashes = discovered["candidate_schema_sha256"]
            if not {"browser_snapshot", "browser_tabs"} <= hashes.keys():
                progress.update(state="error", reason="browser_schema_incompatible")
                return
            server = McpServerConfiguration.model_validate(
                {
                    **prepared["configuration"],
                    "approved_tools": hashes,
                    "enabled": False,
                }
            )
            self._admit(server)
            existing = next(
                (
                    item
                    for item in self._configuration().servers
                    if item.server_id == server.server_id
                ),
                None,
            )
            if existing is not None and (
                existing.integration is None
                or existing.integration.provider is not IntegrationId.PLAYWRIGHT
            ):
                raise ValueError("Browser integration identifier is already in use")
            if existing is not None:
                await self.host.suspend_for_settings(server.server_id)
            write_private_json(
                Path(prepared["candidate_path"]),
                McpHostConfiguration(servers=(server,)).model_dump(mode="json"),
            )
            self._save_server(server, server.server_id)
            saved = True
            progress.update(
                state="done",
                restart_required=True,
                tools_called=0,
                connected=False,
                reviewed_tools=len(hashes),
                requires_tab_selection=prepared["requires_tab_selection"],
            )
        except (OSError, ValueError):
            progress.update(state="error", reason="browser_dependencies_unavailable")
        finally:
            if not saved and directory.is_dir() and not directory.is_symlink():
                await run_blocking_owned(shutil.rmtree, directory)

    async def _test_model(self, model: SelectableModel, progress: dict) -> None:
        key = await asyncio.to_thread(self.keychain.get)
        content: object = "Responde OK. Esta es una prueba sintética de conexión."
        if model.images:
            pixel = (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
                "/x8AAwMCAO+aP1sAAAAASUVORK5CYII="
            )
            content = [
                {"type": "text", "text": str(content)},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{pixel}"}},
            ]
        body = {
            "model": model.model_id,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 64,
            "stream": False,
        }
        if model.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": "connection_test",
                        "description": "Confirma la conexión de prueba.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    },
                }
            ]
            body["tool_choice"] = {"type": "function", "function": {"name": "connection_test"}}
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            response = await client.post(
                str(self.settings.nvidia_base_url).rstrip("/") + "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}"},
            )
            if response.status_code != 200:
                progress.update(state="error", reason=f"model_http_{response.status_code}")
                return
            choices = response.json().get("choices", [])
            if not choices or (model.tools and not choices[0].get("message", {}).get("tool_calls")):
                progress.update(state="error", reason="model_incompatible_response")
                return
        progress.update(state="done", model_id=model.model_id)
