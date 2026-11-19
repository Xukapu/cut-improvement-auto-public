
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения."""

    app_name: str = "ЦУТ Improvement Auto"
    app_version: str = "0.1.0"

    environment: Literal[
        "development",
        "test",
        "production",
    ] = "development"

    debug: bool = False
    workshop_timezone: str = "UTC"

    host: str = "127.0.0.1"
    port: int = 8000

    allowed_hosts: list[str] = [
        "127.0.0.1",
        "localhost",
        "testserver",
    ]

    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "cut_improvement_auto"
    db_user: str = "cut_app"
    db_password: SecretStr

    max_bot_token: SecretStr | None = None
    max_dispatch_mode: Literal["disabled", "test", "all"] = "disabled"
    max_test_client_number: int | None = Field(default=None, ge=1)

    session_cookie_name: str = "cut_session"

    session_ttl_hours: int = Field(
        default=12,
        ge=1,
        le=720,
    )

    login_max_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
    )

    login_lock_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("workshop_timezone")
    @classmethod
    def validate_workshop_timezone(cls, value: str) -> str:
        zone = value.strip()
        try:
            ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("WORKSHOP_TIMEZONE должен быть часовым поясом IANA.") from exc
        return zone

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def docs_enabled(self) -> bool:
        return not self.is_production

    @property
    def session_cookie_secure(self) -> bool:
        return self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()
