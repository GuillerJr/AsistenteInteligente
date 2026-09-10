from __future__ import annotations

import asyncio
import os
import shlex
import signal
import stat
import sys
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO
from uuid import UUID

from pydantic import ValidationError

from aegis_core.engineering import (
    EngineeringDomain,
    EngineeringInferencePolicy,
    EngineeringIpcService,
    EngineeringResearchPolicy,
    EngineeringSubmitPayload,
)
from aegis_core.engineering_terminal import EngineeringTerminal, is_terminal
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import ProtocolError
from aegis_core.job_contracts import TERMINAL_STATUSES, JobSnapshot, JobStatus
from aegis_core.secrets import contains_likely_secret_material

if TYPE_CHECKING:
    from aegis_core.engineering_input import EngineeringInput


class EngineeringCLIError(RuntimeError):
    """Only a stable error code, never private transport/provider exception text."""


async def read_piped_request(stream: TextIO) -> str:
    """One bounded request, not a script of slash commands. No blocked input thread."""
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        try:
            return stream.read(50_001)
        except UnicodeError as error:
            raise EngineeringCLIError("invalid_input_encoding") from error
    if stat.S_ISREG(os.fstat(descriptor).st_mode):
        try:
            return stream.read(50_001)
        except UnicodeError as error:
            raise EngineeringCLIError("invalid_input_encoding") from error
    loop = asyncio.get_running_loop()
    result: asyncio.Future[str] = loop.create_future()
    data = bytearray()

    def ready() -> None:
        if result.done():
            return
        try:
            chunk = os.read(descriptor, 16_384)
            data.extend(chunk)
            if len(data) > 200_000:
                raise EngineeringCLIError("input_too_large")
            if not chunk:
                result.set_result(data.decode(stream.encoding or "utf-8"))
        except UnicodeError:
            result.set_exception(EngineeringCLIError("invalid_input_encoding"))
        except (OSError, EngineeringCLIError) as error:
            result.set_exception(error)

    loop.add_reader(descriptor, ready)
    try:
        return await result
    finally:
        loop.remove_reader(descriptor)


class EngineeringCLI:
    """One foreground job and one conversation. Transport failures never resubmit work."""

    def __init__(
        self,
        client: IpcClient,
        *,
        workspace: Path | None,
        domain: EngineeringDomain = EngineeringDomain.AUTO,
        research_policy: EngineeringResearchPolicy = EngineeringResearchPolicy.OFFLINE,
        inference_policy: EngineeringInferencePolicy = EngineeringInferencePolicy.NVIDIA_ONLY,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._client = client
        self._workspace = workspace.resolve(strict=True) if workspace is not None else None
        if self._workspace is not None and not self._workspace.is_dir():
            raise ValueError("engineering workspace must be a directory")
        self._domain = domain
        self._research_policy = research_policy
        self._inference_policy = inference_policy
        self._conversation_id: UUID | None = None
        self._stdin = stdin if stdin is not None else sys.stdin
        self._ui = EngineeringTerminal(
            stdout if stdout is not None else sys.stdout,
            stderr if stderr is not None else (stdout if stdout is not None else sys.stderr),
            interactive=False,
        )
        self._editor: EngineeringInput | None = None
        self._job: JobSnapshot | None = None
        self._rendered = ""
        self._response_started = False
        self._started_at = 0.0

    async def preflight(self, workspace: Path | None = None) -> None:
        requested = workspace if workspace is not None else self._workspace
        response = await self._call(
            EngineeringIpcService.PREFLIGHT_METHOD,
            {"workspace_path": str(requested)} if requested is not None else {},
        )
        raw = response.get("workspace_path")
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            raise EngineeringCLIError("engineering_preflight_invalid")
        authorized = Path(raw).resolve(strict=True)
        if not authorized.is_dir() or (requested is not None and authorized != requested):
            raise EngineeringCLIError("engineering_preflight_invalid")
        self._workspace = authorized

    async def run_once(self, text: str) -> int:
        async def operation() -> int:
            await self.preflight()
            return await self._submit_and_render(text)

        return await self._guarded(operation())

    async def run_interactive(self) -> int:
        self._ui = EngineeringTerminal(self._ui.stdout, self._ui.stderr, interactive=True)

        async def prepare() -> int:
            await self.preflight()
            return 0

        preparation_status = await self._guarded(prepare())
        if preparation_status != 0:
            return preparation_status
        if is_terminal(self._stdin):
            # The daemon and noninteractive clients never load the editor runtime.
            from aegis_core.engineering_input import EngineeringInput

            self._editor = EngineeringInput(self._stdin, self._ui.stderr, color=self._ui.color)
        self._render_header()
        try:
            while True:
                try:
                    if self._editor is not None:
                        text = await self._editor.read(self._domain.value)
                    else:
                        self._ui.stderr.write(f"◆ {self._domain.value} → ")
                        self._ui.stderr.flush()
                        # Production pipes use read_piped_request, terminals the async editor.
                        text = self._stdin.readline(50_002)
                        if not text:
                            raise EOFError
                    text = text.strip()
                except KeyboardInterrupt:
                    self._ui.notice("Borrador descartado. La sesión sigue activa.")
                    continue
                except EOFError:
                    break
                if not text:
                    continue
                if text.casefold() in {"/exit", "/quit"}:
                    break
                await self._guarded(self._handle_command(text))
            exit_status = 0
            if self._job is not None:
                exit_status = await self._guarded(self._cancel_current())
            self._ui.notice("Sesión cerrada.")
            return exit_status
        finally:
            self._ui.end_activity()
            if self._editor is not None:
                self._editor.close()

    async def _guarded(self, operation: Coroutine[Any, Any, int]) -> int:
        task = asyncio.create_task(operation, name="aegis-engineering-operation")
        loop = asyncio.get_running_loop()
        previous = signal.getsignal(signal.SIGINT)

        def interrupt() -> None:
            if not task.done() and not task.cancelling():
                task.cancel()

        loop.add_signal_handler(signal.SIGINT, interrupt)
        try:
            return await task
        except asyncio.CancelledError:
            await self._cancel_current()
            if (current := asyncio.current_task()) is not None and current.cancelling():
                raise
            return 130
        except EngineeringCLIError as error:
            self._render_error(str(error))
            return 1
        except (ProtocolError, ValidationError):
            self._render_error("ipc_invalid_response")
            return 1
        except BrokenPipeError:
            # A consumer such as `head` closed stdout. Stop owned work and avoid
            # a second flush exception during Python's interpreter shutdown.
            try:
                with open(os.devnull, "w") as sink:
                    os.dup2(sink.fileno(), self._ui.stdout.fileno())
            except (AttributeError, OSError, ValueError):
                pass
            await self._cancel_current()
            return 141
        except (OSError, TimeoutError):
            self._render_error("ipc_unavailable")
            return 1
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            signal.signal(signal.SIGINT, previous)
            self._ui.end_activity()

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.call(method, payload or {})
        if not response.ok:
            if response.error_code == "job_not_found":
                self._job = None
            raise EngineeringCLIError(response.error_code or "ipc_invalid_response")
        return response.payload

    def _accept_snapshot(self, payload: dict[str, Any]) -> JobSnapshot:
        snapshot = JobSnapshot.model_validate(payload)
        if self._job is not None and (
            snapshot.job_id != self._job.job_id
            or snapshot.request_id != self._job.request_id
            or snapshot.conversation_id != self._job.conversation_id
            or snapshot.stream_version < self._job.stream_version
        ):
            raise ProtocolError("engineering job response identity or version mismatch")
        self._job = snapshot
        self._conversation_id = snapshot.conversation_id
        return snapshot

    async def _submit_and_render(self, text: str) -> int:
        if self._job is not None:
            raise EngineeringCLIError("job_pending")
        if self._workspace is None:
            raise EngineeringCLIError("engineering_preflight_invalid")
        if len(text) > 50_000:
            raise EngineeringCLIError("input_too_large")
        if contains_likely_secret_material(text):
            raise EngineeringCLIError("secret_material_rejected")
        try:
            request = EngineeringSubmitPayload(
                text=text,
                workspace_path=str(self._workspace),
                domain=self._domain,
                research_policy=self._research_policy,
                inference_policy=self._inference_policy,
                conversation_id=self._conversation_id,
            )
        except ValidationError as error:
            raise EngineeringCLIError("invalid_engineering_request") from error
        self._rendered = ""
        self._response_started = False
        self._started_at = time.monotonic()
        self._ui.activity("Enviando solicitud")
        payload = await self._call(
            EngineeringIpcService.SUBMIT_METHOD,
            request.model_dump(mode="json", exclude_none=True),
        )
        return await self._follow(self._accept_snapshot(payload))

    def _render_text(self, text: str) -> None:
        if not text or text == self._rendered:
            return
        if not self._response_started:
            self._ui.response_heading()
            self._response_started = True
        if text.startswith(self._rendered):
            self._ui.result(text[len(self._rendered) :])
        else:
            self._ui.result("\n\n[Respuesta revisada]\n" + text)
        self._rendered = text

    async def _follow(self, snapshot: JobSnapshot) -> int:
        last_status: JobStatus | None = None
        confirmation_digest: str | None = None
        while True:
            if snapshot.status != last_status and not self._response_started:
                if snapshot.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                    self._ui.activity(
                        "En cola" if snapshot.status is JobStatus.QUEUED else "Procesando"
                    )
            last_status = snapshot.status
            self._render_text(snapshot.partial_result or "")
            if snapshot.status in TERMINAL_STATUSES:
                self._job = None
                if snapshot.status is JobStatus.COMPLETED:
                    self._render_text(snapshot.result or "")
                    if not (snapshot.result or "").strip():
                        raise EngineeringCLIError("empty_agent_response")
                    self._ui.result("\n")
                    if self._ui.interactive:
                        self._ui.notice(
                            f"Respuesta recibida · {time.monotonic() - self._started_at:.1f} s"
                        )
                    self._render_provenance(snapshot)
                    if snapshot.conversation_persisted is False:
                        self._ui.notice(
                            "La respuesta no pudo guardarse en la conversación.", color="33"
                        )
                        self._conversation_id = None
                    return 0
                if self._rendered:
                    self._ui.result("\n")
                if snapshot.status is JobStatus.CANCELLED:
                    self._ui.notice("Tarea cancelada.", color="33")
                    return 130
                raise EngineeringCLIError(snapshot.error_code or "swarm_execution_failed")
            if snapshot.confirmation is not None:
                if confirmation_digest != snapshot.confirmation.call_digest:
                    confirmation_digest = snapshot.confirmation.call_digest
                    if self._rendered:
                        self._ui.result("\n")
                    self._ui.notice("AUTORIZACIÓN REQUERIDA", color="1;33")
                    self._ui.notice(snapshot.confirmation.summary)
                    self._ui.notice("Confirma en el notch/HUD. Ctrl-C cancela; el CLI no autoriza.")
                    self._ui.notice(f"Tarea: {snapshot.job_id}")
                if not self._ui.interactive:
                    return 3
                # jobs.wait returns immediately at authorization boundaries.
                # Bound foreground observation; never spin while a human decides.
                await asyncio.sleep(0.5)
            payload = await self._call(
                "jobs.wait",
                {
                    "job_id": str(snapshot.job_id),
                    "after_stream_version": snapshot.stream_version,
                    "timeout_milliseconds": 20_000,
                },
            )
            snapshot = self._accept_snapshot(payload)

    async def _cancel_current(self) -> int:
        self._ui.end_activity()
        if self._job is None:
            self._ui.notice("No hay una tarea identificada que cancelar.")
            self._ui.notice("Si interrumpiste el envío, revisa el HUD: podría haberse recibido.")
            return 0
        job_id = self._job.job_id
        try:
            async with asyncio.timeout(5):
                snapshot = self._accept_snapshot(
                    await self._call(
                        "jobs.cancel",
                        {
                            "job_id": str(job_id),
                        },
                    )
                )
            if snapshot.status not in TERMINAL_STATUSES:
                raise EngineeringCLIError("cancel_unverified")
            self._job = None
            if self._rendered:
                self._ui.result("\n")
            if snapshot.status is JobStatus.CANCELLED:
                self._ui.notice("✓ Cancelación confirmada por el daemon.", color="33")
            else:
                if snapshot.status is JobStatus.COMPLETED:
                    self._render_text(snapshot.result or "")
                    self._ui.result("\n")
                self._ui.notice("La tarea ya había terminado; no se deshicieron sus efectos.")
            return 0
        except (EngineeringCLIError, ProtocolError, ValidationError, OSError, TimeoutError):
            self._render_error("cancel_unverified")
            self._ui.notice(f"Tarea: {job_id}")
            return 1

    async def _handle_command(self, text: str) -> int:
        if not text.startswith("/") or "\n" in text:
            return await self._submit_and_render(text)
        try:
            parts = shlex.split(text)
        except ValueError as error:
            raise EngineeringCLIError("invalid_command") from error
        if not parts:
            raise EngineeringCLIError("invalid_command")
        command = parts[0].casefold()
        if command in {"/resume", "/cancel", "/status"}:
            if len(parts) != 1:
                raise EngineeringCLIError("invalid_command")
            if command == "/cancel":
                return await self._cancel_current()
            if command == "/resume":
                if self._job is None:
                    self._ui.notice("No hay una tarea pendiente.")
                    return 0
                return await self._follow(
                    self._accept_snapshot(
                        await self._call(
                            "jobs.status",
                            {"job_id": str(self._job.job_id)},
                        )
                    )
                )
            self._render_status()
            health, security, provider = await asyncio.gather(
                self._call("health"),
                self._call("security.status"),
                self._call("provider.status"),
            )
            if security.get("state") == "compromised":
                raise EngineeringCLIError("security_compromised")
            if (
                health.get("runtime_state") not in ("active", "suspended")
                or security.get("state") != "intact"
                or provider.get("local_model") not in ("available", "unavailable")
            ):
                raise ProtocolError("invalid runtime status")
            self._ui.notice(
                f"Daemon: {health.get('runtime_state', 'desconocido')} · "
                f"Seguridad: {security.get('state', 'desconocida')} · "
                f"Modelo local: {provider.get('local_model', 'desconocido')}"
            )
            return 0
        if command == "/workspace":
            if len(parts) != 2:
                raise EngineeringCLIError("invalid_command")
            self._require_idle()
            try:
                requested = Path(parts[1]).expanduser()
                if not requested.is_absolute():
                    assert self._workspace is not None
                    requested = self._workspace / requested
                requested = requested.resolve(strict=True)
                if not requested.is_dir():
                    raise NotADirectoryError
            except (OSError, ValueError) as error:
                raise EngineeringCLIError("workspace_unavailable") from error
            await self.preflight(requested)
            self._reset_conversation()
            self._render_status()
            return 0
        return self._handle_local_command(text) or 0

    def _require_idle(self) -> None:
        if self._job is not None:
            raise EngineeringCLIError("job_pending")

    def _reset_conversation(self) -> None:
        self._conversation_id = None
        if self._editor is not None:
            self._editor.history.clear()

    def _handle_local_command(self, text: str) -> int | None:
        if not text.startswith("/"):
            return None
        try:
            parts = shlex.split(text)
            command = parts[0].casefold()
            if command in {"/help", "/status", "/clear", "/new", "/exit", "/quit"}:
                if len(parts) != 1:
                    raise ValueError
                if command in {"/exit", "/quit"}:
                    return -1
                if command == "/help":
                    self._render_help()
                elif command == "/status":
                    self._render_status()
                elif command == "/clear":
                    self._ui.clear()
                    self._render_header()
                else:
                    self._require_idle()
                    self._reset_conversation()
                    self._ui.notice("✓ Nueva conversación e historial del editor limpio.")
                    self._ui.notice("No se borran recuerdos persistentes ni preferencias.")
                return 0
            self._require_idle()
            if len(parts) != 2:
                raise ValueError
            if command == "/domain":
                self._domain = EngineeringDomain(parts[1].casefold())
            elif command == "/research":
                self._research_policy = EngineeringResearchPolicy(parts[1].casefold())
            elif command == "/inference":
                policy = EngineeringInferencePolicy(parts[1].casefold())
                if policy != self._inference_policy:
                    # A local conversation must not silently become cloud context.
                    self._reset_conversation()
                    self._ui.notice("Nueva conversación al cambiar la política de inferencia.")
                self._inference_policy = policy
            else:
                raise ValueError
        except (ValueError, IndexError) as error:
            raise EngineeringCLIError("invalid_command") from error
        self._render_status()
        return 0

    def _render_header(self) -> None:
        assert self._workspace is not None
        self._ui.banner(
            self._workspace,
            self._domain.value,
            self._research_policy.value,
            self._inference_policy.value,
        )

    def _render_status(self) -> None:
        self._ui.notice("ESTADO DE SESIÓN", color="1;36")
        for label, value in (
            ("workspace", self._workspace),
            ("perfil", self._domain.value),
            ("investigación", self._research_policy.value),
            ("inferencia", self._inference_policy.value),
            ("conversación", self._conversation_id or "nueva"),
            ("tarea", self._job.job_id if self._job is not None else "ninguna"),
        ):
            self._ui.notice(f"  {label:<12} {value}")
        self._ui.notice("Acceso: lectura. Offline solo limita la investigación web.")

    def _render_help(self) -> None:
        self._ui.notice("COMANDOS", color="1;36")
        for line in (
            "/domain <perfil>       especialidad técnica",
            "/research <modo>       offline | public_web (fuentes públicas)",
            "/inference <modo>      nvidia_only (defecto) | local_only | hybrid",
            '/workspace "carpeta"   cambiar dentro del límite autorizado del daemon',
            "/status                estado local y consulta autenticada del daemon",
            "/resume                retomar una tarea tras desconexión, sin reenviarla",
            "/cancel                cancelar la tarea identificada, sin deshacer cambios",
            "/new                   nueva conversación; no borra memoria persistente",
            "/clear                 limpiar la pantalla sin perder el contexto",
            "/exit                  cancelar trabajo pendiente y salir",
            "Perfiles: " + ", ".join(value.value for value in EngineeringDomain),
            "Enter envía; Alt+Enter añade una línea; pegar varias líneas no las ejecuta.",
            "Historial del editor: solo memoria, hasta 100 entradas/64 KiB; /new lo limpia.",
        ):
            self._ui.notice(line)

    def _render_error(self, code: str) -> None:
        self._ui.error(code)

    def _render_provenance(self, snapshot: JobSnapshot) -> None:
        evaluation = snapshot.evaluation
        brain = evaluation.brain.value if evaluation is not None else "unknown"
        label = {
            "local": "modelo local",
            "nvidia": "modelo remoto",
            "deterministic": "lógica determinista",
            "unknown": "origen no informado",
        }[brain]
        model = (
            f" · {evaluation.model_id}" if evaluation is not None and evaluation.model_id else ""
        )
        self._ui.notice(f"Origen: {label}{model}")
        if brain != "deterministic":
            self._ui.notice(
                "Respuesta no validada técnicamente. "
                "No implica código ejecutado ni pruebas aprobadas.",
                color="33",
            )
