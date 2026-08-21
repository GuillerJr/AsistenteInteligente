from __future__ import annotations

from pathlib import Path

from pydantic import AnyHttpUrl, Field
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
    nvidia_keychain_service: str = "ai.aegis.nvidia-nim"
    nvidia_keychain_account: str = "default"
    ipc_keychain_service: str = "ai.aegis.ipc-auth"
    ipc_keychain_account: str = "default"
    ipc_socket_path: Path = Path.home() / "Library/Application Support/Aegis/aegis.sock"
    ipc_max_frame_bytes: int = Field(default=65_536, ge=4_096, le=1_048_576)
    ipc_clock_skew_seconds: int = Field(default=30, ge=5, le=300)
    ipc_max_clients: int = Field(default=16, ge=1, le=128)
    ipc_read_timeout_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
    ipc_handler_timeout_seconds: float = Field(default=4.0, ge=0.1, le=30.0)
    ipc_max_jobs: int = Field(default=128, ge=1, le=1_024)
    audit_max_bytes: int = Field(default=16_777_216, ge=65_536, le=268_435_456)
    memory_database_path: Path = Path.home() / "Library/Application Support/Aegis/memory.sqlite3"
    memory_max_entries: int = Field(default=50_000, ge=1, le=1_000_000)
    memory_remote_embeddings_enabled: bool = False
    memory_vector_scan_limit: int = Field(default=2_000, ge=10, le=50_000)
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
