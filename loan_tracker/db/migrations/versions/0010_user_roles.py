"""Read-only logins (loan_tracker/db/sql/0010_user_roles.sql).

Revision ID: 0010
Revises: 0009
"""
from pathlib import Path

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "sql" / "0010_user_roles.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("roles are not removed automatically; restore from a backup")
