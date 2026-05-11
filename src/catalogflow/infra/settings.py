"""Configurações da aplicação carregadas de variáveis de ambiente.

Toda configuração externa entra por aqui via Pydantic `BaseSettings`. O resto
do código importa `get_settings()` — nunca lê `os.environ` diretamente.

Ver `.env.example` para a lista completa de variáveis suportadas.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Configurações imutáveis carregadas no boot do processo.

    Atenção: instâncias são cacheadas em `get_settings()`. Em testes, sobrescreva
    via `app.dependency_overrides[get_settings] = lambda: Settings(...)`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Ambiente ──────────────────────────────
    environment: Environment = "development"
    log_level: LogLevel = "INFO"

    # ── Banco de dados ────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://catalogflow:catalogflow@localhost:5432/catalogflow",
        description="DSN async (postgresql+asyncpg://...).",
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_echo: bool = False

    # ── Redis / Celery ────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_result_ttl_seconds: int = 86_400

    # ── Storage (S3 / R2) ─────────────────────
    s3_bucket: str = "catalogflow-dev"
    s3_endpoint_url: str | None = None
    s3_region: str = "auto"
    aws_access_key_id: SecretStr = SecretStr("")
    aws_secret_access_key: SecretStr = SecretStr("")
    s3_presigned_url_ttl_seconds: int = 3600

    # ── Segurança ─────────────────────────────
    secret_key: SecretStr = SecretStr("change-me-in-production")
    algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    internal_secret: SecretStr = SecretStr("change-me-internal-only")
    api_key_prefix: str = "cf_"

    # ── Limites ───────────────────────────────
    max_pdf_size_mb: int = 50
    max_concurrent_jobs_starter: int = 5
    max_concurrent_jobs_growth: int = 20
    rate_limit_per_minute: int = 100

    # ── CORS ──────────────────────────────────
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ── Observabilidade ───────────────────────
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.1
    sentry_environment: str = "development"

    # ── PyMuPDF licenciamento (ADR-004) ───────
    pymupdf_license_mode: Literal["agpl-internal", "commercial"] = "agpl-internal"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        """Aceita CSV vindo do .env e converte para lista."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def max_pdf_size_bytes(self) -> int:
        """Limite de upload convertido para bytes."""
        return self.max_pdf_size_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton de configurações.

    Cacheado para evitar re-parse de `.env` em cada request. Em testes que
    precisem variar configuração, limpar cache com `get_settings.cache_clear()`.
    """
    return Settings()
