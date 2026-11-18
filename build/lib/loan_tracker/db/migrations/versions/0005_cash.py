"""Cash accounts, balances and planned items (loan_tracker/db/sql/0005_cash.sql).

Revision ID: 0005
Revises: 0004
"""
from pathlib import Path

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "sql" / "0005_cash.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("cash history is never dropped automatically; restore from a backup")
