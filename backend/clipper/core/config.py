"""Application settings loaded from environment variables (prefix ``CLIPPER_``) or ``.env``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLIPPER_", env_file=".env", extra="ignore")

    # --- server ---------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    api_token: str | None = None  # when set, required as Bearer token / ?token= on every request
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    public_base_url: str | None = None  # needed for OAuth redirects and Kick webhooks
    log_level: str = "INFO"

    # --- storage --------------------------------------------------------------
    data_dir: Path = Path("./data")
    database_url: str | None = None  # default: sqlite+aiosqlite:///<data_dir>/clipper.db
    disk_min_free_gb: float = 2.0
    source_retention_days: int = 14  # moment source clips of rejected/unused moments
    secret_key: str | None = None  # Fernet key; generated into <data_dir>/secret.key if missing

    # --- workers --------------------------------------------------------------
    queue_backend: Literal["local", "redis"] = "local"
    redis_url: str = "redis://localhost:6379/0"
    render_concurrency: int = 1
    analysis_threads: int = 4
    max_streams: int = 8
    run_local_render_workers: bool = True  # disable when external redis workers render

    # --- speech recognition ---------------------------------------------------
    whisper_model: str = "small"
    whisper_device: Literal["auto", "cpu", "cuda"] = "auto"
    whisper_compute_type: str = "auto"  # int8 on cpu, float16 on cuda
    whisper_language: str | None = None  # None = auto-detect
    asr_workers: int = 1
    models_dir: Path | None = None  # default <data_dir>/models

    # --- LLM (context analysis) ----------------------------------------------
    llm_provider: Literal["none", "ollama", "openai", "anthropic"] = "none"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_base_url: str | None = None  # ollama: http://localhost:11434 ; openai-compatible: https://.../v1
    llm_api_key: str | None = None
    llm_vision: bool = False
    llm_timeout_s: float = 45.0

    # --- platforms ------------------------------------------------------------
    twitch_client_id: str | None = None
    twitch_client_secret: str | None = None
    youtube_api_key: str | None = None
    youtube_cookies_file: str | None = None  # Netscape cookies.txt of the user's own account (yt-dlp)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    kick_client_id: str | None = None
    kick_client_secret: str | None = None
    kick_unofficial_chat: bool = False
    rtmp_port: int = 1935

    # --- defaults for new channels -------------------------------------------
    default_buffer_seconds: int = 120
    default_pre_seconds: float = 45.0
    default_post_seconds: float = 12.0
    chat_reaction_delay_s: float = 6.0

    live_poll_interval_s: float = 45.0

    @property
    def resolved_models_dir(self) -> Path:
        return (self.models_dir or self.data_dir / "models").resolve()

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'clipper.db').resolve()}"


_settings: Settings | None = None


def _prepare(s: Settings) -> Settings:
    s.data_dir = s.data_dir.resolve()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = _prepare(Settings())
    return _settings


def override_settings(**kwargs) -> Settings:
    """Replace the process-wide settings (used by tests and the CLI)."""
    global _settings
    _settings = _prepare(Settings(**kwargs))
    return _settings
