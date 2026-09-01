from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import psutil
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.providers.mlx_provider import MLXProviderError
from aegis_core.secrets import MacOSIpcSecret, SecretNotFoundError
from aegis_core.tools.audit import AuditSink, NullAuditSink

_MAGIC = b"AEGBIO1\0"
_MAX_ENVELOPE_BYTES = 2 * 1_024 * 1_024
_RENAME_SWAP = 0x00000002
_AT_FDCWD = -2
_CONFIRMATION_TTL_SECONDS = 15 * 60
_MAX_CONFIRMATION_REPLAYS = 512
_CONFIRMATION_OPERATORS = frozenset(
    {"si", "confirmo", "adelante", "procede", "aprobado", "dale", "autorizo", "ok"}
)
_SPEAKER_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_SPANISH_VOICE_LINE = re.compile(r"^(.+?)\s+(es_[A-Z]{2})\s+#")
_DISTRACTOR_PHRASES = (
    "Jarvis, confirma",
    "Adelante, aprobado",
    "Sí, dale",
)
_MINIMUM_DISTRACTOR_FILES = 15


class BiometricTrainingError(RuntimeError):
    """A validated local biometric adaptation cycle failed closed."""


@dataclass(frozen=True, slots=True)
class SynthesizedDistractor:
    path: Path
    voice: str
    phrase_index: int


class AdversarialDistractorGenerator:
    """Build a bounded, local-only negative speaker dataset with macOS voices."""

    def __init__(
        self,
        destination: Path,
        *,
        say_executable: Path = Path("/usr/bin/say"),
    ) -> None:
        self._destination = destination
        self._say = say_executable

    async def generate(self) -> tuple[SynthesizedDistractor, ...]:
        self._prepare_destination()
        voices = await self._available_spanish_voices()
        if len(voices) < 2:
            raise BiometricTrainingError("fewer than two Spanish TTS voices are available")
        generated: list[SynthesizedDistractor] = []
        for voice_index, voice in enumerate(voices):
            for phrase_index, phrase in enumerate(_DISTRACTOR_PHRASES):
                if len(generated) >= _MINIMUM_DISTRACTOR_FILES:
                    return tuple(generated)
                digest = hashlib.sha256(f"{voice}\0{phrase}\0{voice_index}".encode()).hexdigest()[
                    :16
                ]
                target = self._destination / f"negative-{len(generated):02d}-{digest}.caf"
                if self._valid_existing_caf(target):
                    generated.append(SynthesizedDistractor(target, voice, phrase_index))
                    continue
                temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp.caf")
                process = await asyncio.create_subprocess_exec(
                    str(self._say),
                    "-v",
                    voice,
                    "-r",
                    str((165, 180, 195)[phrase_index]),
                    "--data-format=LEI16@16000",
                    "-o",
                    str(temporary),
                    phrase,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env={
                        "HOME": str(Path.home()),
                        "LANG": "es_ES.UTF-8",
                        "PATH": "/usr/bin:/bin",
                        "TMPDIR": tempfile.gettempdir(),
                    },
                )
                try:
                    await asyncio.wait_for(process.wait(), timeout=20)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                if process.returncode == 0 and self._valid_existing_caf(temporary):
                    temporary.chmod(0o600)
                    os.replace(temporary, target)
                    generated.append(SynthesizedDistractor(target, voice, phrase_index))
                else:
                    temporary.unlink(missing_ok=True)
            await asyncio.sleep(0.1)
        if len(generated) < _MINIMUM_DISTRACTOR_FILES:
            raise BiometricTrainingError("Spanish TTS distractor generation was incomplete")
        return tuple(generated)

    async def _available_spanish_voices(self) -> tuple[str, ...]:
        if self._say != Path("/usr/bin/say") or not self._say.is_file():
            raise BiometricTrainingError("macOS say executable is unavailable")
        process = await asyncio.create_subprocess_exec(
            str(self._say),
            "-v",
            "?",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise BiometricTrainingError("macOS voice enumeration timed out") from error
        if process.returncode != 0 or len(stdout) > 1_048_576:
            raise BiometricTrainingError("macOS voice enumeration failed")
        names: list[str] = []
        for line in stdout.decode("utf-8", errors="strict").splitlines():
            match = _SPANISH_VOICE_LINE.match(line)
            if match is not None and match.group(1) not in names:
                names.append(match.group(1))
        preferred = ("Diego", "Jorge", "Mónica", "Monica", "Paulina")
        names.sort(
            key=lambda name: (
                name not in preferred,
                preferred.index(name) if name in preferred else name,
            )
        )
        return tuple(names)

    def _prepare_destination(self) -> None:
        try:
            status = self._destination.stat(follow_symlinks=False)
        except FileNotFoundError:
            self._destination.mkdir(mode=0o700, parents=True, exist_ok=False)
            status = self._destination.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != os.getuid()
        ):
            raise BiometricTrainingError("distractor directory is not private")
        if status.st_mode & 0o077:
            self._destination.chmod(0o700)
            status = self._destination.stat(follow_symlinks=False)
            if status.st_mode & 0o077:
                raise BiometricTrainingError("distractor directory permissions are unsafe")

    @staticmethod
    def _valid_existing_caf(path: Path) -> bool:
        try:
            status = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(status.st_mode)
                or path.is_symlink()
                or status.st_size < 68
                or status.st_size > 5 * 1_024 * 1_024
            ):
                return False
            with path.open("rb") as handle:
                header = handle.read(64)
            return header.startswith(b"caff")
        except OSError:
            return False


@dataclass(frozen=True, slots=True)
class VoiceConfirmationResult:
    speaker_verified: bool
    semantic_verified: bool

    @property
    def authorized(self) -> bool:
        return self.speaker_verified and self.semantic_verified


class ConfirmationTranscriber(Protocol):
    async def transcribe_pcm_s16le(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16_000,
    ) -> str: ...


class VoiceConfirmationVerifier:
    """Fail-closed semantic approval with a bounded in-memory replay cache."""

    def __init__(
        self,
        transcriber: ConfirmationTranscriber,
        *,
        audit_sink: AuditSink | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transcriber = transcriber
        self._audit = audit_sink or NullAuditSink()
        self._clock = monotonic_clock
        self._recent: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    async def verify(
        self,
        pcm: bytes,
        *,
        speaker_identifier: str,
        speaker_confidence: float,
        owner_profile_match: bool,
    ) -> VoiceConfirmationResult:
        if (
            len(pcm) % 2
            or not 8_000 <= len(pcm) <= 96_000
            or _SPEAKER_IDENTIFIER.fullmatch(speaker_identifier) is None
            or not math.isfinite(speaker_confidence)
        ):
            return await self._reject("invalid_evidence")
        digest = hashlib.sha256(pcm).hexdigest()
        async with self._lock:
            now = self._clock()
            self._expire_locked(now)
            if digest in self._recent:
                return await self._reject("replay_detected")
            self._recent[digest] = now + _CONFIRMATION_TTL_SECONDS
            self._recent.move_to_end(digest)
            while len(self._recent) > _MAX_CONFIRMATION_REPLAYS:
                self._recent.popitem(last=False)
        speaker_verified = (
            owner_profile_match
            and speaker_identifier.casefold() not in {"unknown", "untrusted", "background"}
            and speaker_confidence >= 0.78
        )
        if not speaker_verified:
            return await self._reject("speaker_mismatch")
        try:
            transcript = await self._transcriber.transcribe_pcm_s16le(pcm)
        except MLXProviderError:
            return await self._reject("transcription_failed")
        semantic_verified = self._normalized_operator(transcript) in _CONFIRMATION_OPERATORS
        if not semantic_verified:
            return await self._reject("semantic_mismatch", speaker_verified=True)
        self._audit.record_system_event(
            uuid4(),
            event_type="voice_confirmation_verified",
            component="voice_authorization",
            data={
                "speaker_verified": True,
                "semantic_verified": True,
                "transcript_recorded": False,
            },
        )
        return VoiceConfirmationResult(True, True)

    async def _reject(
        self,
        reason: str,
        *,
        speaker_verified: bool = False,
    ) -> VoiceConfirmationResult:
        self._audit.record_system_event(
            uuid4(),
            event_type="voice_confirmation_rejected",
            component="voice_authorization",
            data={
                "reason": reason,
                "speaker_verified": speaker_verified,
                "semantic_verified": False,
                "audio_hash_recorded": False,
                "transcript_recorded": False,
            },
        )
        return VoiceConfirmationResult(speaker_verified, False)

    def _expire_locked(self, now: float) -> None:
        while self._recent:
            _, expires_at = next(iter(self._recent.items()))
            if expires_at > now:
                break
            self._recent.popitem(last=False)

    @staticmethod
    def _normalized_operator(text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text.casefold())
        without_marks = "".join(
            character for character in decomposed if not unicodedata.combining(character)
        )
        return " ".join(re.sub(r"[^a-z0-9\s]", " ", without_marks).split())


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
        distractor_directory: Path | None = None,
        enrollment_directory: Path,
        active_model_path: Path,
        trainer_executable: Path,
        calibrator_executable: Path | None = None,
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
        self._distractor_directory = distractor_directory or training_directory / "Distractors"
        self._enrollment_directory = enrollment_directory
        self._active_model_path = active_model_path
        self._trainer_executable = trainer_executable
        self._calibrator_executable = calibrator_executable
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

    async def harden_with_distractors(self) -> bool:
        """Retrain and swap only after the staged model passes adversarial calibration."""
        result = await asyncio.to_thread(self._harden_with_distractors_blocking)
        self._audit.record_system_event(
            uuid4(),
            event_type="biometric_adversarial_hardening",
            component="biometric_training",
            data={"accepted": result, "distractor_count": _MINIMUM_DISTRACTOR_FILES},
        )
        return result

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
            self._copy_distractor_dataset(dataset / "background")
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

    def _harden_with_distractors_blocking(self) -> bool:
        self._validate_private_inputs(require_calibrator=True)
        owner_identifier = self._sole_enrolled_owner()
        if self._calibrator_executable is None:
            raise BiometricTrainingError("biometric calibrator is unavailable")
        with tempfile.TemporaryDirectory(prefix="aegis-biometric-hardening-") as raw_temp:
            root = Path(raw_temp)
            root.chmod(0o700)
            dataset = root / "dataset"
            self._copy_enrollment_dataset(dataset)
            self._copy_distractor_dataset(dataset / "background")
            staged_parent = root / "staged"
            staged_parent.mkdir(mode=0o700)
            staged_model = staged_parent / "JarvisSpeakerIdentity.mlmodelc"
            self._run_trainer(dataset, staged_model)
            self._validate_compiled_model(staged_model)
            report = self._run_adversarial_calibrator(
                owner_identifier,
                staged_model,
            )
            if report.get("accepted") is not True:
                return False
            self._atomic_model_swap(staged_model)
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

    def _validate_private_inputs(self, *, require_calibrator: bool = False) -> None:
        inputs = [
            (self._training_directory, False),
            (self._enrollment_directory, False),
            (self._trainer_executable, True),
        ]
        if self._distractor_directory.exists():
            inputs.append((self._distractor_directory, False))
        if require_calibrator:
            if self._calibrator_executable is None:
                raise BiometricTrainingError("biometric calibrator is unavailable")
            inputs.append((self._calibrator_executable, True))
        for path, executable in inputs:
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

    def _copy_distractor_dataset(self, background: Path) -> None:
        try:
            entries = tuple(sorted(self._distractor_directory.glob("*.caf")))
        except OSError as error:
            raise BiometricTrainingError("speaker distractors are unavailable") from error
        valid = [
            source
            for source in entries[:64]
            if AdversarialDistractorGenerator._valid_existing_caf(source)
            and source.stat(follow_symlinks=False).st_uid == os.getuid()
            and not source.stat(follow_symlinks=False).st_mode & 0o077
        ]
        if len(valid) < _MINIMUM_DISTRACTOR_FILES:
            raise BiometricTrainingError("speaker distractor dataset is incomplete")
        background.mkdir(mode=0o700, parents=True, exist_ok=True)
        for index, source in enumerate(valid):
            target = background / f"synthetic-negative-{index:02d}.caf"
            shutil.copyfile(source, target)
            target.chmod(0o600)

    def _sole_enrolled_owner(self) -> str:
        try:
            candidates = tuple(
                path.name
                for path in self._enrollment_directory.iterdir()
                if path.is_dir()
                and not path.is_symlink()
                and path.name != "background"
                and _SPEAKER_IDENTIFIER.fullmatch(path.name) is not None
            )
        except OSError as error:
            raise BiometricTrainingError("speaker enrollment is unavailable") from error
        if len(candidates) != 1:
            raise BiometricTrainingError("a sole enrolled owner is required")
        return candidates[0]

    def _run_adversarial_calibrator(
        self,
        owner_identifier: str,
        model_path: Path,
    ) -> dict[str, object]:
        assert self._calibrator_executable is not None
        completed = subprocess.run(
            (
                str(self._calibrator_executable),
                owner_identifier,
                str(model_path),
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=120,
            check=False,
            env={
                "HOME": str(Path.home()),
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "TMPDIR": tempfile.gettempdir(),
            },
        )
        if len(completed.stdout) > 4_096:
            raise BiometricTrainingError("biometric calibration output is oversized")
        try:
            report = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BiometricTrainingError("biometric calibration output is invalid") from error
        expected_keys = {
            "accepted",
            "threshold",
            "maximumDistractorConfidence",
            "minimumOwnerConfidence",
            "distractorCount",
            "ownerCount",
        }
        if (
            completed.returncode not in {0, 2}
            or not isinstance(report, dict)
            or set(report) != expected_keys
            or report.get("distractorCount") != _MINIMUM_DISTRACTOR_FILES
        ):
            raise BiometricTrainingError("biometric calibration failed")
        return report

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
            iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
            core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
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
            ok = bool(value) and core.CFNumberGetValue(value, 4, ctypes.byref(idle_nanoseconds))
            if value:
                core.CFRelease(value)
            core.CFRelease(key)
            iokit.IOObjectRelease(service)
            return max(0.0, idle_nanoseconds.value / 1_000_000_000) if ok else 0.0
        except (AttributeError, OSError, TypeError, ValueError):
            return 0.0
