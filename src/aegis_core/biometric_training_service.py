from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import psutil
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.secrets import MacOSIpcSecret, SecretNotFoundError
from aegis_core.tools.audit import AuditSink, NullAuditSink

_MAGIC = b"AEGBIO1\0"
_MAX_ENVELOPE_BYTES = 2 * 1_024 * 1_024
_RENAME_SWAP = 0x00000002
_AT_FDCWD = -2


class BiometricTrainingError(RuntimeError):
    """A validated local biometric adaptation cycle failed closed."""


@dataclass(frozen=True, slots=True)
class BiometricRuntimeSnapshot:
    power_source: str
    thermal_state: str


RuntimeProbe = Callable[[], object | None]


class BiometricTrainingService:
    SAMPLE_READY_METHOD = "biometric.training.sample_ready"

    def __init__(
        self,
        *,
        training_directory: Path,
        enrollment_directory: Path,
        active_model_path: Path,
        trainer_executable: Path,
        keychain_service: str = "ai.aegis.biometric-training",
        keychain_account: str = "default",
        runtime_probe: RuntimeProbe,
        audit_sink: AuditSink | None = None,
        maximum_cpu_percent: float = 15.0,
        minimum_idle_seconds: float = 120.0,
    ) -> None:
        if not 1 <= maximum_cpu_percent <= 50:
            raise ValueError("biometric CPU threshold is out of range")
        if not 30 <= minimum_idle_seconds <= 3_600:
            raise ValueError("biometric idle threshold is out of range")
        self._training_directory = training_directory
        self._enrollment_directory = enrollment_directory
        self._active_model_path = active_model_path
        self._trainer_executable = trainer_executable
        self._secret = MacOSIpcSecret(keychain_service, keychain_account)
        self._runtime_probe = runtime_probe
        self._audit = audit_sink or NullAuditSink()
        self._maximum_cpu = maximum_cpu_percent
        self._minimum_idle = minimum_idle_seconds
        self._armed = asyncio.Event()

    def arm(self) -> None:
        self._armed.set()

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.SAMPLE_READY_METHOD: self.handle_sample_ready}

    async def handle_sample_ready(self, request: IpcRequest) -> IpcHandlerResult:
        capture_id = request.payload.get("capture_id")
        if (
            request.method != self.SAMPLE_READY_METHOD
            or set(request.payload) != {"capture_id"}
            or not isinstance(capture_id, str)
        ):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        try:
            UUID(capture_id)
        except ValueError:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        self.arm()
        return IpcHandlerResult(ok=True, payload={"scheduled": True})

    async def run(self) -> None:
        while True:
            await self._armed.wait()
            try:
                processed = await self._attempt_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._audit.record_system_event(
                    uuid4(),
                    event_type="biometric_adaptation_failed",
                    component="biometric_training",
                    data={"reason": type(error).__name__},
                )
                processed = False
            if processed or not self._pending_samples():
                self._armed.clear()
            else:
                await asyncio.sleep(60)

    async def _attempt_cycle(self) -> bool:
        samples = self._pending_samples()
        if not samples or not self._resource_policy_allows_training():
            return False
        result = await asyncio.to_thread(self._train_and_swap, samples)
        if result:
            for sample in samples:
                try:
                    sample.unlink()
                except OSError:
                    pass
        return result

    def _resource_policy_allows_training(self) -> bool:
        snapshot = self._runtime_probe()
        power_source = getattr(snapshot, "power_source", "unknown")
        thermal_state = getattr(snapshot, "thermal_state", "unknown")
        if power_source != "ac" or thermal_state not in {"nominal", "fair"}:
            return False
        if psutil.cpu_percent(interval=0.2) >= self._maximum_cpu:
            return False
        return self._hid_idle_seconds() >= self._minimum_idle

    def _train_and_swap(self, samples: tuple[Path, ...]) -> bool:
        self._validate_private_inputs()
        try:
            key = bytes.fromhex(self._secret.get())
        except (SecretNotFoundError, ValueError) as error:
            raise BiometricTrainingError("biometric key is unavailable") from error
        with tempfile.TemporaryDirectory(prefix="aegis-biometric-") as raw_temp:
            root = Path(raw_temp)
            root.chmod(0o700)
            dataset = root / "dataset"
            self._copy_enrollment_dataset(dataset)
            sample_hashes: list[str] = []
            for source in samples:
                owner, caf = self._decrypt_sample(source, key)
                target_directory = dataset / owner
                target_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                target = target_directory / f"adapt-{source.stem}.caf"
                target.write_bytes(caf)
                target.chmod(0o600)
                sample_hashes.append(hashlib.sha256(caf).hexdigest())
            staged_parent = root / "staged"
            staged_parent.mkdir(mode=0o700)
            staged_model = staged_parent / "JarvisSpeakerIdentity.mlmodelc"
            self._run_trainer(dataset, staged_model)
            self._validate_compiled_model(staged_model)
            self._atomic_model_swap(staged_model)
        self._audit.record_system_event(
            uuid4(),
            event_type="biometric_model_adapted",
            component="biometric_training",
            data={
                "validated_samples": len(samples),
                "sample_set_sha256": hashlib.sha256(
                    "\n".join(sorted(sample_hashes)).encode()
                ).hexdigest(),
                "model": "JarvisSpeakerIdentity.mlmodelc",
            },
        )
        return True

    def _pending_samples(self) -> tuple[Path, ...]:
        try:
            entries = tuple(sorted(self._training_directory.glob("*.caf.enc")))
        except OSError:
            return ()
        valid: list[Path] = []
        for path in entries[:64]:
            try:
                status = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if (
                stat.S_ISREG(status.st_mode)
                and status.st_uid == os.getuid()
                and not status.st_mode & 0o077
                and 64 <= status.st_size <= _MAX_ENVELOPE_BYTES
            ):
                valid.append(path)
        return tuple(valid)

    def _validate_private_inputs(self) -> None:
        for path, executable in (
            (self._training_directory, False),
            (self._enrollment_directory, False),
            (self._trainer_executable, True),
        ):
            status = path.stat(follow_symlinks=False)
            expected = stat.S_ISREG(status.st_mode) if executable else stat.S_ISDIR(status.st_mode)
            if (
                not expected
                or status.st_uid != os.getuid()
                or status.st_mode & 0o022
                or (executable and not status.st_mode & stat.S_IXUSR)
            ):
                raise BiometricTrainingError("biometric input is not private")

    def _copy_enrollment_dataset(self, destination: Path) -> None:
        destination.mkdir(mode=0o700)
        for label in self._enrollment_directory.iterdir():
            if label.is_symlink() or not label.is_dir():
                raise BiometricTrainingError("speaker enrollment contains an unsafe entry")
            target_label = destination / label.name
            target_label.mkdir(mode=0o700)
            for source in label.iterdir():
                status = source.stat(follow_symlinks=False)
                if not stat.S_ISREG(status.st_mode) or source.is_symlink():
                    raise BiometricTrainingError("speaker enrollment contains an unsafe file")
                target = target_label / source.name
                shutil.copyfile(source, target)
                target.chmod(0o600)

    @staticmethod
    def _decrypt_sample(path: Path, key: bytes) -> tuple[str, bytes]:
        envelope = path.read_bytes()
        if len(envelope) < 8 + 16 + 1 + 2 + 32 + 12 + 16 or envelope[:8] != _MAGIC:
            raise BiometricTrainingError("biometric sample envelope is invalid")
        owner_length = envelope[24]
        header_length = 25 + owner_length + 32
        if not 2 <= owner_length <= 32 or len(envelope) <= header_length + 12 + 16:
            raise BiometricTrainingError("biometric sample owner is invalid")
        try:
            owner = envelope[25 : 25 + owner_length].decode("ascii")
        except UnicodeDecodeError as error:
            raise BiometricTrainingError("biometric owner label is invalid") from error
        if not owner[0].isalnum() or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in owner
        ):
            raise BiometricTrainingError("biometric owner label is invalid")
        expected_hash = envelope[25 + owner_length : header_length]
        nonce = envelope[header_length : header_length + 12]
        ciphertext = envelope[header_length + 12 :]
        try:
            caf = AESGCM(key).decrypt(nonce, ciphertext, envelope[:header_length])
        except InvalidTag as error:
            raise BiometricTrainingError("biometric sample authentication failed") from error
        if hashlib.sha256(caf).digest() != expected_hash:
            raise BiometricTrainingError("biometric sample digest mismatch")
        if len(caf) < 68 or caf[:4] != b"caff" or b"lpcm" not in caf[:64]:
            raise BiometricTrainingError("biometric sample CAF is invalid")
        return owner, caf

    def _run_trainer(self, dataset: Path, output: Path) -> None:
        completed = subprocess.run(
            (str(self._trainer_executable), str(dataset), str(output)),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=900,
            check=False,
            env={
                "HOME": str(Path.home()),
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "TMPDIR": tempfile.gettempdir(),
            },
        )
        if completed.returncode != 0 or not output.is_dir():
            raise BiometricTrainingError("speaker transfer training failed")

    def _validate_compiled_model(self, model: Path) -> None:
        completed = subprocess.run(
            (str(self._trainer_executable), "--validate-model", str(model)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise BiometricTrainingError("adapted speaker model failed validation")

    def _atomic_model_swap(self, staged_model: Path) -> None:
        if not self._active_model_path.is_dir():
            raise BiometricTrainingError("active speaker model is unavailable")
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib")
        renameatx_np = libc.renameatx_np
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            _AT_FDCWD,
            os.fsencode(staged_model),
            _AT_FDCWD,
            os.fsencode(self._active_model_path),
            _RENAME_SWAP,
        )
        if result != 0:
            raise BiometricTrainingError("atomic speaker model swap failed")
        shutil.rmtree(staged_model, ignore_errors=True)

    @staticmethod
    def _hid_idle_seconds() -> float:
        try:
            iokit = ctypes.CDLL(
                "/System/Library/Frameworks/IOKit.framework/IOKit"
            )
            core = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
            iokit.IOServiceMatching.restype = ctypes.c_void_p
            iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
            iokit.IORegistryEntryCreateCFProperty.argtypes = [
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            iokit.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
            iokit.IOObjectRelease.argtypes = [ctypes.c_uint]
            core.CFStringCreateWithCString.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            core.CFStringCreateWithCString.restype = ctypes.c_void_p
            core.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            core.CFNumberGetValue.restype = ctypes.c_bool
            core.CFRelease.argtypes = [ctypes.c_void_p]
            matching = iokit.IOServiceMatching(b"IOHIDSystem")
            service = iokit.IOServiceGetMatchingService(0, matching)
            if not service:
                return 0.0
            key = core.CFStringCreateWithCString(None, b"HIDIdleTime", 0x08000100)
            value = iokit.IORegistryEntryCreateCFProperty(service, key, None, 0)
            idle_nanoseconds = ctypes.c_longlong()
            ok = bool(value) and core.CFNumberGetValue(
                value, 4, ctypes.byref(idle_nanoseconds)
            )
            if value:
                core.CFRelease(value)
            core.CFRelease(key)
            iokit.IOObjectRelease(service)
            return max(0.0, idle_nanoseconds.value / 1_000_000_000) if ok else 0.0
        except (AttributeError, OSError, TypeError, ValueError):
            return 0.0
