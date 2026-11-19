"""Phones signed in to the Android app (loan_tracker/db/sql/0011_app_device.sql).

Revision ID: 0011
Revises: 0010
"""
from pathlib import Path

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "sql" / "0011_app_device.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("the device table is not removed automatically; restore from a backup")
