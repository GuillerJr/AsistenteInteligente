"""Signed-app settings for the selected AI connection, without exposing credentials."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from aegis_core.contracts import AgentRole
from aegis_core.failure_boundary import record_boundary_error
from aegis_core.inference_settings import (
    InferencePreferences,
    load_inference_preferences,
    save_inference_preferences,
)
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler

_STATES = frozenset({"ready", "signed_out", "login_pending", "missing", "unavailable"})


class InferenceIpcService:
    METHODS = frozenset({
        "settings.inference.status", "settings.inference.connect", "settings.inference.cancel",
        "settings.inference.disconnect", "settings.inference.save", "settings.inference.test",
    })

    def __init__(
        self, provider: Any, preferences_path: Path,
        *, jobs_busy: Callable[[], bool] = lambda: False,
    ) -> None:
        self.provider = provider
        self.preferences_path = preferences_path
        self.task: asyncio.Task | None = None
        self._mutation = asyncio.Lock()
        self.jobs_busy = jobs_busy
        self._last_test: dict[str, str | int | None] = {
            "state": "idle", "error_code": None, "latency_ms": None,
        }

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def close(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def status_payload(self) -> dict:
        try:
            async with asyncio.timeout(5):
                status = await self.provider.status()
            preferences = load_inference_preferences(self.preferences_path)
            state = status.get("state")
            if state not in _STATES or type(status.get("installed")) is not bool:
                raise ValueError("invalid_inference_status")
            models = []
            for model in status.get("models", []):
                if (
                    not isinstance(model, dict)
                    or not isinstance(model.get("id"), str)
                    or not isinstance(model.get("display_name"), str)
                    or type(model.get("images")) is not bool
                    or type(model.get("is_default")) is not bool
                ):
                    raise ValueError("invalid_inference_catalogue")
                # Only public model metadata crosses IPC, never account or OAuth fields.
                models.append({key: model[key] for key in (
                    "id", "display_name", "images", "is_default",
                )})
            selected = preferences.model_id or status.get("model_id")
            if selected is not None and not isinstance(selected, str):
                raise ValueError("invalid_inference_selection")
            public = {
                "provider": "codex", "state": state, "installed": status["installed"],
                "model_id": selected, "models": models,
            }
        except Exception as error:
            record_boundary_error(error)
            public = {
                "provider": "codex", "state": "unavailable", "installed": False,
                "model_id": None, "models": [],
            }
        return {**public, "last_test": dict(self._last_test)}

    async def provider_status_payload(self) -> dict[str, str]:
        status = await self.status_payload()
        state = status["state"]
        credential = (
            "configured" if state == "ready" else "unavailable"
            if state in {"missing", "unavailable"} else "missing"
        )
        return {"provider": "codex", "credential": credential, "voice_output": "unconfigured"}

    @staticmethod
    def _auth_url(value: Any) -> str:
        if not isinstance(value, str) or len(value) > 16_384:
            raise ValueError("invalid_login_url")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"auth.openai.com", "chatgpt.com"}
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in {None, 443} or parsed.fragment
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("invalid_login_url")
        return value

    async def _test(self) -> None:
        started = time.monotonic()
        try:
            async with asyncio.timeout(24):
                status = await self.provider.status()
                if status.get("state") != "ready":
                    self._last_test.update(state="failed", error_code="inference_not_connected")
                    return
                result = await self.provider.complete(
                    role=AgentRole.SYNTHESIZER,
                    messages=[{"role": "user", "content": "Responde únicamente con el número 4."}],
                    max_tokens=64, temperature=0.0, timeout_seconds=20,
                )
                if (
                    result.tool_calls or result.content.strip() != "4"
                    or result.finish_reason in {"length", "content_filter"}
                ):
                    raise ValueError("invalid_test_response")
                self._last_test.update(state="passed", error_code=None)
        except asyncio.CancelledError:
            self._last_test.update(state="cancelled", error_code=None)
            raise
        except TimeoutError:
            self._last_test.update(state="failed", error_code="inference_timeout")
        except Exception as error:
            record_boundary_error(error)
            self._last_test.update(state="failed", error_code="inference_test_failed")
        finally:
            self._last_test["latency_ms"] = int((time.monotonic() - started) * 1000)

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method not in self.METHODS:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        payload = request.payload
        try:
            if request.method == "settings.inference.status":
                if payload:
                    raise ValueError("invalid_payload")
                return IpcHandlerResult(ok=True, payload=await self.status_payload())
            if self._mutation.locked() and request.method != "settings.inference.cancel":
                return IpcHandlerResult(ok=False, error_code="inference_operation_busy")
            async with self._mutation:
                if request.method != "settings.inference.cancel" and self.jobs_busy():
                    return IpcHandlerResult(ok=False, error_code="inference_operation_busy")
                if request.method == "settings.inference.cancel":
                    if payload:
                        raise ValueError("invalid_payload")
                    await self.close()
                    async with asyncio.timeout(5):
                        await self.provider.login_cancel()
                elif self.task is not None and not self.task.done():
                    return IpcHandlerResult(ok=False, error_code="inference_operation_busy")
                elif request.method == "settings.inference.connect":
                    if set(payload) != {"confirm"} or payload["confirm"] is not True:
                        raise ValueError("invalid_payload")
                    async with asyncio.timeout(5):
                        receipt = await self.provider.login_start()
                    auth_url = self._auth_url(receipt.get("authUrl"))
                    return IpcHandlerResult(ok=True, payload={
                        **await self.status_payload(), "authUrl": auth_url,
                    })
                elif request.method == "settings.inference.disconnect":
                    if set(payload) != {"confirm"} or payload["confirm"] is not True:
                        raise ValueError("invalid_payload")
                    async with asyncio.timeout(5):
                        await self.provider.logout()
                    self._last_test.update(state="idle", error_code=None, latency_ms=None)
                elif request.method == "settings.inference.save":
                    if set(payload) != {"model_id"}:
                        raise ValueError("invalid_payload")
                    preferences = InferencePreferences(model_id=payload["model_id"])
                    async with asyncio.timeout(5):
                        models = await self.provider.list_models()
                    if preferences.model_id is not None and not any(
                        item.get("id") == preferences.model_id and item.get("images") is True
                        for item in models
                    ):
                        return IpcHandlerResult(ok=False, error_code="inference_model_incompatible")
                    if self.jobs_busy():
                        return IpcHandlerResult(ok=False, error_code="inference_operation_busy")
                    save_inference_preferences(self.preferences_path, preferences)
                    self._last_test.update(state="idle", error_code=None, latency_ms=None)
                elif request.method == "settings.inference.test":
                    if payload:
                        raise ValueError("invalid_payload")
                    self._last_test.update(state="running", error_code=None, latency_ms=None)
                    self.task = asyncio.create_task(self._test())
                return IpcHandlerResult(ok=True, payload=await self.status_payload())
        except (ValueError, ValidationError):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except TimeoutError:
            return IpcHandlerResult(ok=False, error_code="inference_timeout")
        except Exception as error:
            record_boundary_error(error)
            return IpcHandlerResult(ok=False, error_code="inference_unavailable")
