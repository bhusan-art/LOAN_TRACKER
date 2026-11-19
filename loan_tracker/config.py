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


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    """An integer setting kept within sensible limits (a typo cannot switch a safeguard off)."""
    try:
        v = int(_env(name, str(default)))
    except ValueError:
        return default
    return max(low, min(high, v))


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
    # borrower statement links (Phase 15): the address borrowers open, e.g. https://loans.example.com
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL").rstrip("/"))
    # sign out after this many minutes without use (browser / Android app), and a browser
    # session never lasts longer than SESSION_MAX_HOURS even when in use
    session_idle_minutes: int = field(default_factory=lambda: _bounded_int("SESSION_IDLE_MINUTES", 30, 5, 24 * 60))
    app_idle_minutes: int = field(default_factory=lambda: _bounded_int("APP_IDLE_MINUTES", 30, 5, 90 * 24 * 60))
    session_max_hours: int = field(default_factory=lambda: _bounded_int("SESSION_MAX_HOURS", 12, 1, 24 * 30))
    # one sign-in per user: signing in (browser or phone) signs that user out everywhere else
    single_session: bool = field(default_factory=lambda: _env("SINGLE_SESSION", "1") != "0")
    # origins allowed to call the API with a device token: the apps' own pages
    # (Android: https://localhost, Windows: https://tauri.localhost)
    app_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        o.strip().rstrip("/") for o in _env("APP_ORIGINS", "https://localhost,https://tauri.localhost").split(",") if o.strip()))
    # documents (Phase 14): encryption key(s) and where the encrypted files live
    document_key: str = field(default_factory=lambda: _env("DOCUMENT_KEY"))
    document_key_old: str = field(default_factory=lambda: _env("DOCUMENT_KEY_OLD"))
    document_dir: str = field(default_factory=lambda: _env("DOCUMENT_DIR", "data/documents"))
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
