"""Local Jarvis coordinator and explicit development service entry point.

This connects actual IPC, SQLite, execution lifecycle and the official Codex
client. It is the first vertical baseline slice, not the complete OpenClaw parity
target. No legacy owner state, tools, credentials or installed app are imported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from jarvis_core.codex import CodexClient, CodexError
from jarvis_core.protocol import LocalRpcServer, RpcError, TransportSecurityError
from jarvis_core.runtime import AgentRuntime, FinalAnswer, RunContext, RunHandle
from jarvis_core.state import SessionRecord, SessionRef, StateError, StateLimits, StateStore

_BASE = {"session_id", "temporary"}
_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def _params(params: dict, allowed: set[str], required: set[str] | None = None) -> None:
    if not isinstance(params, dict) or set(params) - allowed or (required or set()) - set(params):
        raise RpcError(-32602, "Los parámetros de la solicitud no son válidos.")


def _session(record: SessionRecord) -> dict:
    return {"session_id": record.ref.session_id, "temporary": record.ref.temporary,
            "title": record.title, "archived": record.archived}


class _ProviderAdapter:
    def __init__(self, service: JarvisService) -> None:
        self.service = service

    async def next_step(self, context: RunContext) -> FinalAnswer:
        service = self.service
        ref = service._refs[context.run_id]
        service.store.set_run_status(ref, context.run_id, "running")
        record = service.store.get_session(ref)
        try:
            result = await service.provider.chat(
                context.message, thread_id=record.provider_thread_id, temporary=ref.temporary,
                on_thread=lambda value: service.store.set_provider_thread(ref, value),
                on_event=lambda kind, payload: service.store.append_event(
                    ref, context.run_id, kind, payload,
                ),
            )
        except CodexError as error:
            service._errors[context.run_id] = str(error)
            raise
        return FinalAnswer(result["answer"])


class JarvisService:
    def __init__(self, directory: Path, provider: CodexClient) -> None:
        self.directory = directory
        self.store = StateStore(directory / "jarvis.sqlite3",
                                limits=StateLimits(max_json_bytes=512000))
        self.provider = provider
        self.runtime = AgentRuntime(_ProviderAdapter(self))
        self._handles: dict[str, RunHandle] = {}
        self._refs: dict[str, SessionRef] = {}
        self._errors: dict[str, str] = {}
        self._observers: set[asyncio.Task] = set()
        self._closing = False
        self._close_task: asyncio.Task | None = None
        self.server = LocalRpcServer(directory, self.handlers(), max_frame_bytes=1024 * 1024)

    def handlers(self) -> dict[str, Callable[[dict], Awaitable[dict]]]:
        methods = {
            "service.status": self.status, "provider.login": self.login,
            "provider.logout": self.logout, "sessions.create": self.create_session,
            "sessions.list": self.list_sessions, "sessions.update": self.update_session,
            "sessions.delete": self.delete_session, "sessions.messages": self.messages,
            "runs.start": self.start_run, "runs.get": self.get_run,
            "runs.cancel": self.cancel_run, "events.poll": self.events,
        }

        def safe(callback):
            async def invoke(params):
                try:
                    return await callback(params)
                except StateError as error:
                    raise RpcError(-32010, error.code) from None
                except CodexError as error:
                    raise RpcError(-32020, str(error)) from None
            return invoke

        return {name: safe(callback) for name, callback in methods.items()}

    def _ref(self, params: dict) -> SessionRef:
        # Mode is validated against a real session. No client metadata is trusted
        # as a replacement for that lookup or for a durable provider binding.
        ref = SessionRef(params.get("session_id"), params.get("temporary", False))
        self.store.get_session(ref)
        return ref

    def _busy(self, ref: SessionRef) -> bool:
        return any(value == ref for value in self._refs.values())

    async def status(self, params: dict) -> dict:
        _params(params, set())
        provider = await self.provider.account_status()
        return {"ready": not self._closing, "provider": provider,
                "capabilities": {"chat": True, "sessions": True, "temporary_sessions": True,
                                 "streaming": True, "cancellation": True,
                                 "tools": False, "baseline_complete": False}}

    async def login(self, params: dict) -> dict:
        _params(params, set())
        return await self.provider.login()

    async def logout(self, params: dict) -> dict:
        _params(params, set())
        if self._handles:
            raise RpcError(-32011, "Cancela las ejecuciones antes de cerrar la conexión.")
        return await self.provider.logout()

    async def create_session(self, params: dict) -> dict:
        _params(params, {"title", "temporary"})
        self._title(params.get("title", "Nueva conversación"))
        record = self.store.create_session(params.get("title", "Nueva conversación"),
                                           temporary=params.get("temporary", False))
        return {"session": _session(record)}

    async def list_sessions(self, params: dict) -> dict:
        _params(params, {"include_archived", "temporary", "offset"})
        archived = params.get("include_archived", False)
        temporary = params.get("temporary")
        if type(archived) is not bool or (temporary is not None and type(temporary) is not bool):
            raise RpcError(-32602, "La selección de sesiones no es válida.")
        offset = params.get("offset", 0)
        modes = (False, True) if temporary is None else (temporary,)
        records = [_session(record) for mode in modes
                   for record in self.store.list_sessions(
                       temporary=mode, include_archived=archived, limit=100, offset=offset,
                   )]
        return {"sessions": records, "next_offset": offset + 100,
                "has_more": any(len([r for r in records if r["temporary"] == mode]) == 100
                                for mode in (False, True))}

    @staticmethod
    def _title(title: str) -> None:
        if not isinstance(title, str) or not title.strip() or len(title.encode()) > 512:
            raise RpcError(-32602, "El título debe tener entre 1 y 512 bytes.")

    async def update_session(self, params: dict) -> dict:
        _params(params, _BASE | {"title", "archived"}, {"session_id"})
        ref = self._ref(params)
        if "title" in params:
            self._title(params["title"])
        if self._busy(ref):
            raise RpcError(-32011, "Espera a que termine la ejecución de esta conversación.")
        record = self.store.update_session(ref, title=params.get("title"),
                                           archived=params.get("archived"))
        return {"session": _session(record)}

    async def delete_session(self, params: dict) -> dict:
        _params(params, _BASE, {"session_id"})
        ref = self._ref(params)
        if self._busy(ref):
            raise RpcError(-32011, "Cancela y espera al cierre de la ejecución antes de borrar.")
        self.store.delete_session(ref)
        return {"deleted": True}

    async def messages(self, params: dict) -> dict:
        _params(params, _BASE | {"after", "runs_offset"}, {"session_id"})
        ref = self._ref(params)
        selected = self.store.list_messages(ref, after=params.get("after", 0), limit=50)
        records = []
        size = 0
        for record in selected:
            size += len(json.dumps(asdict(record)).encode())
            if size > 256000:
                break
            records.append(record)
        runs = self.store.list_runs(ref, limit=100, offset=params.get("runs_offset", 0))
        return {"messages": [asdict(record) for record in records],
                "next_after": records[-1].sequence if records else params.get("after", 0),
                "has_more": len(records) == 50 or len(records) < len(selected),
                "runs": [{"run_id": run.run_id, "status": run.status,
                          "outcome": run.outcome} for run in runs],
                "runs_next_offset": params.get("runs_offset", 0) + len(runs),
                "runs_has_more": len(runs) == 100}

    async def start_run(self, params: dict) -> dict:
        _params(params, _BASE | {"message", "request_id"}, {"session_id", "message"})
        if self._closing:
            raise RpcError(-32011, "Jarvis está cerrando el servicio.")
        ref = self._ref(params)
        message = params["message"]
        if (not isinstance(message, str) or not message.strip()
                or len(message.encode()) > 32000):
            raise RpcError(-32602, "El mensaje debe tener entre 1 y 32000 bytes.")
        request_id = params.get("request_id")
        if request_id is not None:
            if request_id in self._refs and self._refs[request_id] != ref:
                raise RpcError(-32012, "La identidad de solicitud ya está en uso.")
            try:
                previous = self.store.get_run(ref, request_id)
            except StateError as error:
                if error.code != "run_not_found":
                    raise
            else:
                original = next((m for m in self.store.get_run_messages(ref, request_id)
                                 if m.run_id == request_id and m.role == "user"), None)
                if original is None or original.text != message:
                    raise RpcError(-32012, "La solicitud ya corresponde a otro mensaje.")
                return {"run_id": previous.run_id, "status": previous.status}
        if self._busy(ref):
            raise RpcError(-32011, "Espera a que termine la ejecución de esta conversación.")
        record = self.store.create_run(ref, run_id=request_id, user_text=message)
        self._refs[record.run_id] = ref
        handle = self.runtime.start(ref.session_id, message, run_id=record.run_id)
        self._handles[handle.run_id] = handle
        observer = asyncio.create_task(self._settle(ref, handle))
        self._observers.add(observer)
        observer.add_done_callback(self._observers.discard)
        return {"run_id": handle.run_id, "status": record.status}

    async def _settle(self, ref: SessionRef, handle: RunHandle) -> None:
        result = await handle.await_settled()
        try:
            error = self._errors.pop(handle.run_id, result.error)
            receipt = {"answer": result.answer, "answer_source": result.answer_source,
                       "error": error, "action_receipts": []}
            self.store.finish_run(ref, result.run_id, status=result.status, receipt=receipt,
                                  assistant_text=result.answer,
                                  clear_provider_thread=result.status != "completed",
                                  outcome="known" if result.status == "completed" else "unknown")
        except StateError:
            # Do not forge a successful receipt if persistence failed. The active
            # record remains recoverable as interrupted on the next process start.
            self._closing = True
        finally:
            self._handles.pop(handle.run_id, None)
            self._refs.pop(handle.run_id, None)

    def _run_value(self, ref: SessionRef, run_id: str) -> dict:
        record = self.store.get_run(ref, run_id)
        receipt = json.loads(record.receipt_json) if record.receipt_json else {}
        return {"run_id": record.run_id, "status": record.status, "outcome": record.outcome,
                "answer": receipt.get("answer"), "error": receipt.get("error"),
                "answer_source": receipt.get("answer_source"), "action_receipts": []}

    async def get_run(self, params: dict) -> dict:
        _params(params, _BASE | {"run_id"}, {"session_id", "run_id"})
        return {"run": self._run_value(self._ref(params), params["run_id"])}

    async def cancel_run(self, params: dict) -> dict:
        _params(params, _BASE | {"run_id"}, {"session_id", "run_id"})
        ref = self._ref(params)
        record = self.store.get_run(ref, params["run_id"])
        handle = self._handles.get(record.run_id)
        accepted = handle.cancel().accepted if handle else False
        if accepted:
            self.store.set_run_status(ref, record.run_id, "cancelling")
        return {"run_id": record.run_id, "accepted": accepted}

    async def events(self, params: dict) -> dict:
        _params(params, _BASE | {"run_id", "after"}, {"session_id", "run_id"})
        ref = self._ref(params)
        records = self.store.list_events(ref, params["run_id"], after=params.get("after", 0),
                                         limit=50)
        return {"events": [{"sequence": record.sequence, "kind": record.kind,
                            "payload": json.loads(record.payload_json)} for record in records],
                "has_more": len(records) == 50}

    async def start(self) -> None:
        await self.server.start()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        self._closing = True
        transport_error = None
        try:
            await self.server.aclose()
        except Exception as error:
            transport_error = error
        for handle in tuple(self._handles.values()):
            handle.cancel()
        observers = tuple(self._observers)
        if observers:
            _, pending = await asyncio.wait(observers, timeout=25)
            if pending:
                # Stop the owned provider to release its pending turn, but never
                # close SQLite under a callback that has not actually settled.
                await self.provider.aclose()
                _, pending = await asyncio.wait(pending, timeout=3)
                if pending:
                    raise TimeoutError("service_cleanup_incomplete")
        await self.provider.aclose()
        self.store.close()
        if transport_error is not None:
            raise transport_error


async def _serve(args: argparse.Namespace) -> None:
    provider = CodexClient(args.codex, args.state / "provider")
    service = JarvisService(args.state, provider)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        await service.start()
        await stopped.wait()
    finally:
        await service.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Servicio local de la base Jarvis")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(_serve(args))
    except (StateError, CodexError, OSError, TransportSecurityError):
        parser.exit(1, "No se pudo iniciar o cerrar el servicio local de Jarvis.\n")


if __name__ == "__main__":
    main()
