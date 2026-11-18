"""Bank statement import (loan_tracker/db/sql/0006_bank_import.sql).

Revision ID: 0006
Revises: 0005
"""
from pathlib import Path

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "sql" / "0006_bank_import.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("bank import history is never dropped automatically; restore from a backup")
