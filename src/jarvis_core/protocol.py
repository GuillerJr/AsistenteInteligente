"""Own local JSON-RPC transport; no web listener or implicit user-data paths.

Every request carries a private random discovery bearer. Productive services also
require an admitted Ed25519 client identity, nonce and sequence bound to the exact
request and physical profile. This is key identity, not Apple code attestation.
Hosts inject a finite method catalog and cooperative async handlers. Cancellation
of an RPC is not a receipt that an underlying operation had no effect.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import errno
import hmac
import json
import math
import os
import re
import secrets
import socket
import stat
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Self

from jarvis_core.client_identity import (
    ClientAuthority,
    ClientIdentity,
    ClientIdentityError,
    client_directory,
    decode_signed_body,
    profile_binding,
)

RpcHandler = Callable[[dict[str, Any]], Awaitable[Any]]
_METHOD = re.compile(r"[a-z][a-z0-9_.]{0,95}\Z")
_TOKEN = re.compile(rb"[a-f0-9]{64}\Z")
_SOCKET_NAME = "service.sock"
_TOKEN_NAME = "transport.token"
_MAX_ID = 2**53 - 1
authenticated_client = ContextVar("jarvis_authenticated_rpc_client", default=None)


class RpcError(Exception):
    """Safe, public RPC failure. Hosts must not put credentials in its message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TransportSecurityError(ValueError):
    """A local path or credential did not satisfy the transport contract."""


def _check_directory(directory: Path, *, create: bool = False) -> None:
    if not directory.is_absolute():
        raise TransportSecurityError("Se requiere una ruta de estado absoluta y explícita.")
    # Reject symlinks in every component, including a symlink to a private directory.
    for part in reversed((directory, *directory.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if part != directory or not create:
                raise TransportSecurityError("El directorio de estado no existe.") from None
            part.mkdir(mode=0o700)
            info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TransportSecurityError("La ruta de estado contiene un enlace o no es directorio.")
    info = directory.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise TransportSecurityError("El directorio de estado debe ser propio y privado (0700).")
    # macOS sockaddr_un has 104 bytes, including the terminating NUL.
    if len(os.fsencode(directory / _SOCKET_NAME)) > 103:
        raise TransportSecurityError("La ruta del socket local es demasiado larga.")


def _read_token(directory: Path) -> str:
    _check_directory(directory)
    try:
        descriptor = os.open(directory / _TOKEN_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise TransportSecurityError(
            "No se pudo abrir la credencial local de forma segura."
        ) from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size != 64
        ):
            raise TransportSecurityError("La credencial local debe ser propia y privada (0600).")
        raw = os.read(descriptor, 65)
        if not _TOKEN.fullmatch(raw):
            raise TransportSecurityError("La credencial local no tiene el formato esperado.")
        return raw.decode("ascii")
    finally:
        os.close(descriptor)


def _ensure_token(directory: Path) -> None:
    path = directory / _TOKEN_NAME
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        _read_token(directory)
        return
    try:
        raw = secrets.token_hex(32).encode("ascii")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    _read_token(directory)


def _check_socket(directory: Path) -> os.stat_result:
    _check_directory(directory)
    try:
        info = (directory / _SOCKET_NAME).lstat()
    except OSError:
        raise TransportSecurityError("El socket local no está disponible.") from None
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise TransportSecurityError("El socket local debe ser propio y privado (0600).")
    return info


def _valid_id(value: Any) -> bool:
    return (type(value) is int and abs(value) <= _MAX_ID) or (
        isinstance(value, str) and 1 <= len(value) <= 128
    )


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_number(_: str) -> None:
    raise ValueError("non-finite number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite number")
    return result


def _decode(frame: bytes) -> Any:
    return json.loads(
        frame.decode("utf-8"),
        object_pairs_hook=_object,
        parse_constant=_invalid_number,
        parse_float=_finite_float,
    )


def _encode(value: Any, limit: int) -> bytes:
    frame = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if len(frame) + 1 > limit:
        raise RpcError(-32004, "El mensaje excede el límite permitido.")
    return frame + b"\n"


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _budgets(max_frame_bytes: int, *timeouts: float) -> None:
    if type(max_frame_bytes) is not int or not 512 <= max_frame_bytes <= 1024 * 1024:
        raise ValueError("El límite de mensaje debe estar entre 512 y 1048576 bytes.")
    if any(
        type(value) not in (int, float) or not math.isfinite(value) or value <= 0
        for value in timeouts
    ):
        raise ValueError("Los tiempos máximos deben ser finitos y positivos.")


class LocalRpcServer:
    """Bounded Unix socket server. Existing sockets are never replaced implicitly.

    Handler exceptions are sanitized; RpcError is an explicit public error. A token
    is checked from its private file on every request, so replacing/removing it
    revokes the previous credential even for an already connected client.
    """

    def __init__(
        self,
        state_directory: Path,
        handlers: Mapping[str, RpcHandler],
        *,
        max_frame_bytes: int = 65536,
        idle_timeout: float = 30,
        request_timeout: float = 30,
        shutdown_timeout: float = 2,
        max_connections: int = 32,
        client_authority: ClientAuthority | None = None,
        read_methods: frozenset[str] = frozenset(),
        recover_stale: Callable[[], None] | None = None,
        client_control_guard=None,
    ) -> None:
        _budgets(max_frame_bytes, idle_timeout, request_timeout, shutdown_timeout)
        if type(max_connections) is not int or not 1 <= max_connections <= 256:
            raise ValueError("El límite de conexiones debe estar entre 1 y 256.")
        if any(
            not isinstance(name, str)
            or not _METHOD.fullmatch(name)
            or name == "health"
            or not callable(handler)
            for name, handler in handlers.items()
        ):
            raise ValueError("El catálogo RPC contiene un método inválido o reservado.")
        self.state_directory = Path(state_directory)
        self.socket_path = self.state_directory / _SOCKET_NAME
        self.token_path = self.state_directory / _TOKEN_NAME
        self._handlers = dict(handlers)
        self._limit = max_frame_bytes
        self._idle_timeout = idle_timeout
        self._request_timeout = request_timeout
        self._shutdown_timeout = shutdown_timeout
        self._max_connections = max_connections
        self._server: asyncio.Server | None = None
        self._connections: dict[asyncio.Task[None], asyncio.StreamWriter] = {}
        self._socket_identity: tuple[int, int] | None = None
        self._closing = False
        self.client_authority = client_authority
        self._read_methods = read_methods | {"health"}
        self._recover_stale = recover_stale
        self.recovered_stale_socket = False
        self._client_control_guard = client_control_guard
        self._identity_session = ContextVar("jarvis_rpc_client_connection", default=None)

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving() and not self._closing

    @property
    def active_connections(self) -> int:
        return len(self._connections)

    async def start(self) -> Self:
        if self._server is not None or self._connections:
            raise RuntimeError("El transporte ya está iniciado o sigue cerrándose.")
        _check_directory(self.state_directory, create=True)
        _ensure_token(self.state_directory)
        if os.path.lexists(self.socket_path):
            self._recover_socket()
        self._closing = False
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Binding our own socket is atomic. asyncio's path= convenience API
            # unlinks an existing Unix socket, which would permit a startup race.
            listener.bind(os.fspath(self.socket_path))
            info = self.socket_path.lstat()
            self._socket_identity = (info.st_dev, info.st_ino)
            os.chmod(self.socket_path, 0o600, follow_symlinks=False)
            _check_socket(self.state_directory)
            self._server = await asyncio.start_unix_server(
                self._accept, sock=listener, limit=self._limit, start_serving=False
            )
            await self._server.start_serving()
        except BaseException:
            listener.close()
            await self.aclose()
            raise
        return self

    def _recover_socket(self):
        """Reclaim only a refused socket under the caller's physical profile lease.

        No PID guess or signal is used; a listener, ambiguous errno, symlink or
        inode replacement fails closed. Same-user adversaries that ignore the
        lease remain outside this cooperative local-process guarantee.
        """
        if self._recover_stale is None:
            raise TransportSecurityError("Ya existe una entrada para el socket; no se sustituirá.")
        self._recover_stale()
        before = _check_socket(self.state_directory)
        identity = (before.st_dev, before.st_ino, before.st_ctime_ns)
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.1)
            try:
                probe.connect(os.fspath(self.socket_path))
            except OSError as error:
                if error.errno != errno.ECONNREFUSED:
                    raise TransportSecurityError(
                        "No se confirmó un socket residual cerrado.") from None
            else:
                raise TransportSecurityError("El socket local todavía tiene un servicio activo.")
        finally:
            probe.close()
        self._recover_stale()
        after = _check_socket(self.state_directory)
        if identity != (after.st_dev, after.st_ino, after.st_ctime_ns):
            raise TransportSecurityError("El socket cambió durante su revisión.")
        self.socket_path.unlink()
        self.recovered_stale_socket = True

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closing or len(self._connections) >= self._max_connections:
            writer.close()
            return
        task = asyncio.create_task(self._serve(reader, writer))
        self._connections[task] = writer
        task.add_done_callback(self._connections.pop)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        identity_session = {"challenge": None, "sequence": 0}
        identity_token = self._identity_session.set(identity_session)
        try:
            while not self._closing:
                try:
                    frame = await asyncio.wait_for(reader.readuntil(b"\n"), self._idle_timeout)
                except asyncio.LimitOverrunError:
                    await self._send(writer, _error(None, -32004, "El mensaje excede el límite."))
                    return
                except (asyncio.IncompleteReadError, TimeoutError):
                    return
                if len(frame) > self._limit:
                    await self._send(writer, _error(None, -32004, "El mensaje excede el límite."))
                    return
                response = await self._dispatch(frame)
                await self._send(writer, response)
        except (ConnectionError, OSError, TimeoutError):
            pass
        finally:
            self._identity_session.reset(identity_token)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), self._shutdown_timeout)

    async def _send(self, writer: asyncio.StreamWriter, response: dict[str, Any]) -> None:
        try:
            frame = _encode(response, self._limit)
        except (TypeError, ValueError, OverflowError, RecursionError, RpcError):
            frame = _encode(
                _error(response.get("id"), -32004, "El resultado no cabe en un mensaje válido."),
                self._limit,
            )
        writer.write(frame)
        await asyncio.wait_for(writer.drain(), self._idle_timeout)

    async def _dispatch(self, frame: bytes, identity_session=None) -> dict[str, Any]:
        if identity_session is None:
            identity_session = self._identity_session.get()
        try:
            request = _decode(frame)
        except (ValueError, UnicodeError, RecursionError):
            return _error(None, -32700, "El mensaje JSON no es válido.")
        if not isinstance(request, dict):
            return _error(None, -32600, "La solicitud RPC no es válida.")
        request_id = request.get("id")
        if not _valid_id(request_id):
            return _error(None, -32600, "La identidad de la solicitud no es válida.")
        credential = request.get("auth")
        try:
            token = _read_token(self.state_directory)
        except TransportSecurityError:
            return _error(request_id, -32001, "La autenticación local no está disponible.")
        if (
            not isinstance(credential, str)
            or len(credential) != 64
            or not credential.isascii()
            or not hmac.compare_digest(credential.encode("utf-8"), token.encode("ascii"))
        ):
            return _error(request_id, -32001, "No se autorizó la conexión local.")
        if (
            request.get("jsonrpc") != "2.0"
            or set(request) - {"jsonrpc", "id", "method", "params", "auth"}
            or not isinstance(request.get("method"), str)
            or not _METHOD.fullmatch(request["method"])
        ):
            return _error(request_id, -32600, "La solicitud RPC no es válida.")
        params = request.get("params", {})
        if not isinstance(params, dict):
            return _error(request_id, -32602, "Los parámetros deben ser un objeto JSON.")
        method = request["method"]
        client_id = None
        if self.client_authority is not None:
            try:
                if method == "client.challenge":
                    if set(params) != {"client_id"} or identity_session is None:
                        raise ClientIdentityError("client_challenge_invalid")
                    challenge = self.client_authority.challenge(params["client_id"])
                    identity_session.update(challenge=challenge, sequence=0)
                    return {"jsonrpc": "2.0", "id": request_id, "result": challenge}
                if (method != "client.request" or identity_session is None
                        or identity_session["challenge"] is None
                        or set(params) != {"body", "signature", "sequence"}
                        or type(params.get("sequence")) is not int
                        or params["sequence"] != identity_session["sequence"] + 1):
                    raise ClientIdentityError("client_signature_required")
                body = decode_signed_body(params["body"], self._limit)
                row = self.client_authority.verify(identity_session["challenge"],
                                                   params["sequence"], body,
                                                   params["signature"])
                client_id = row["client_id"]
                inner = _decode(body)
                if (not isinstance(inner, dict)
                        or set(inner) != {"jsonrpc", "id", "method", "params"}
                        or inner["jsonrpc"] != "2.0" or inner["id"] != request_id
                        or not isinstance(inner["method"], str)
                        or not _METHOD.fullmatch(inner["method"])
                        or not isinstance(inner["params"], dict)
                        or inner["method"] in {"client.request", "client.challenge"}):
                    raise ClientIdentityError("client_body_invalid")
                identity_session["sequence"] = params["sequence"]
                method, params = inner["method"], inner["params"]
                if row["scope"] != "owner" and method not in self._read_methods:
                    return _error(request_id, -32003, "El cliente no tiene este ámbito.")
                controls = {"clients.status": self.client_authority.status,
                            "clients.review": self.client_authority.review,
                            "clients.admit": self.client_authority.admit,
                            "clients.revoke": self.client_authority.revoke}
                if method in controls:
                    try:
                        guard = (self._client_control_guard(method)
                                 if self._client_control_guard else contextlib.nullcontext())
                        with guard:
                            if method == "clients.status":
                                if params:
                                    raise ClientIdentityError("client_arguments_invalid")
                                result = controls[method](row["client_id"])
                            else:
                                result = controls[method](row["client_id"], params)
                    except RpcError as error:
                        return _error(request_id, error.code, error.message)
                    except (ClientIdentityError, ValueError, KeyError, TypeError):
                        return _error(request_id, -32038,
                                      "La revisión de clientes ya no es válida.")
                    return {"jsonrpc": "2.0", "id": request_id, "result": result}
            except (ClientIdentityError, ValueError, KeyError, UnicodeError):
                return _error(request_id, -32001, "No se autorizó la identidad local del cliente.")
            except Exception:
                return _error(request_id, -32001, "La identidad local no está disponible.")
        if method == "health":
            result: Any = {"service": "jarvis", "protocol": "jarvis-local-rpc", "version": 1}
        elif method not in self._handlers:
            return _error(request_id, -32601, "El método solicitado no está disponible.")
        else:
            try:
                token = authenticated_client.set(client_id)
                try:
                    result = await asyncio.wait_for(
                        self._handlers[method](params), self._request_timeout)
                finally:
                    authenticated_client.reset(token)
            except TimeoutError:
                return _error(request_id, -32002, "La operación excedió el tiempo máximo.")
            except RpcError as error:
                return _error(request_id, error.code, error.message)
            except Exception:
                return _error(request_id, -32603, "No se pudo completar la operación.")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def aclose(self) -> None:
        self._closing = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = tuple(self._connections)
        for task, writer in tuple(self._connections.items()):
            writer.close()
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self._shutdown_timeout)
            if pending:
                raise TimeoutError(
                    "Un manejador RPC no terminó de cancelar; el cierre sigue pendiente."
                )
        if self._socket_identity is not None:
            try:
                info = self.socket_path.lstat()
            except FileNotFoundError:
                pass
            else:
                if (
                    stat.S_ISSOCK(info.st_mode)
                    and (info.st_dev, info.st_ino) == self._socket_identity
                ):
                    self.socket_path.unlink()
            self._socket_identity = None

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


class LocalRpcClient:
    """One sequential connection; independent clients can make concurrent calls.

    A cancelled/timed out call closes the connection: no stale response can be
    mistaken for the next request, and mutations are never retried automatically.
    """

    def __init__(
        self,
        state_directory: Path,
        *,
        max_frame_bytes: int = 65536,
        timeout: float = 30,
        identity: ClientIdentity | None = None,
    ) -> None:
        _budgets(max_frame_bytes, timeout)
        self.state_directory = Path(state_directory)
        self._limit = max_frame_bytes
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._token: str | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()
        self.identity = identity
        self._challenge = None
        self._sequence = 0

    async def connect(self) -> Self:
        if self._writer is not None:
            raise RuntimeError("El cliente ya está conectado.")
        token = _read_token(self.state_directory)
        _check_socket(self.state_directory)
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_unix_connection(self.state_directory / _SOCKET_NAME, limit=self._limit),
            self._timeout,
        )
        self._token = token
        try:
            if (self.identity is None
                    and (client_directory(self.state_directory) / "clients.json").exists()):
                self.identity = ClientIdentity.for_profile(self.state_directory)
            if self.identity is not None:
                self._challenge = await self.call(
                    "client.challenge", {"client_id": self.identity.client_id})
                if (not isinstance(self._challenge, dict)
                        or set(self._challenge) != {
                            "client_id", "profile_binding", "server_id", "nonce"}
                        or self._challenge["client_id"] != self.identity.client_id
                        or self._challenge["profile_binding"] != profile_binding(
                            self.state_directory)
                        or any(not isinstance(self._challenge[key], str)
                               or re.fullmatch(r"[a-f0-9]{64}", self._challenge[key]) is None
                               for key in self._challenge)):
                    raise TransportSecurityError("El desafío de identidad local no coincide.")
        except BaseException:
            await self.aclose()
            raise
        return self

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not isinstance(method, str) or not _METHOD.fullmatch(method):
            raise ValueError("El método RPC no es válido.")
        if params is not None and not isinstance(params, dict):
            raise ValueError("Los parámetros RPC deben ser un objeto.")
        async with self._lock:
            if self._reader is None or self._writer is None:
                raise RuntimeError("El cliente no está conectado.")
            self._next_id = (self._next_id % _MAX_ID) + 1
            request = {
                    "jsonrpc": "2.0",
                    "id": self._next_id,
                    "method": method,
                    "params": params or {},
                    "auth": self._token,
                }
            if self._challenge is not None:
                next_sequence = self._sequence + 1
                inner = {key: value for key, value in request.items() if key != "auth"}
                body = _encode(inner, self._limit).rstrip(b"\n")
                request["method"] = "client.request"
                request["params"] = {"body": base64.b64encode(body).decode("ascii"),
                                     "sequence": next_sequence,
                                     "signature": self.identity.sign(
                                         self._challenge, next_sequence, body)}
            frame = _encode(request, self._limit)
            if self._challenge is not None:
                self._sequence = next_sequence
            try:
                async with asyncio.timeout(self._timeout):
                    self._writer.write(frame)
                    await self._writer.drain()
                    response_frame = await self._reader.readuntil(b"\n")
                    if len(response_frame) > self._limit:
                        raise RpcError(-32004, "La respuesta excede el límite permitido.")
                    response = _decode(response_frame)
                    if (
                        not isinstance(response, dict)
                        or response.get("jsonrpc") != "2.0"
                        or type(response.get("id")) is not int
                        or response["id"] != self._next_id
                        or set(response)
                        not in ({"jsonrpc", "id", "result"}, {"jsonrpc", "id", "error"})
                    ):
                        raise ValueError("La respuesta RPC no es válida.")
                    if "error" in response:
                        error = response["error"]
                        if (
                            not isinstance(error, dict)
                            or set(error) != {"code", "message"}
                            or type(error["code"]) is not int
                            or not isinstance(error["message"], str)
                        ):
                            raise ValueError("El error RPC no es válido.")
                        raise RpcError(error["code"], error["message"])
                    return response["result"]
            except RpcError:
                raise
            except BaseException:
                await self.aclose()
                raise

    async def aclose(self) -> None:
        writer, self._writer = self._writer, None
        self._reader = None
        self._token = None
        self._challenge = None
        self._sequence = 0
        if writer is not None:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), self._timeout)

    async def __aenter__(self) -> Self:
        return await self.connect()

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
