"""Settings from environment variables (.env is loaded by docker compose /
VS Code; see .env.example). Nothing here has side effects."""
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env(
        "DATABASE_URL", "postgresql+psycopg://loan_tracker:change-me@127.0.0.1:5432/loan_tracker"))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", "dev-only-change-me"))
    timezone: str = field(default_factory=lambda: _env("TZ", "Asia/Kolkata"))
    secure_cookies: bool = field(default_factory=lambda: _env("SECURE_COOKIES", "0") == "1")
    reminder_days_before: int = field(default_factory=lambda: int(_env("REMINDER_DAYS_BEFORE", "3")))
    concentration_flag_pct: int = field(default_factory=lambda: int(_env("CONCENTRATION_FLAG_PCT", "25")))
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    email_to: str = field(default_factory=lambda: _env("EMAIL_TO"))
    email_from: str = field(default_factory=lambda: _env("EMAIL_FROM"))


def get_settings() -> Settings:
    return Settings()


def today(settings: Settings | None = None) -> date:
    """Today's date in the owner's timezone. The ONLY place the app reads the
    clock for business dates; the engine never does."""
    tz = ZoneInfo((settings or get_settings()).timezone)
    return datetime.now(tz).date()
