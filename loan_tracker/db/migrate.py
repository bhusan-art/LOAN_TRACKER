"""Apply database migrations: `python -m loan_tracker.db.migrate [DATABASE_URL]`.

Wraps Alembic so there is no separate alembic.ini to keep in sync.
"""
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from loan_tracker.config import get_settings

HERE = Path(__file__).resolve().parent


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE / "migrations"))
    cfg.set_main_option("sqlalchemy.url", (url or get_settings().database_url).replace("%", "%%"))
    return cfg


def upgrade(url: str | None = None) -> None:
    command.upgrade(alembic_config(url), "head")


if __name__ == "__main__":
    upgrade(sys.argv[1] if len(sys.argv) > 1 else None)
    print("database is up to date")
