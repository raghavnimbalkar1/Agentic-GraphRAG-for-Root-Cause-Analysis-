"""
core/config.py

Single source of truth for all project configuration.
Loaded once at startup from .env — import `settings` everywhere.

Usage:
    from core.config import settings
    print(settings.neo4j_uri)
"""

from enum import Enum
from pathlib import Path
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Enums ──────────────────────────────────────────────────────────────────

class LLMProvider(str, Enum):
    OPENAI    = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA    = "ollama"
    GEMINI    = "gemini"          



class LogLevel(str, Enum):
    DEBUG   = "DEBUG"
    INFO    = "INFO"
    WARNING = "WARNING"
    ERROR   = "ERROR"


# ── Settings ───────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    """
    All configuration loaded from environment variables / .env file.
    Fields with no default are REQUIRED — startup fails immediately if missing.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # silently ignore unknown env vars
    )

    # ── LLM ───────────────────────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.OPENAI
    llm_model: str             = "gpt-4o"
    openai_api_key: str        = Field(default="", repr=False)
    anthropic_api_key: str     = Field(default="", repr=False)
    google_api_key: str = Field(default="", repr=False)

    # ── Ollama (local or remote) ──────────────────────────────────
    ollama_base_url: str = "http://100.111.119.121:11434"

    # ── Neo4j ─────────────────────────────────────────────────
    neo4j_uri: str      = "bolt://localhost:7687"
    neo4j_user: str     = "neo4j"
    neo4j_password: str = Field(..., repr=False)   # required — no default

    # ── Docker sandbox ────────────────────────────────────────
    # Local dev: unix:///var/run/docker.sock
    # Inside agent container (DinD): tcp://docker-daemon:2375
    docker_host: str        = "unix:///var/run/docker.sock"
    sop_executor_image: str = "sop-executor:latest"

    # ── Agent behaviour ───────────────────────────────────────
    agent_max_attempts: int = Field(default=5, ge=1, le=20)
    alert_listen_port: int  = Field(default=8888, ge=1024, le=65535)

    # ── Logging ───────────────────────────────────────────────
    log_level: LogLevel = LogLevel.INFO

    # ── Filesystem paths ──────────────────────────────────────
    sops_dir: Path  = Path("sops")
    audit_dir: Path = Path("audit")

    # ── Validators ────────────────────────────────────────────
    @field_validator("openai_api_key")
    @classmethod
    def warn_missing_openai_key(cls, v: str, info) -> str:
        # Only warn — don't fail, because Ollama users have no key
        if not v:
            import warnings
            warnings.warn(
                "OPENAI_API_KEY is empty. Fine if using Ollama or Anthropic.",
                stacklevel=2,
            )
        return v

    @field_validator("sops_dir", "audit_dir", mode="after")
    @classmethod
    def ensure_dirs_exist(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        return v

    # ── Computed helpers ──────────────────────────────────────
    @property
    def is_local_llm(self) -> bool:
        return self.llm_provider == LLMProvider.OLLAMA

    @property
    def neo4j_auth(self) -> tuple[str, str]:
        return (self.neo4j_user, self.neo4j_password)


# ── Singleton ─────────────────────────────────────────────────────────────
# Cached so .env is only parsed once per process.

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Module-level singleton — import this everywhere.
settings: Settings = get_settings()