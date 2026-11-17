"""Initial schema: tables, enums, triggers (loan_tracker/db/sql/0001_initial.sql).

Revision ID: 0001
Revises:
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "sql" / "0001_initial.sql"


def upgrade() -> None:
    # Raw driver cursor, no parameters: runs the whole file in one call
    # (plpgsql $$ bodies and the literal % in RAISE messages stay intact).
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Deliberately not automatic: this database holds financial records.
    # Restore from a backup instead (see deploy/README.md).
    raise NotImplementedError("downgrade of the initial schema is not supported")
