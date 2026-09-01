from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from aegis_core.brain.router import analyze_browser_intent, decide_browser_target
from aegis_core.contracts import (
    AgentRole,
    InputModality,
    PolicyDecision,
    ToolCall,
    ToolCallBasis,
    UserRequest,
)
from aegis_core.conversation_quality import (
    ConversationQualityEvaluator,
    ConversationQualityFlag,
)
from aegis_core.dialogue import DialogueKernel, DialogueMode
from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.graph_service import GraphRAGService
from aegis_core.memory.sqlite import MemoryNotFoundError, SQLiteMemoryStore
from aegis_core.orchestration.direct_actions import direct_tool_call
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context

SCHEMA_VERSION = "1.0"
CASES_PER_CATEGORY = 5
EXPECTED_CASES = 25


class AcceptanceExpectationError(RuntimeError):
    """One static acceptance contract was not satisfied."""


def _require(condition: bool) -> None:
    if not condition:
        raise AcceptanceExpectationError


class AcceptanceCategory(StrEnum):
    CONVERSATION = "conversation"
    BROWSER = "browser"
    SYSTEM = "system"
    MEMORY = "memory"
    SECURITY = "security"


@dataclass(frozen=True, slots=True)
class AcceptanceCaseResult:
    case_id: str
    category: AcceptanceCategory
    passed: bool
    latency_ms: int
    failure_code: str | None = None

    def private_dict(self) -> dict[str, str | int | bool | None]:
        return {
            "case_id": self.case_id,
            "category": self.category.value,
            "passed": self.passed,
            "latency_ms": self.latency_ms,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    results: tuple[AcceptanceCaseResult, ...]
    duration_ms: int

    @property
    def passed(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def score(self) -> int:
        return round((self.passed / self.total) * 100) if self.total else 0

    @property
    def gate_passed(self) -> bool:
        return self.total == EXPECTED_CASES and self.passed == EXPECTED_CASES

    def private_dict(self) -> dict[str, object]:
        category_totals = Counter(result.category.value for result in self.results)
        category_passed = Counter(result.category.value for result in self.results if result.passed)
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": "local_acceptance_contract",
            "score": self.score,
            "gate_passed": self.gate_passed,
            "passed": self.passed,
            "total": self.total,
            "duration_ms": self.duration_ms,
            "categories": {
                category.value: {
                    "passed": category_passed[category.value],
                    "total": category_totals[category.value],
                }
                for category in AcceptanceCategory
            },
            "results": [result.private_dict() for result in self.results],
            "privacy": {
                "contains_prompt_text": False,
                "contains_target_urls": False,
                "contains_transcripts": False,
                "network_calls": 0,
            },
        }

    def private_json(self) -> str:
        return json.dumps(
            self.private_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class _BenchmarkContext:
    root: Path

    def private_directory(self, name: str) -> Path:
        directory = self.root / name
        directory.mkdir(mode=0o700, exist_ok=False)
        metadata = os.lstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise PermissionError("benchmark directory is not private")
        return directory

    def memory_store(self, name: str, *, maximum: int = 50_000) -> SQLiteMemoryStore:
        directory = self.private_directory(name)
        store = SQLiteMemoryStore(
            directory / "memory.sqlite3",
            max_entries=maximum,
            max_namespace_entries=maximum,
            encryption_secret=b"a" * 32,
        )
        store.initialize()
        return store


CaseRunner = Callable[[_BenchmarkContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _AcceptanceCase:
    case_id: str
    category: AcceptanceCategory
    runner: CaseRunner


class JarvisAcceptanceBenchmark:
    """Runs 25 bounded, offline contracts without retaining scenario input."""

    def __init__(self, *, temporary_root: Path = Path("/private/tmp")) -> None:
        self._temporary_root = temporary_root
        self._cases = self._build_cases()
        self._validate_manifest()

    @property
    def manifest(self) -> tuple[tuple[str, AcceptanceCategory], ...]:
        return tuple((case.case_id, case.category) for case in self._cases)

    async def run(self) -> AcceptanceReport:
        started = time.monotonic_ns()
        with tempfile.TemporaryDirectory(
            prefix="aegis-acceptance-",
            dir=self._temporary_root,
        ) as raw_root:
            root = Path(raw_root)
            root.chmod(0o700)
            context = _BenchmarkContext(root)
            results: list[AcceptanceCaseResult] = []
            for case in self._cases:
                case_started = time.monotonic_ns()
                failure_code: str | None = None
                try:
                    await case.runner(context)
                except AcceptanceExpectationError:
                    failure_code = "expectation_failed"
                except Exception:
                    failure_code = "internal_error"
                latency_ms = max(0, (time.monotonic_ns() - case_started) // 1_000_000)
                results.append(
                    AcceptanceCaseResult(
                        case_id=case.case_id,
                        category=case.category,
                        passed=failure_code is None,
                        latency_ms=min(latency_ms, 2_147_483_647),
                        failure_code=failure_code,
                    )
                )
        duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return AcceptanceReport(tuple(results), min(duration_ms, 2_147_483_647))

    def _build_cases(self) -> tuple[_AcceptanceCase, ...]:
        definitions: tuple[tuple[str, AcceptanceCategory, CaseRunner], ...] = (
            ("conversation.mode", AcceptanceCategory.CONVERSATION, self._conversation_mode),
            ("conversation.support", AcceptanceCategory.CONVERSATION, self._support_mode),
            ("conversation.repair", AcceptanceCategory.CONVERSATION, self._repair_mode),
            ("conversation.quality", AcceptanceCategory.CONVERSATION, self._quality_gate),
            (
                "conversation.identity_guard",
                AcceptanceCategory.CONVERSATION,
                self._identity_claim_guard,
            ),
            ("browser.multiple", AcceptanceCategory.BROWSER, self._browser_multiple),
            ("browser.single", AcceptanceCategory.BROWSER, self._browser_single),
            ("browser.explicit", AcceptanceCategory.BROWSER, self._browser_explicit),
            ("browser.splice", AcceptanceCategory.BROWSER, self._browser_choice_splice),
            ("browser.non_intent", AcceptanceCategory.BROWSER, self._browser_non_intent),
            ("system.application", AcceptanceCategory.SYSTEM, self._system_application),
            ("system.audio", AcceptanceCategory.SYSTEM, self._system_audio),
            ("system.mail", AcceptanceCategory.SYSTEM, self._system_mail),
            ("system.calendar", AcceptanceCategory.SYSTEM, self._system_calendar),
            ("system.security", AcceptanceCategory.SYSTEM, self._system_security),
            ("memory.roundtrip", AcceptanceCategory.MEMORY, self._memory_roundtrip),
            ("memory.encryption", AcceptanceCategory.MEMORY, self._memory_encryption),
            ("memory.namespace", AcceptanceCategory.MEMORY, self._memory_namespace),
            ("memory.session_reset", AcceptanceCategory.MEMORY, self._memory_session_reset),
            ("memory.fifo", AcceptanceCategory.MEMORY, self._memory_fifo),
            ("security.unknown_tool", AcceptanceCategory.SECURITY, self._unknown_tool),
            ("security.role_boundary", AcceptanceCategory.SECURITY, self._role_boundary),
            ("security.secret_guard", AcceptanceCategory.SECURITY, self._secret_guard),
            ("security.biometric", AcceptanceCategory.SECURITY, self._biometric_guard),
            ("security.intent_boundary", AcceptanceCategory.SECURITY, self._intent_boundary),
        )
        return tuple(_AcceptanceCase(*definition) for definition in definitions)

    def _validate_manifest(self) -> None:
        identifiers = [case.case_id for case in self._cases]
        categories = Counter(case.category for case in self._cases)
        if (
            len(identifiers) != EXPECTED_CASES
            or len(set(identifiers)) != EXPECTED_CASES
            or any(categories[category] != CASES_PER_CATEGORY for category in AcceptanceCategory)
        ):
            raise RuntimeError("acceptance benchmark manifest is incomplete")

    @staticmethod
    async def _conversation_mode(_: _BenchmarkContext) -> None:
        guidance = DialogueKernel().classify("Hola Jarvis, conversemos")
        _require(guidance.mode is DialogueMode.CONVERSATION)
        _require(guidance.allow_follow_up)

    @staticmethod
    async def _support_mode(_: _BenchmarkContext) -> None:
        guidance = DialogueKernel().classify("Estoy frustrado y necesito desahogarme")
        _require(guidance.mode is DialogueMode.SUPPORT)
        _require(guidance.max_sentences == 5)

    @staticmethod
    async def _repair_mode(_: _BenchmarkContext) -> None:
        guidance = DialogueKernel().classify("Eso no fue lo que dije")
        _require(guidance.mode is DialogueMode.REPAIR)
        _require(not guidance.allow_follow_up)

    @staticmethod
    async def _quality_gate(_: _BenchmarkContext) -> None:
        quality = ConversationQualityEvaluator().evaluate(
            request="Me siento abrumado con el trabajo",
            response="Tiene sentido que te esté pesando. Podemos ordenar primero lo más urgente.",
        )
        _require(quality.passed)
        _require(quality.score == 100)

    @staticmethod
    async def _identity_claim_guard(_: _BenchmarkContext) -> None:
        quality = ConversationQualityEvaluator().evaluate(
            request="¿Eres una persona?",
            response="Soy humano y tengo conciencia.",
        )
        _require(not quality.passed)
        _require(ConversationQualityFlag.HUMAN_IDENTITY_CLAIM in quality.flags)

    @staticmethod
    async def _browser_multiple(_: _BenchmarkContext) -> None:
        decision = decide_browser_target(
            "Abre el navegador y busca Apple Silicon",
            installed_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
            running_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
        )
        _require(decision.requires_selection)
        _require(decision.selected_bundle_identifier is None)

    @staticmethod
    async def _browser_single(_: _BenchmarkContext) -> None:
        decision = decide_browser_target(
            "Abre el navegador y busca Apple Silicon",
            installed_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
            running_bundle_identifiers=("com.google.Chrome",),
        )
        _require(not decision.requires_selection)
        _require(decision.selected_bundle_identifier == "com.google.Chrome")

    @staticmethod
    async def _browser_explicit(_: _BenchmarkContext) -> None:
        decision = decide_browser_target(
            "Abre Safari y busca privacidad local",
            installed_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
            running_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
        )
        _require(not decision.requires_selection)
        _require(decision.selected_bundle_identifier == "com.apple.Safari")

    @staticmethod
    async def _browser_choice_splice(_: _BenchmarkContext) -> None:
        call = direct_tool_call(
            UserRequest(
                text="Abre el navegador y busca privacidad local",
                metadata={"preferred_browser_bundle_identifier": "company.thebrowser.Browser"},
            )
        )
        _require(call is not None)
        if call is None:
            return
        _require(call.tool_name == "browser_search")
        _require(call.arguments == {"query": "privacidad local", "browser": "arc"})

    @staticmethod
    async def _browser_non_intent(_: _BenchmarkContext) -> None:
        intent = analyze_browser_intent("Explícame cómo funciona un navegador")
        _require(not intent.requires_browser)
        _require(not intent.generic_target)

    @staticmethod
    async def _system_application(_: _BenchmarkContext) -> None:
        call = direct_tool_call(UserRequest(text="Abre Mail"))
        _require(call is not None)
        if call is None:
            return
        _require(call.tool_name == "application_open")
        _require(call.arguments == {"bundle_identifier": "com.apple.mail"})

    @staticmethod
    async def _system_audio(_: _BenchmarkContext) -> None:
        call = direct_tool_call(UserRequest(text="Pon el volumen al 42 por ciento"))
        _require(call is not None)
        if call is None:
            return
        _require(call.tool_name == "system_audio_set")
        _require(call.arguments == {"volume_percent": 42, "muted": None})

    @staticmethod
    async def _system_mail(_: _BenchmarkContext) -> None:
        call = direct_tool_call(UserRequest(text="Muéstrame mis correos no leídos"))
        _require(call is not None)
        if call is None:
            return
        _require(call.tool_name == "mail_list_recent")
        _require(call.arguments == {"limit": 10, "unread_only": True})

    @staticmethod
    async def _system_calendar(_: _BenchmarkContext) -> None:
        now = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
        call = direct_tool_call(UserRequest(text="Qué tengo hoy"), now=now)
        _require(call is not None)
        if call is None:
            return
        _require(call.tool_name == "calendar_list_events")
        _require(call.arguments["start_at"] == "2026-09-01T00:00:00+00:00")
        _require(call.arguments["end_at"] == "2026-09-02T00:00:00+00:00")

    @staticmethod
    async def _system_security(_: _BenchmarkContext) -> None:
        call = direct_tool_call(UserRequest(text="Revisa la postura de seguridad"))
        _require(call is not None)
        if call is None:
            return
        _require(call.tool_name == "terminal_run_template")
        _require(call.requested_by is AgentRole.CODE_SECURITY)
        _require(call.arguments == {"template": "security_posture"})

    @staticmethod
    async def _memory_roundtrip(context: _BenchmarkContext) -> None:
        store = context.memory_store("memory-roundtrip")
        record = store.put(
            namespace="user.default",
            kind=MemoryKind.PREFERENCE,
            content="El propietario prefiere respuestas breves.",
        )
        loaded = store.get(namespace="user.default", memory_id=record.memory_id)
        _require(loaded == record)
        _require(loaded.content_sha256 != loaded.content)

    @staticmethod
    async def _memory_encryption(context: _BenchmarkContext) -> None:
        store = context.memory_store("memory-encryption")
        sentinel = "Preferencia centinela privada de aceptación."
        store.put(
            namespace="user.default",
            kind=MemoryKind.PREFERENCE,
            content=sentinel,
        )
        encoded = sentinel.encode("utf-8")
        database_files = tuple(store.path.parent.glob(f"{store.path.name}*"))
        _require(bool(database_files))
        _require(all(encoded not in path.read_bytes() for path in database_files if path.is_file()))

    @staticmethod
    async def _memory_namespace(context: _BenchmarkContext) -> None:
        store = context.memory_store("memory-namespace")
        record = store.put(
            namespace="project.alpha",
            kind=MemoryKind.SEMANTIC,
            content="Arquitectura local del proyecto alfa.",
        )
        try:
            store.get(namespace="project.beta", memory_id=record.memory_id)
        except MemoryNotFoundError:
            pass
        else:
            raise AcceptanceExpectationError
        _require(store.search(namespace="project.beta", query="arquitectura local") == ())

    @staticmethod
    async def _memory_session_reset(context: _BenchmarkContext) -> None:
        store = context.memory_store("memory-session-reset")
        session = store.create_conversation(namespace="user.default")
        tag = "session." + hashlib.sha256(str(session.conversation_id).encode()).hexdigest()[:16]
        ephemeral = store.put(
            namespace="user.default",
            kind=MemoryKind.EPISODIC,
            content="Referencia musical temporal de esta sesión.",
            source=f"session:{session.conversation_id}",
            tags=(tag,),
        )
        durable = store.put(
            namespace="user.default",
            kind=MemoryKind.PREFERENCE,
            content="El propietario prefiere respuestas breves.",
            source="owner-profile",
        )
        result = await GraphRAGService(store).reset_session(
            namespace="user.default",
            session_id=session.conversation_id,
        )
        _require(result.conversation_deleted)
        _require(result.memories_deleted == 1)
        try:
            store.get(namespace="user.default", memory_id=ephemeral.memory_id)
        except MemoryNotFoundError:
            pass
        else:
            raise AcceptanceExpectationError
        _require(store.get(namespace="user.default", memory_id=durable.memory_id) == durable)

    @staticmethod
    async def _memory_fifo(context: _BenchmarkContext) -> None:
        store = context.memory_store("memory-fifo", maximum=1)
        first = store.put(
            namespace="user.default",
            kind=MemoryKind.EPISODIC,
            content="Primera memoria temporal.",
        )
        second = store.put(
            namespace="user.default",
            kind=MemoryKind.EPISODIC,
            content="Segunda memoria temporal.",
        )
        try:
            store.get(namespace="user.default", memory_id=first.memory_id)
        except MemoryNotFoundError:
            pass
        else:
            raise AcceptanceExpectationError
        _require(store.get(namespace="user.default", memory_id=second.memory_id) == second)

    @staticmethod
    async def _unknown_tool(context: _BenchmarkContext) -> None:
        call = ToolCall(
            call_id="acceptance-unknown",
            tool_name="terminal_arbitrary_shell",
            arguments={"command": "whoami"},
            requested_by=AgentRole.CODE_SECURITY,
        )
        authorization = build_default_tool_broker().authorize(
            call,
            default_policy_context(context.root),
        )
        _require(authorization.decision is PolicyDecision.DENY)
        _require(authorization.reason_code == "unknown_tool")

    @staticmethod
    async def _role_boundary(context: _BenchmarkContext) -> None:
        call = ToolCall(
            call_id="acceptance-role",
            tool_name="system_describe_runtime",
            arguments={},
            requested_by=AgentRole.ROUTER,
        )
        authorization = build_default_tool_broker().authorize(
            call,
            default_policy_context(context.root),
        )
        _require(authorization.decision is PolicyDecision.DENY)
        _require(authorization.reason_code == "role_not_allowed")

    @staticmethod
    async def _secret_guard(context: _BenchmarkContext) -> None:
        call = ToolCall(
            call_id="acceptance-secret",
            tool_name="web_research",
            arguments={"query": "revisa nvapi-private-value", "max_results": 2},
            requested_by=AgentRole.PLANNER,
        )
        authorization = build_default_tool_broker().authorize(
            call,
            default_policy_context(context.root),
        )
        _require(authorization.decision is PolicyDecision.DENY)
        _require(authorization.reason_code == "invalid_arguments")

    @staticmethod
    async def _biometric_guard(context: _BenchmarkContext) -> None:
        call = ToolCall(
            call_id="acceptance-biometric",
            tool_name="mail_send_message",
            arguments={
                "recipients": ["owner@example.com"],
                "subject": "Estado",
                "body": "Listo.",
            },
            requested_by=AgentRole.PLANNER,
        )
        request = UserRequest(
            text="Envía el correo",
            modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
            metadata={
                "speaker_identity": {"id": "owner", "confidence": 0.77},
                "owner_speaker_profile": True,
                "sole_speaker_profile": True,
            },
        )
        authorization = build_default_tool_broker().authorize(
            call,
            default_policy_context(context.root),
            request=request,
        )
        _require(authorization.decision is PolicyDecision.DENY)
        _require(authorization.reason_code == "biometric_untrusted")

    @staticmethod
    async def _intent_boundary(context: _BenchmarkContext) -> None:
        broker = build_default_tool_broker()
        policy = default_policy_context(context.root)
        reversible = ToolCall(
            call_id="acceptance-reversible",
            tool_name="application_open",
            arguments={"bundle_identifier": "com.apple.Safari"},
            requested_by=AgentRole.PLANNER,
            authorization_basis=ToolCallBasis.EXPLICIT_LOCAL_INTENT,
        )
        sensitive = ToolCall(
            call_id="acceptance-sensitive",
            tool_name="terminal_run_template",
            arguments={"template": "security_posture"},
            requested_by=AgentRole.CODE_SECURITY,
            authorization_basis=ToolCallBasis.EXPLICIT_LOCAL_INTENT,
        )
        reversible_auth = broker.authorize(reversible, policy)
        sensitive_auth = broker.authorize(sensitive, policy)
        _require(reversible_auth.decision is PolicyDecision.ALLOW)
        _require(reversible_auth.reason_code == "explicit_local_intent")
        _require(sensitive_auth.decision is PolicyDecision.REQUIRE_CONFIRMATION)
