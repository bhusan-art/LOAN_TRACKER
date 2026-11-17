"""Database test fixtures.

Set TEST_DATABASE_URL to an EMPTY throwaway database, e.g. with the
docker-compose Postgres:
    createdb -h 127.0.0.1 -U loan_tracker loan_tracker_test
    $env:TEST_DATABASE_URL = "postgresql+psycopg://loan_tracker:<pw>@127.0.0.1:5432/loan_tracker_test"
The schema is reset once per test run; every test runs in a rolled-back
transaction. Without TEST_DATABASE_URL these tests are skipped.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from loan_tracker.db.migrate import upgrade

URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def db_engine():
    if not URL:
        pytest.skip("TEST_DATABASE_URL not set (database tests skipped)")
    engine = create_engine(URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    upgrade(URL)
    yield engine
    engine.dispose()


@pytest.fixture()
def s(db_engine):
    conn = db_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()
