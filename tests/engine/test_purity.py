"""The engine stays pure: no database, web, network or clock (P7, CLAUDE.md rule 4)."""
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[2] / "loan_tracker" / "engine"
FORBIDDEN = ["sqlalchemy", "fastapi", "psycopg", "requests", "httpx", "datetime.now",
             "date.today", "time.time", "open(", "float("]


def test_engine_source_is_pure():
    offenders = []
    for path in ENGINE.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {word}" for word in FORBIDDEN if word in text]
    assert offenders == []
