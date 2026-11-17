"""Settings from environment variables (.env is loaded by docker compose /
VS Code; see .env.example). Nothing here has side effects."""
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _rupees_env(name: str) -> int:
    """'25,000' -> 2_500_000 paise; empty -> 0."""
    raw = _env(name).replace(",", "").strip()
    if not raw:
        return 0
    from decimal import Decimal, InvalidOperation
    try:
        return int(Decimal(raw) * 100)
    except InvalidOperation:
        raise ValueError(f"{name} must be an amount in rupees, e.g. 25000") from None


def _database_url(url: str, password: str = "") -> str:
    """DATABASE_URL with DATABASE_PASSWORD put in safely: a password containing
    @ : / # % or spaces would otherwise break the URL (it is %-encoded here)."""
    if not password:
        return url
    from sqlalchemy.engine import make_url
    return make_url(url).set(password=password).render_as_string(hide_password=False)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _database_url(
        _env("DATABASE_URL", "postgresql+psycopg://loan_tracker:change-me@127.0.0.1:5432/loan_tracker"),
        _env("DATABASE_PASSWORD")))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", "dev-only-change-me"))
    timezone: str = field(default_factory=lambda: _env("APP_TIMEZONE", "Asia/Kolkata"))
    secure_cookies: bool = field(default_factory=lambda: _env("SECURE_COOKIES", "0") == "1")
    reminder_days_before: int = field(default_factory=lambda: int(_env("REMINDER_DAYS_BEFORE", "3")))
    concentration_flag_pct: int = field(default_factory=lambda: int(_env("CONCENTRATION_FLAG_PCT", "25")))
    # cash forecast (Phase 11): warn when the balance would fall below this (rupees)
    cash_buffer_paise: int = field(default_factory=lambda: _rupees_env("CASH_MIN_BALANCE"))
    # receipts and closure certificates (Phase 10): who issues them
    receipt_name: str = field(default_factory=lambda: _env("RECEIPT_NAME"))
    receipt_business_name: str = field(default_factory=lambda: _env("RECEIPT_BUSINESS_NAME"))
    receipt_details: str = field(default_factory=lambda: _env("RECEIPT_DETAILS"))
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    email_to: str = field(default_factory=lambda: _env("EMAIL_TO"))
    email_from: str = field(default_factory=lambda: _env("EMAIL_FROM"))
    # comma list of books whose routine reminders are muted, e.g. "PERSONAL".
    # Payments YOU owe that are due today or overdue are never muted.
    mute_books: frozenset = field(default_factory=lambda: frozenset(
        b.strip().upper() for b in _env("MUTE_BOOKS").split(",") if b.strip()))


def get_settings() -> Settings:
    return Settings()


def today(settings: Settings | None = None) -> date:
    """Today's date in the owner's timezone. The ONLY place the app reads the
    clock for business dates; the engine never does."""
    tz = ZoneInfo((settings or get_settings()).timezone)
    return datetime.now(tz).date()
