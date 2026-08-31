from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import struct
import subprocess
import sys
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from aegis_core.secrets import MacOSIpcSecret, SecretNotFoundError
from aegis_core.tools.audit import AuditSink, NullAuditSink

_DISCOVERY_VERSION = "1.0"
_DISCOVERY_GROUP = "239.174.21.5"
_DISCOVERY_PORT = 45115
_MAX_DISCOVERY_PACKET = 1_024
_MAX_REQUEST_BYTES = 65_536
_MAX_RESPONSE_BYTES = 1_048_576
_ANNOUNCE_INTERVAL_SECONDS = 0.25
_INTERFACE_REFRESH_SECONDS = 1.0
_PEER_LIVENESS_SECONDS = 0.9
_MODEL_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_RESPONSE_MAGIC = b"AEGDRM1\0"


class DistributedMLXError(RuntimeError):
    """Authenticated Thunderbolt/JACCL inference was unavailable or failed closed."""


@dataclass(frozen=True, slots=True)
class ThunderboltPeer:
    node_id: UUID
    host: str
    address: str
    last_seen: float


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, owner: ThunderboltPeerDiscovery) -> None:
        self._owner = owner

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._owner.receive(data, addr[0])


class ThunderboltPeerDiscovery:
    def __init__(
        self,
        *,
        secret: bytes,
        group: str = _DISCOVERY_GROUP,
        port: int = _DISCOVERY_PORT,
    ) -> None:
        if len(secret) != 32 or not 1_024 <= port <= 65_535:
            raise ValueError("distributed discovery configuration is invalid")
        self._secret = secret
        self._group = ipaddress.ip_address(group)
        if not self._group.is_multicast:
            raise ValueError("distributed discovery group is not multicast")
        self._port = port
        self._node_id = uuid4()
        self._host = socket.gethostname().split(".", 1)[0]
        self._transports: list[tuple[asyncio.DatagramTransport, str]] = []
        self._peers: dict[UUID, ThunderboltPeer] = {}
        self._nonces: OrderedDict[str, float] = OrderedDict()
        self._interfaces: tuple[tuple[str, str, int], ...] = ()

    @property
    def peers(self) -> tuple[ThunderboltPeer, ...]:
        now = time.monotonic()
        self._peers = {
            key: peer
            for key, peer in self._peers.items()
            if now - peer.last_seen <= _PEER_LIVENESS_SECONDS
        }
        return tuple(sorted(self._peers.values(), key=lambda peer: str(peer.node_id)))

    @property
    def host(self) -> str:
        return self._host

    async def start(self) -> None:
        interfaces = self._thunderbolt_interfaces()
        if interfaces == self._interfaces and (self._transports or not interfaces):
            return
        self.close()
        self._interfaces = interfaces
        self._peers.clear()
        if not interfaces:
            return
        loop = asyncio.get_running_loop()
        try:
            for _, address, _ in interfaces:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                sock.bind(("", self._port))
                encoded_address = socket.inet_aton(address)
                membership = socket.inet_aton(str(self._group)) + encoded_address
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, encoded_address)
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _DiscoveryProtocol(self),
                    sock=sock,
                )
                self._transports.append((transport, address))
        except Exception:
            self.close()
            self._interfaces = ()
            raise

    def close(self) -> None:
        for transport, _ in self._transports:
            transport.close()
        self._transports.clear()

    async def run(self) -> None:
        next_refresh = 0.0
        while True:
            now = time.monotonic()
            if now >= next_refresh:
                try:
                    await self.start()
                except OSError:
                    self.close()
                next_refresh = now + _INTERFACE_REFRESH_SECONDS
            self.announce()
            await asyncio.sleep(_ANNOUNCE_INTERVAL_SECONDS)

    def announce(self) -> None:
        if not self._transports:
            return
        for transport, address in self._transports:
            body = {
                "version": _DISCOVERY_VERSION,
                "node_id": str(self._node_id),
                "host": self._host,
                "address": address,
                "timestamp": int(time.time()),
                "nonce": secrets.token_hex(16),
            }
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            packet = {
                **body,
                "hmac": hmac.new(self._secret, canonical, hashlib.sha256).hexdigest(),
            }
            encoded = json.dumps(packet, separators=(",", ":")).encode()
            if len(encoded) <= _MAX_DISCOVERY_PACKET:
                transport.sendto(encoded, (str(self._group), self._port))

    def receive(self, data: bytes, source_address: str) -> None:
        try:
            if not 1 <= len(data) <= _MAX_DISCOVERY_PACKET:
                return
            packet = json.loads(data)
            if not isinstance(packet, dict) or set(packet) != {
                "version", "node_id", "host", "address", "timestamp", "nonce", "hmac"
            }:
                return
            signature = packet.pop("hmac")
            canonical = json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
            if not isinstance(signature, str) or not hmac.compare_digest(
                signature,
                hmac.new(self._secret, canonical, hashlib.sha256).hexdigest(),
            ):
                return
            node_id = UUID(packet["node_id"])
            timestamp = int(packet["timestamp"])
            nonce = packet["nonce"]
            host = packet["host"]
            address = ipaddress.ip_address(packet["address"])
            source = ipaddress.ip_address(source_address)
            if (
                node_id == self._node_id
                or packet["version"] != _DISCOVERY_VERSION
                or abs(time.time() - timestamp) > 10
                or not isinstance(nonce, str)
                or len(nonce) != 32
                or nonce in self._nonces
                or not isinstance(host, str)
                or not 1 <= len(host) <= 63
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", host) is None
                or address != source
                or not self._on_thunderbolt_subnet(address)
            ):
                return
            now = time.monotonic()
            self._nonces[nonce] = now
            while len(self._nonces) > 512:
                self._nonces.popitem(last=False)
            self._peers[node_id] = ThunderboltPeer(node_id, host, str(address), now)
        except (TypeError, ValueError, json.JSONDecodeError):
            return

    def _on_thunderbolt_subnet(
        self, address: ipaddress.IPv4Address | ipaddress.IPv6Address
    ) -> bool:
        return any(
            address in ipaddress.ip_network(f"{local}/{mask}", strict=False)
            for _, local, mask in self._interfaces
        )

    @staticmethod
    def _thunderbolt_interfaces() -> tuple[tuple[str, str, int], ...]:
        try:
            ports = subprocess.run(
                ("/usr/sbin/networksetup", "-listallhardwareports"),
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout
        except OSError:
            return ()
        devices: list[str] = []
        current_thunderbolt = False
        for line in ports.splitlines():
            if line.startswith("Hardware Port:"):
                current_thunderbolt = "Thunderbolt" in line
            elif current_thunderbolt and line.startswith("Device:"):
                devices.append(line.partition(":")[2].strip())
        interfaces: list[tuple[str, str, int]] = []
        for device in devices:
            try:
                address = subprocess.run(
                    ("/usr/sbin/ipconfig", "getifaddr", device),
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                ).stdout.strip()
                if address:
                    ipaddress.ip_address(address)
                    interfaces.append((device, address, 24))
            except (OSError, ValueError):
                continue
        return tuple(interfaces)


LocalFallback = Callable[[str, int], Awaitable[str]]


class DistributedMLXProvider:
    def __init__(
        self,
        *,
        discovery: ThunderboltPeerDiscovery,
        hostfile: Path,
        model_id: str,
        local_fallback: LocalFallback,
        audit_sink: AuditSink | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        if _MODEL_PATTERN.fullmatch(model_id) is None:
            raise ValueError("distributed MLX model identifier is invalid")
        if not 5 <= timeout_seconds <= 300:
            raise ValueError("distributed MLX timeout is invalid")
        self._discovery = discovery
        self._hostfile = hostfile
        self._model_id = model_id
        self._fallback = local_fallback
        self._audit = audit_sink or NullAuditSink()
        self._timeout = timeout_seconds
        self._lock = asyncio.Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    async def generate(self, prompt: str, *, maximum_tokens: int = 512) -> str:
        if not prompt or len(prompt.encode()) > 48_000 or not 1 <= maximum_tokens <= 2_048:
            raise DistributedMLXError("distributed prompt is invalid")
        detected_at = time.monotonic()
        try:
            async with self._lock:
                return await self._generate_distributed(prompt, maximum_tokens)
        except (DistributedMLXError, OSError, TimeoutError) as error:
            transition_ms = round((time.monotonic() - detected_at) * 1_000)
            self._audit.record_system_event(
                uuid4(),
                event_type="distributed_mlx_degraded",
                component="mlx_distributed",
                data={
                    "reason": type(error).__name__,
                    "fallback_started_ms": transition_ms,
                    "model": self._model_id,
                },
            )
            try:
                return await self._fallback(prompt, maximum_tokens)
            except asyncio.CancelledError:
                raise
            except Exception as fallback_error:
                self._audit.record_system_event(
                    uuid4(),
                    event_type="distributed_mlx_fallback_failed",
                    component="mlx_distributed",
                    data={"reason": type(fallback_error).__name__},
                )
                raise DistributedMLXError(
                    "distributed and local MLX inference are unavailable"
                ) from fallback_error

    async def _generate_distributed(self, prompt: str, maximum_tokens: int) -> str:
        peers = self._discovery.peers
        hostfile, required_peer_addresses = self._validated_hostfile(peers)
        launch = shutil_which("mlx.launch")
        if launch is None:
            raise DistributedMLXError("mlx.launch is unavailable")
        request = json.dumps(
            {"prompt": prompt, "maximum_tokens": maximum_tokens},
            separators=(",", ":"),
        ).encode()
        process = await asyncio.create_subprocess_exec(
            launch,
            "--backend",
            "jaccl",
            "--hostfile",
            str(hostfile),
            "--",
            sys.executable,
            "-m",
            "aegis_core.providers.mlx_distributed",
            "--worker",
            "--model",
            self._model_id,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "HF_HUB_OFFLINE": "1"},
        )
        try:
            async with asyncio.timeout(self._timeout):
                stdout = await self._communicate_with_link_watchdog(
                    process,
                    struct.pack(">I", len(request)) + request,
                    required_peer_addresses,
                )
        except (DistributedMLXError, TimeoutError):
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise
        marker = stdout.rfind(_RESPONSE_MAGIC)
        if process.returncode != 0 or marker < 0 or len(stdout) < marker + 12:
            raise DistributedMLXError("JACCL worker failed")
        framed = stdout[marker + len(_RESPONSE_MAGIC) :]
        length = struct.unpack(">I", framed[:4])[0]
        if not 1 <= length <= _MAX_RESPONSE_BYTES or len(framed) != length + 4:
            raise DistributedMLXError("JACCL worker response is invalid")
        try:
            response = json.loads(framed[4:])
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DistributedMLXError("JACCL worker response is not JSON") from error
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise DistributedMLXError("JACCL worker rejected inference")
        content = response.get("content")
        if not isinstance(content, str) or not content.strip():
            raise DistributedMLXError("JACCL worker returned empty content")
        return content.strip()

    async def _communicate_with_link_watchdog(
        self,
        process: asyncio.subprocess.Process,
        request: bytes,
        required_peer_addresses: frozenset[str],
    ) -> bytes:
        async def monitor() -> None:
            while True:
                await asyncio.sleep(0.1)
                active = {peer.address for peer in self._discovery.peers}
                if not required_peer_addresses.issubset(active):
                    raise DistributedMLXError("authenticated Thunderbolt peer disappeared")

        communicate = asyncio.create_task(process.communicate(request))
        watchdog = asyncio.create_task(monitor())
        try:
            done, _ = await asyncio.wait(
                {communicate, watchdog},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if watchdog in done:
                await watchdog
            stdout, _ = await communicate
            return stdout
        finally:
            for task in (communicate, watchdog):
                if not task.done():
                    task.cancel()
            await asyncio.gather(communicate, watchdog, return_exceptions=True)

    def _validated_hostfile(
        self, peers: tuple[ThunderboltPeer, ...]
    ) -> tuple[Path, frozenset[str]]:
        status = self._hostfile.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.getuid()
            or status.st_mode & 0o077
        ):
            raise DistributedMLXError("JACCL hostfile is not private")
        try:
            entries = json.loads(self._hostfile.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise DistributedMLXError("JACCL hostfile is invalid") from error
        if not isinstance(entries, list) or not 2 <= len(entries) <= 8:
            raise DistributedMLXError("JACCL hostfile topology is invalid")
        world_size = len(entries)
        configured_hosts: set[str] = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {"ssh", "ips", "rdma"}:
                raise DistributedMLXError("JACCL hostfile entry is invalid")
            ssh = entry["ssh"]
            ips = entry["ips"]
            rdma = entry["rdma"]
            if (
                not isinstance(ssh, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", ssh) is None
                or not isinstance(ips, list)
                or len(ips) > world_size
                or not all(isinstance(ip, str) for ip in ips)
                or not isinstance(rdma, list)
                or len(rdma) != world_size
            ):
                raise DistributedMLXError("JACCL hostfile entry is invalid")
            try:
                for ip in ips:
                    ipaddress.ip_address(ip)
            except ValueError as error:
                raise DistributedMLXError("JACCL hostfile IP is invalid") from error
            for peer_index, device in enumerate(rdma):
                if peer_index == index:
                    if device is not None:
                        raise DistributedMLXError("JACCL hostfile diagonal is invalid")
                elif (
                    not isinstance(device, str)
                    or re.fullmatch(r"rdma_[A-Za-z0-9._-]{1,31}", device) is None
                ):
                    raise DistributedMLXError("JACCL RDMA mesh is incomplete")
            configured_hosts.add(ssh.split(".", 1)[0].casefold())
        local_host = self._discovery.host.split(".", 1)[0].casefold()
        if local_host not in configured_hosts:
            raise DistributedMLXError("local Mac is absent from JACCL hostfile")
        peer_by_host = {
            peer.host.split(".", 1)[0].casefold(): peer for peer in peers
        }
        required_hosts = configured_hosts - {local_host}
        if not required_hosts or not required_hosts.issubset(peer_by_host):
            raise DistributedMLXError("JACCL hostfile lacks authenticated peers")
        required_addresses = frozenset(
            peer_by_host[host].address for host in required_hosts
        )
        return self._hostfile, required_addresses


def shutil_which(program: str) -> str | None:
    from shutil import which

    return which(program, path=os.environ.get("PATH"))


def load_cluster_secret(service: str, account: str) -> bytes:
    try:
        return bytes.fromhex(MacOSIpcSecret(service, account).get())
    except (SecretNotFoundError, ValueError) as error:
        raise DistributedMLXError("distributed cluster credential is unavailable") from error


def _worker(model_id: str) -> int:
    try:
        import mlx.core as mx
        from mlx_lm.generate import stream_generate
        from mlx_lm.utils import sharded_load

        group = mx.distributed.init()
        header = sys.stdin.buffer.read(4)
        if len(header) != 4:
            raise DistributedMLXError("worker request header is invalid")
        length = struct.unpack(">I", header)[0]
        if not 1 <= length <= _MAX_REQUEST_BYTES:
            raise DistributedMLXError("worker request exceeds its limit")
        body = sys.stdin.buffer.read(length)
        if len(body) != length:
            raise DistributedMLXError("worker request is incomplete")
        request = json.loads(body)
        prompt = request.get("prompt") if isinstance(request, dict) else None
        maximum_tokens = request.get("maximum_tokens") if isinstance(request, dict) else None
        if not isinstance(prompt, str) or not isinstance(maximum_tokens, int):
            raise DistributedMLXError("worker request is invalid")
        model, tokenizer = sharded_load(model_id, tensor_group=group)
        formatted = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
        )
        segments: list[str] = []
        for response in stream_generate(
            model,
            tokenizer,
            formatted,
            max_tokens=maximum_tokens,
            max_kv_size=4_096,
            kv_bits=4,
        ):
            segments.append(response.text)
        mx.eval(mx.distributed.all_sum(mx.array(1), stream=mx.cpu))
        if group.rank() == 0:
            payload = json.dumps(
                {"ok": True, "content": "".join(segments)},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            sys.stdout.buffer.write(
                _RESPONSE_MAGIC + struct.pack(">I", len(payload)) + payload
            )
            sys.stdout.buffer.flush()
        return 0
    except Exception:
        return 1


def _main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--model")
    arguments = parser.parse_args()
    if not arguments.worker or not isinstance(arguments.model, str):
        return 64
    return _worker(arguments.model)


if __name__ == "__main__":
    raise SystemExit(_main())
