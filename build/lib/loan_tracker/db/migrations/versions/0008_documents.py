"""Encrypted documents (loan_tracker/db/sql/0008_documents.sql).

Revision ID: 0008
Revises: 0007
"""
from pathlib import Path

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "sql" / "0008_documents.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("document records are never dropped automatically; restore from a backup")
