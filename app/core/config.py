from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Resume Filtering"
    environment: str = "development"
    api_prefix: str = "/api"
    database_url: str = Field(default="postgresql+psycopg://resume:resume@postgres:5432/resume_filter")
    redis_url: str = "redis://redis:6379/0"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 720
    cors_origins: str = "http://localhost:3000"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    default_deepseek_model: str = "deepseek-chat"
    resume_download_timeout_seconds: int = 30
    max_resume_chars_for_llm: int = 60000
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_ssl: bool = True
    smtp_use_starttls: bool = False
    recruiter_from_email: str | None = None
    recruiter_from_name: str = "HR Team"
    imap_host: str | None = None
    imap_port: int = 993
    save_sent_email_copy: bool = True
    sent_mailbox_name: str = "Sent"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
