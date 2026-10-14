"""Centralized settings — all runtime config flows through here.

Uses pydantic-settings so values can come from env vars or a .env file.
Defaults are sane for `docker compose up` in dev.
"""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    log_level: str = "info"

    # DB
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "logiq"
    postgres_user: str = "logiq"
    postgres_password: str = "logiq"

    # Redis
    redis_url: str = "redis://redis:6379/0"
    log_stream: str = "logiq.logs"
    metric_stream: str = "logiq.metrics"
    consumer_group: str = "logiq-workers"

    # Auth
    jwt_secret: str = "change-me"
    jwt_alg: str = "HS256"
    jwt_expire_minutes: int = 60
    admin_user: str = "admin"
    admin_password: str = "admin"
    admin_email: str = ""
    ingest_api_key: str = ""
    cors_origins: str = "http://localhost:3000"
    analysis_interval_seconds: int = 60
    analysis_window_minutes: int = 15
    retention_days: int = 30

    # LLM
    llm_provider: Literal["openai", "gemini", "disabled"] = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    embed_model: str = "text-embedding-3-small"

    # Anomaly thresholds
    zscore_threshold: float = 2.5
    ewma_alpha: float = 0.3
    ewma_threshold: float = 2.0
    rule_cpu: float = 80.0
    rule_mem: float = 85.0
    rule_latency: float = 1000.0

    # Actions
    notification_hosts: str = Field(default="", validation_alias="_DB_NOTIFICATION_HOSTS")
    billing_provider: Literal["sandbox", "stripe", "disabled"] = "sandbox"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    public_url: str = "http://localhost:3000"
    secrets_encryption_key: str = ""
    smtp_host: str = Field(default="", validation_alias="_DB_SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="_DB_SMTP_PORT")
    smtp_user: str = Field(default="", validation_alias="_DB_SMTP_USER")
    smtp_password: str = Field(default="", validation_alias="_DB_SMTP_PASSWORD")
    smtp_sender: str = Field(default="", validation_alias="_DB_SMTP_SENDER")

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key if self.llm_provider == "openai" else
                    self.gemini_api_key if self.llm_provider == "gemini" else "")

    @property
    def llm_model(self) -> str:
        return self.gemini_model if self.llm_provider == "gemini" else self.openai_model

    @property
    def db_url(self) -> str:
        return URL.create("postgresql+asyncpg", username=self.postgres_user,
                          password=self.postgres_password, host=self.postgres_host,
                          port=self.postgres_port, database=self.postgres_db).render_as_string(hide_password=False)

    @property
    def db_url_sync(self) -> str:
        return URL.create("postgresql+psycopg2", username=self.postgres_user,
                          password=self.postgres_password, host=self.postgres_host,
                          port=self.postgres_port, database=self.postgres_db).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    config = Settings()
    if len(config.admin_password.encode("utf-8")) > 72:
        raise ValueError("ADMIN_PASSWORD must be at most 72 UTF-8 bytes")
    if config.app_env == "production":
        if "*" in config.cors_origins:
            raise ValueError("Production requires explicit CORS_ORIGINS")
        if len(config.jwt_secret) < 32 or config.jwt_secret.startswith("change-me"):
            raise ValueError("Production requires a random JWT_SECRET of at least 32 characters")
        if len(config.secrets_encryption_key) < 32:
            raise ValueError("Production requires SECRETS_ENCRYPTION_KEY of at least 32 random characters")
        if config.admin_password == "admin" or len(config.admin_password) < 12:
            raise ValueError("Production requires ADMIN_PASSWORD of at least 12 characters")
    return config


settings = get_settings()
