"""Конфігурація застосунку.

Правило портабельності №1: застосунок читає **тільки env-змінні**.
Жодного знання про конкретного хмарного провайдера тут немає —
S3 задається через ``endpoint_url``, тому той самий код працює з AWS S3,
Cloudflare R2, MinIO та Backblaze B2.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="KOPIYKA_", extra="ignore")

    env: Literal["local", "staging", "prod"] = "local"
    log_level: str = "INFO"

    # --- База даних -------------------------------------------------------
    # Роль застосунку НЕ є власником таблиць — інакше RLS обходиться.
    database_url: str = "postgresql+asyncpg://kopiyka_app:devpass@localhost:5432/kopiyka"
    db_pool_size: int = 5
    db_echo: bool = False

    # --- Object storage (S3-сумісне) --------------------------------------
    s3_endpoint_url: str | None = None  # None = справжній AWS S3
    s3_region: str = "eu-central-1"
    s3_bucket: str = "kopiyka-imports"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_presign_ttl_seconds: int = 300
    raw_statement_retention_days: int = 7

    # --- Автентифікація ---------------------------------------------------
    # Фаза 1: Cloudflare Access. Фаза 2: власний OIDC (тиждень 7).
    auth_mode: Literal["cf_access", "oidc", "dev"] = "dev"
    cf_access_team_domain: str | None = None  # напр. myteam.cloudflareaccess.com
    cf_access_aud: str | None = None
    invite_only: bool = True

    # --- Ліміти -----------------------------------------------------------
    max_upload_bytes: int = Field(default=10 * 1024 * 1024)

    @property
    def cf_certs_url(self) -> str:
        return f"https://{self.cf_access_team_domain}/cdn-cgi/access/certs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
