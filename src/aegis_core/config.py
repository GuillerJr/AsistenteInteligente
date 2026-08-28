from __future__ import annotations

from pathlib import Path

from pydantic import AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AEGIS_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    nvidia_base_url: AnyHttpUrl = "https://integrate.api.nvidia.com/v1"
    nvidia_embedding_model_id: str = "nvidia/nemotron-3-embed-1b"
    nvidia_tts_url: AnyHttpUrl = (
        "https://877104f7-e885-42b9-8de8-f6e4c6303969.invocation.api.nvcf.nvidia.com/"
        "v1/audio/synthesize"
    )
    nvidia_tts_stream_url: AnyHttpUrl = (
        "https://877104f7-e885-42b9-8de8-f6e4c6303969.invocation.api.nvcf.nvidia.com/"
        "v1/audio/synthesize_online"
    )
    nvidia_tts_voice: str = Field(
        default="Magpie-Multilingual.ES-US.Diego",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$",
    )
    nvidia_tts_language: str = Field(default="es-US", pattern=r"^[a-z]{2}-[A-Z]{2}$")
    nvidia_tts_timeout_seconds: float = Field(default=15.0, ge=1.0, le=30.0)
    nvidia_keychain_service: str = "ai.aegis.nvidia-nim"
    nvidia_keychain_account: str = "default"
    local_foundation_api_url: AnyHttpUrl = (
        "http://127.0.0.1:9999/v1/chat/completions"
    )
    local_foundation_model_id: str = Field(
        default="foundation",
        pattern=r"^foundation$",
    )
    local_foundation_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    local_foundation_confidence_threshold: float = Field(default=0.82, ge=0.5, le=0.99)
    local_brain_executable_path: Path = (
        Path.home() / "Applications/Jarvis.app/Contents/Helpers/jarvis-local-brain"
    )
    local_brain_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    local_embedding_executable_path: Path = (
        Path.home() / "Applications/Jarvis.app/Contents/Helpers/jarvis-local-embedding"
    )
    local_embedding_timeout_seconds: float = Field(default=5.0, ge=0.5, le=20.0)
    ipc_keychain_service: str = "ai.aegis.ipc-auth"
    ipc_keychain_account: str = "default"
    ipc_socket_path: Path = Path.home() / "Library/Application Support/Aegis/aegis.sock"
    ipc_max_frame_bytes: int = Field(default=65_536, ge=4_096, le=1_048_576)
    ipc_max_message_bytes: int = Field(default=1_048_576, ge=65_536, le=16_777_216)
    ipc_clock_skew_seconds: int = Field(default=30, ge=5, le=300)
    ipc_max_clients: int = Field(default=16, ge=1, le=128)
    ipc_read_timeout_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
    ipc_write_timeout_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
    ipc_handler_timeout_seconds: float = Field(default=4.0, ge=0.1, le=30.0)
    ipc_max_jobs: int = Field(default=128, ge=1, le=1_024)
    audit_max_bytes: int = Field(default=16_777_216, ge=65_536, le=268_435_456)
    memory_database_path: Path = Path.home() / "Library/Application Support/Aegis/memory.sqlite3"
    memory_keychain_service: str = "ai.aegis.memory-aead"
    memory_keychain_account: str = "default"
    evaluation_database_path: Path = (
        Path.home() / "Library/Application Support/Aegis/evaluations.sqlite3"
    )
    evaluation_max_entries: int = Field(default=10_000, ge=20, le=100_000)
    performance_database_path: Path = (
        Path.home() / "Library/Application Support/Aegis/performance.sqlite3"
    )
    performance_max_entries: int = Field(default=10_000, ge=20, le=100_000)
    skills_directory: Path = Path.home() / "Library/Application Support/Aegis/skills"
    plugins_directory: Path = Path.home() / "Library/Application Support/Aegis/plugins"
    capability_learning_directory: Path = (
        Path.home() / "Library/Application Support/Aegis/capabilities"
    )
    memory_max_entries: int = Field(default=50_000, ge=1, le=1_000_000)
    memory_namespace_max_entries: int = Field(default=2_000, ge=1, le=50_000)
    memory_max_vectors: int = Field(default=2_000, ge=1, le=2_000)
    memory_remote_embeddings_enabled: bool = False
    memory_embedding_backfill_limit: int = Field(default=500, ge=0, le=2_000)
    memory_vector_scan_limit: int = Field(default=2_000, ge=10, le=2_000)
    memory_rag_namespace: str = Field(
        default="user.default",
        pattern=r"^[a-z][a-z0-9_.-]{0,63}$",
    )
    memory_rag_limit: int = Field(default=5, ge=1, le=10)
    memory_rag_max_context_bytes: int = Field(default=4_096, ge=512, le=16_384)
    conversation_history_turns: int = Field(default=12, ge=1, le=50)
    conversation_max_sessions: int = Field(default=1_000, ge=1, le=100_000)
    conversation_max_turns: int = Field(default=1_000, ge=2, le=10_000)
    conversation_max_context_bytes: int = Field(default=4_096, ge=512, le=16_384)
    workspace_root: Path = Path.cwd()
    request_timeout_seconds: float = Field(default=45.0, ge=1.0, le=300.0)
    job_timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    nvidia_rate_limit_cooldown_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    max_concurrency: int = Field(default=4, ge=1, le=16)
    max_output_tokens: int = Field(default=4_096, ge=64, le=65_536)

    @model_validator(mode="after")
    def ipc_message_limit_contains_legacy_frame(self) -> Settings:
        if self.ipc_max_message_bytes < self.ipc_max_frame_bytes:
            raise ValueError("IPC message limit cannot be smaller than legacy frame limit")
        return self

    @model_validator(mode="after")
    def memory_namespace_limit_fits_global_capacity(self) -> Settings:
        if self.memory_namespace_max_entries > self.memory_max_entries:
            raise ValueError("memory namespace limit cannot exceed global memory capacity")
        return self

    @field_validator("nvidia_tts_url", "nvidia_tts_stream_url")
    @classmethod
    def nvidia_tts_must_use_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("NVIDIA TTS URL must use HTTPS")
        return value

    @field_validator("local_foundation_api_url")
    @classmethod
    def local_foundation_api_must_be_fixed_loopback(
        cls,
        value: AnyHttpUrl,
    ) -> AnyHttpUrl:
        if (
            value.scheme != "http"
            or value.host not in {"127.0.0.1", "localhost"}
            or value.port != 9999
            or value.path != "/v1/chat/completions"
            or value.query is not None
            or value.fragment is not None
        ):
            raise ValueError("local Foundation Model API must use the fixed loopback endpoint")
        return value
