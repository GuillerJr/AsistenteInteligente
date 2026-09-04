from __future__ import annotations

import asyncio
import os
import platform
import resource
import sqlite3
import stat
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psutil
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.memory.records import MemoryStorageMetrics
from aegis_core.runtime_state import RuntimePowerSnapshot
from aegis_core.tools.audit import AuditSink

SCHEMA_VERSION = 3
APPLICATION_ID = 0x4A505246


class PerformanceProfilerError(RuntimeError):
    """A privacy-preserving performance sample could not be trusted or persisted."""


class PerformanceSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: UUID
    recorded_at: datetime
    daemon_pid: int = Field(gt=1)
    daemon_rss_bytes: int = Field(ge=0)
    daemon_peak_rss_bytes: int = Field(ge=0)
    daemon_cpu_percent: float = Field(ge=0, le=10_000)
    cpu_per_core_percent: tuple[float, ...] = Field(max_length=256)
    thermal_state: str = Field(pattern=r"^(nominal|fair|serious|critical|unknown)$")
    low_power_mode: bool
    power_source: str = Field(
        default="unknown",
        pattern=r"^(ac|battery|ups|unknown)$",
    )
    afm_loopback_available: bool
    afm_pid: int | None = Field(default=None, gt=1)
    afm_rss_bytes: int | None = Field(default=None, ge=0)
    memory_items: int = Field(ge=0)
    memory_capacity: int = Field(gt=0)
    namespace_memory_items: int = Field(ge=0)
    namespace_memory_capacity: int = Field(gt=0)
    namespace_node_embeddings: int = Field(ge=0)
    namespace_node_embedding_capacity: int = Field(gt=0)
    graph_index_bytes: int = Field(ge=0)
    sqlite_vec_loaded: bool
    rss_growth_since_idle_baseline_bytes: int = Field(ge=0)

    @field_validator("recorded_at")
    @classmethod
    def recorded_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("performance timestamp must be timezone-aware")
        return value

    @field_validator("cpu_per_core_percent")
    @classmethod
    def per_core_utilization_is_bounded(
        cls,
        value: tuple[float, ...],
    ) -> tuple[float, ...]:
        if any(not 0 <= item <= 100 for item in value):
            raise ValueError("per-core CPU utilization is out of range")
        return value

    @model_validator(mode="after")
    def related_metrics_are_consistent(self) -> PerformanceSample:
        if self.daemon_peak_rss_bytes < self.daemon_rss_bytes:
            raise ValueError("peak RSS cannot be lower than resident RSS")
        if self.memory_items > self.memory_capacity:
            raise ValueError("memory count exceeds configured capacity")
        if self.namespace_memory_items > self.namespace_memory_capacity:
            raise ValueError("namespace memory count exceeds configured capacity")
        if self.namespace_node_embeddings > self.namespace_node_embedding_capacity:
            raise ValueError("namespace graph embedding count exceeds configured capacity")
        expected_graph_bytes = self.namespace_node_embeddings * 384 * 4
        if self.graph_index_bytes != expected_graph_bytes:
            raise ValueError("graph index byte estimate is inconsistent")
        if self.afm_loopback_available != (
            self.afm_pid is not None and self.afm_rss_bytes is not None
        ):
            raise ValueError("AFM availability and process metrics do not match")
        return self


class SpeculativeTransactionMetric(BaseModel):
    """Content-free latency record for one local-draft/cloud-verify transaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_id: UUID
    recorded_at: datetime
    route: str = Field(
        pattern=r"^(verified_remote|local_timeout|local_verifier_error|local_policy)$"
    )
    verifier_model_id: str = Field(
        min_length=3,
        max_length=256,
        pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$",
    )
    active_first_partial_ms: int = Field(ge=0, le=600_000)
    active_total_ms: int = Field(ge=0, le=600_000)
    wall_time_ms: int = Field(ge=0, le=600_000)
    local_draft_ms: int = Field(ge=0, le=600_000)
    verifier_ms: int = Field(ge=0, le=600_000)
    accepted_draft_tokens: int = Field(ge=0, le=65_536)
    local_fallback: bool
    thermal_throttled: bool

    @field_validator("recorded_at")
    @classmethod
    def metric_timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speculative metric timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def latency_values_are_consistent(self) -> SpeculativeTransactionMetric:
        if self.active_first_partial_ms > self.active_total_ms:
            raise ValueError("first partial latency exceeds active latency")
        if self.active_total_ms > self.wall_time_ms:
            raise ValueError("active latency exceeds wall latency")
        if self.local_draft_ms > self.active_total_ms:
            raise ValueError("local draft latency exceeds active latency")
        if self.verifier_ms > self.active_total_ms:
            raise ValueError("verifier latency exceeds active latency")
        if self.local_fallback != self.route.startswith("local_"):
            raise ValueError("speculative fallback route is inconsistent")
        return self


class PerformanceAnalyticsStore:
    """Private, allow-list-only telemetry ledger with bounded retention."""

    def __init__(self, path: Path, *, max_entries: int = 10_000) -> None:
        if not 20 <= max_entries <= 100_000:
            raise ValueError("performance retention is out of range")
        self._path = path
        self._max_entries = max_entries
        self._expected_uid = os.getuid()
        self._lock = threading.RLock()
        self._initialized = False
        self._directory_identity: tuple[int, int] | None = None
        self._database_identity: tuple[int, int] | None = None

    def initialize(self) -> None:
        with self._lock:
            self._prepare_private_directory()
            self._prepare_database_file()
            with self._connect() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                application_id = int(
                    connection.execute("PRAGMA application_id").fetchone()[0]
                )
                if version == 0:
                    existing = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                        ).fetchone()[0]
                    )
                    if application_id != 0 or existing:
                        raise PerformanceProfilerError(
                            "refusing to modify an unrelated performance database"
                        )
                    connection.executescript(
                        f"""
                        PRAGMA application_id = {APPLICATION_ID};
                        PRAGMA user_version = {SCHEMA_VERSION};
                        CREATE TABLE performance_samples (
                            sample_id TEXT PRIMARY KEY NOT NULL,
                            recorded_at TEXT NOT NULL,
                            payload_json TEXT NOT NULL
                        ) STRICT;
                        CREATE INDEX performance_samples_recorded_at
                        ON performance_samples(recorded_at DESC, sample_id DESC);
                        CREATE TABLE speculative_transactions (
                            transaction_id TEXT PRIMARY KEY NOT NULL,
                            recorded_at TEXT NOT NULL,
                            route TEXT NOT NULL,
                            verifier_model_id TEXT NOT NULL,
                            active_first_partial_ms INTEGER NOT NULL,
                            active_total_ms INTEGER NOT NULL,
                            wall_time_ms INTEGER NOT NULL,
                            local_draft_ms INTEGER NOT NULL,
                            verifier_ms INTEGER NOT NULL,
                            accepted_draft_tokens INTEGER NOT NULL,
                            local_fallback INTEGER NOT NULL,
                            thermal_throttled INTEGER NOT NULL
                        ) STRICT;
                        CREATE INDEX speculative_transactions_recorded_at
                        ON speculative_transactions(recorded_at DESC, transaction_id DESC);
                        """
                    )
                elif version == 1 and application_id == APPLICATION_ID:
                    connection.executescript(
                        """
                        BEGIN IMMEDIATE;
                        CREATE TABLE speculative_transactions (
                            transaction_id TEXT PRIMARY KEY NOT NULL,
                            recorded_at TEXT NOT NULL,
                            route TEXT NOT NULL,
                            verifier_model_id TEXT NOT NULL,
                            active_first_partial_ms INTEGER NOT NULL,
                            active_total_ms INTEGER NOT NULL,
                            wall_time_ms INTEGER NOT NULL,
                            local_draft_ms INTEGER NOT NULL,
                            verifier_ms INTEGER NOT NULL,
                            accepted_draft_tokens INTEGER NOT NULL,
                            local_fallback INTEGER NOT NULL,
                            thermal_throttled INTEGER NOT NULL
                        ) STRICT;
                        CREATE INDEX speculative_transactions_recorded_at
                        ON speculative_transactions(recorded_at DESC, transaction_id DESC);
                        PRAGMA user_version = 2;
                        COMMIT;
                        """
                    )
                    version = 2
                if version == 2 and application_id == APPLICATION_ID:
                    connection.executescript(
                        f"""
                        BEGIN IMMEDIATE;
                        UPDATE performance_samples
                        SET payload_json = json_remove(
                            json_set(
                                payload_json,
                                '$.namespace_node_embeddings',
                                    COALESCE(json_extract(payload_json, '$.namespace_vectors'), 0),
                                '$.namespace_node_embedding_capacity',
                                    COALESCE(
                                        json_extract(payload_json, '$.namespace_vector_capacity'),
                                        2000
                                    ),
                                '$.graph_index_bytes',
                                    COALESCE(
                                        json_extract(payload_json, '$.namespace_vectors'), 0
                                    ) * 1536
                            ),
                            '$.namespace_vectors',
                            '$.namespace_vector_capacity',
                            '$.sqlite_vec_probe_growth_bytes',
                            '$.soak_cycles',
                            '$.soak_rss_growth_bytes',
                            '$.soak_peak_growth_bytes',
                            '$.soak_budget_bytes',
                            '$.soak_budget_passed'
                        );
                        PRAGMA user_version = {SCHEMA_VERSION};
                        COMMIT;
                        """
                    )
                elif version not in {0, SCHEMA_VERSION} or application_id not in {
                    0,
                    APPLICATION_ID,
                }:
                    raise PerformanceProfilerError("performance database identity is invalid")
                self._verify_schema(connection)
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise PerformanceProfilerError(
                        "performance database integrity check failed"
                    )
            self._secure_database_file()
            self._initialized = True

    def append(self, sample: PerformanceSample) -> None:
        self._require_initialized()
        canonical_time = sample.recorded_at.astimezone(UTC).isoformat()
        payload = sample.model_dump_json()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO performance_samples(sample_id, recorded_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (str(sample.sample_id), canonical_time, payload),
            )
            connection.execute(
                """
                DELETE FROM performance_samples
                WHERE sample_id IN (
                    SELECT sample_id FROM performance_samples
                    ORDER BY recorded_at DESC, sample_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self._max_entries,),
            )
        self._secure_database_file()

    def load_recent(self, *, limit: int = 100) -> tuple[PerformanceSample, ...]:
        self._require_initialized()
        if not 1 <= limit <= min(self._max_entries, 1_000):
            raise ValueError("performance sample limit is out of range")
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM performance_samples
                ORDER BY recorded_at DESC, sample_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        try:
            return tuple(
                PerformanceSample.model_validate_json(row["payload_json"]) for row in rows
            )
        except (ValueError, TypeError) as error:
            raise PerformanceProfilerError("stored performance sample is invalid") from error

    def record_speculative_transaction(
        self,
        metric: SpeculativeTransactionMetric,
    ) -> None:
        self._require_initialized()
        canonical_time = metric.recorded_at.astimezone(UTC).isoformat()
        values = (
            str(metric.transaction_id),
            canonical_time,
            metric.route,
            metric.verifier_model_id,
            metric.active_first_partial_ms,
            metric.active_total_ms,
            metric.wall_time_ms,
            metric.local_draft_ms,
            metric.verifier_ms,
            metric.accepted_draft_tokens,
            int(metric.local_fallback),
            int(metric.thermal_throttled),
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO speculative_transactions(
                    transaction_id, recorded_at, route, verifier_model_id,
                    active_first_partial_ms, active_total_ms, wall_time_ms,
                    local_draft_ms, verifier_ms, accepted_draft_tokens,
                    local_fallback, thermal_throttled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            connection.execute(
                """
                DELETE FROM speculative_transactions
                WHERE transaction_id IN (
                    SELECT transaction_id FROM speculative_transactions
                    ORDER BY recorded_at DESC, transaction_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self._max_entries,),
            )
        self._secure_database_file()

    def load_recent_speculative(
        self,
        *,
        limit: int = 100,
    ) -> tuple[SpeculativeTransactionMetric, ...]:
        self._require_initialized()
        if not 1 <= limit <= min(self._max_entries, 1_000):
            raise ValueError("speculative metric limit is out of range")
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT * FROM speculative_transactions
                ORDER BY recorded_at DESC, transaction_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        try:
            return tuple(
                SpeculativeTransactionMetric(
                    transaction_id=row["transaction_id"],
                    recorded_at=row["recorded_at"],
                    route=row["route"],
                    verifier_model_id=row["verifier_model_id"],
                    active_first_partial_ms=row["active_first_partial_ms"],
                    active_total_ms=row["active_total_ms"],
                    wall_time_ms=row["wall_time_ms"],
                    local_draft_ms=row["local_draft_ms"],
                    verifier_ms=row["verifier_ms"],
                    accepted_draft_tokens=row["accepted_draft_tokens"],
                    local_fallback=bool(row["local_fallback"]),
                    thermal_throttled=bool(row["thermal_throttled"]),
                )
                for row in rows
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise PerformanceProfilerError("stored speculative metric is invalid") from error

    def _prepare_private_directory(self) -> None:
        directory = self._path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = os.lstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != self._expected_uid
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise PerformanceProfilerError("performance directory is not private")
        self._directory_identity = (metadata.st_dev, metadata.st_ino)

    def _prepare_database_file(self) -> None:
        try:
            descriptor = os.open(
                self._path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._secure_database_file()

    def _secure_database_file(self) -> None:
        metadata = os.lstat(self._path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self._expected_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o177
        ):
            raise PerformanceProfilerError("performance database is not private")
        identity = (metadata.st_dev, metadata.st_ino)
        if self._database_identity is not None and identity != self._database_identity:
            raise PerformanceProfilerError("performance database identity changed")
        self._database_identity = identity

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        self._verify_identity()
        target: str | Path = self._path
        if read_only:
            target = f"file:{self._path}?mode=ro"
        connection = sqlite3.connect(
            target,
            uri=read_only,
            timeout=2,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA busy_timeout = 2000")
        if read_only:
            connection.execute("PRAGMA query_only = ON")
        else:
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _verify_identity(self) -> None:
        if self._directory_identity is None or self._database_identity is None:
            return
        directory = os.lstat(self._path.parent)
        database = os.lstat(self._path)
        if self._directory_identity != (directory.st_dev, directory.st_ino):
            raise PerformanceProfilerError("performance directory identity changed")
        if self._database_identity != (database.st_dev, database.st_ino):
            raise PerformanceProfilerError("performance database identity changed")

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        objects = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
            if not row["name"].startswith("sqlite_")
        }
        if objects != {
            "performance_samples",
            "performance_samples_recorded_at",
            "speculative_transactions",
            "speculative_transactions_recorded_at",
        }:
            raise PerformanceProfilerError("performance schema is invalid")

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise PerformanceProfilerError("performance store is not initialized")


class PerformanceProfiler:
    """On-demand system profiler; it never records prompts, URLs, or transcripts."""

    def __init__(
        self,
        store: PerformanceAnalyticsStore,
        *,
        memory_probe: Callable[[], MemoryStorageMetrics],
        thermal_probe: Callable[[], RuntimePowerSnapshot | None],
    ) -> None:
        self._store = store
        self._memory_probe = memory_probe
        self._thermal_probe = thermal_probe
        self._process = psutil.Process(os.getpid())
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        self._process.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        self._idle_baseline_rss_bytes = int(self._process.memory_info().rss)

    async def collect(self) -> PerformanceSample:
        async with self._async_lock:
            thermal = self._thermal_probe()
            return await asyncio.to_thread(self._collect_sync, thermal)

    def _collect_sync(
        self,
        thermal: RuntimePowerSnapshot | None,
    ) -> PerformanceSample:
        try:
            with self._lock:
                memory = self._memory_probe()
                rss_after_probe = int(self._process.memory_info().rss)
                usage = resource.getrusage(resource.RUSAGE_SELF)
                peak_rss = int(usage.ru_maxrss)
                if platform.system() != "Darwin":
                    peak_rss *= 1_024
                per_core = tuple(
                    round(max(0.0, float(value)), 3)
                    for value in psutil.cpu_percent(interval=None, percpu=True)
                )
                daemon_cpu = round(
                    max(0.0, float(self._process.cpu_percent(interval=None))),
                    3,
                )
                afm_pid, afm_rss = self._afm_loopback_process()
                sample = PerformanceSample(
                    sample_id=uuid4(),
                    recorded_at=datetime.now(UTC),
                    daemon_pid=self._process.pid,
                    daemon_rss_bytes=rss_after_probe,
                    daemon_peak_rss_bytes=max(peak_rss, rss_after_probe),
                    daemon_cpu_percent=daemon_cpu,
                    cpu_per_core_percent=per_core,
                    thermal_state=(thermal.thermal_state if thermal is not None else "unknown"),
                    low_power_mode=(thermal.low_power_mode if thermal is not None else False),
                    power_source=(thermal.power_source if thermal is not None else "unknown"),
                    afm_loopback_available=afm_pid is not None,
                    afm_pid=afm_pid,
                    afm_rss_bytes=afm_rss,
                    memory_items=memory.memory_items,
                    memory_capacity=memory.memory_capacity,
                    namespace_memory_items=memory.namespace_memory_items,
                    namespace_memory_capacity=memory.namespace_memory_capacity,
                    namespace_node_embeddings=memory.namespace_node_embeddings,
                    namespace_node_embedding_capacity=(
                        memory.namespace_node_embedding_capacity
                    ),
                    graph_index_bytes=memory.graph_index_bytes,
                    sqlite_vec_loaded=memory.sqlite_vec_loaded,
                    rss_growth_since_idle_baseline_bytes=max(
                        0,
                        rss_after_probe - self._idle_baseline_rss_bytes,
                    ),
                )
                self._store.append(sample)
                return sample
        except Exception as error:
            raise PerformanceProfilerError("performance sample collection failed") from error

    @staticmethod
    def _afm_loopback_process() -> tuple[int | None, int | None]:
        try:
            connections = psutil.net_connections(kind="tcp")
        except (OSError, psutil.Error):
            return None, None
        current_uid = os.getuid()
        for connection in connections:
            local = connection.laddr
            if (
                connection.status != psutil.CONN_LISTEN
                or connection.pid is None
                or not local
                or local.port != 9999
                or local.ip not in {"127.0.0.1", "::1"}
            ):
                continue
            try:
                process = psutil.Process(connection.pid)
                if process.uids().real != current_uid:
                    continue
                return process.pid, int(process.memory_info().rss)
            except (OSError, psutil.Error):
                continue
        return None, None


class PerformanceIpcService:
    SNAPSHOT_METHOD = "performance.snapshot"

    def __init__(self, profiler: PerformanceProfiler, audit_sink: AuditSink) -> None:
        self._profiler = profiler
        self._audit = audit_sink

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.SNAPSHOT_METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method == self.SNAPSHOT_METHOD:
            if request.payload:
                return IpcHandlerResult(ok=False, error_code="invalid_payload")
            operation = "snapshot"
            try:
                sample = await self._profiler.collect()
            except PerformanceProfilerError:
                self._record_failure(request, operation)
                return IpcHandlerResult(ok=False, error_code="performance_probe_failed")
            self._record_success(request, operation)
            return IpcHandlerResult(
                ok=True,
                payload=sample.model_dump(mode="json"),
            )
        return IpcHandlerResult(ok=False, error_code="method_not_found")

    def _record_success(self, request: IpcRequest, operation: str) -> None:
        self._audit.record_system_event(
            request.request_id,
            event_type="performance_profile_completed",
            component="performance_profiler",
            call_id=request.nonce,
            data={
                "operation": operation,
                "persisted": True,
                "contains_user_content": False,
            },
        )

    def _record_failure(self, request: IpcRequest, operation: str) -> None:
        self._audit.record_system_event(
            request.request_id,
            event_type="performance_profile_failed",
            component="performance_profiler",
            call_id=request.nonce,
            data={
                "operation": operation,
                "persisted": False,
                "contains_user_content": False,
            },
        )
