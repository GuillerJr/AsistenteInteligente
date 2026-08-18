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
    nvidia_keychain_service: str = "ai.aegis.nvidia-nim"
    nvidia_keychain_account: str = "default"
    ipc_keychain_service: str = "ai.aegis.ipc-auth"
    ipc_keychain_account: str = "default"
    ipc_socket_path: Path = Path.home() / "Library/Application Support/Aegis/aegis.sock"
    ipc_max_frame_bytes: int = Field(default=65_536, ge=4_096, le=1_048_576)
    ipc_clock_skew_seconds: int = Field(default=30, ge=5, le=300)
    ipc_max_clients: int = Field(default=16, ge=1, le=128)
    ipc_max_jobs: int = Field(default=128, ge=1, le=1_024)
    memory_database_path: Path = Path.home() / "Library/Application Support/Aegis/memory.sqlite3"
    memory_max_entries: int = Field(default=50_000, ge=1, le=1_000_000)
    workspace_root: Path = Path.cwd()
    request_timeout_seconds: float = Field(default=45.0, ge=1.0, le=300.0)
    max_concurrency: int = Field(default=4, ge=1, le=16)
    max_output_tokens: int = Field(default=4_096, ge=64, le=65_536)
