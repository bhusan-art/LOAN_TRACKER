"""Phase 2.1 acceptance tests — rounding, calendar, cycle boundaries.

Pinned 2026 values per docs/design/engine.md §4.2/§5 and DECISIONS #8/#12/#14.
No accrual or waterfall yet.
"""
from datetime import date
from decimal import Decimal

from loan_tracker.engine.calendar import clamp_due_day, cycle_boundaries, days_in_month
from loan_tracker.engine.money import round_half_up
from loan_tracker.engine.types import LoanTerms


def terms(**over) -> LoanTerms:
    """Canonical Example-A terms; override any field via keyword."""
    base = dict(
        loan_id=1,
        direction="GIVEN",
        book="PERSONAL",
        principal_paise=10_000_000,
        start_date=date(2026, 1, 1),
        rate=Decimal("2"),
        rate_unit="PER_MONTH",
        interest_type="SIMPLE",
        interest_cycle="MONTHLY",
        due_day=1,
        grace_days=3,
        penalty_rule=None,
        maturity_date=None,
    )
    base.update(over)
    return LoanTerms(**base)


class TestRoundHalfUp:
    def test_half_rounds_up(self):
        assert round_half_up(Decimal("0.5")) == 1

    def test_example_a_april_due(self):
        # engine.md §5.2: 167_096.77 -> 167_097 p
        assert round_half_up(Decimal("167096.77")) == 167_097

    def test_short_first_period_due(self):
        # engine.md §5.3: 105_990.78 -> 105_991 p
        assert round_half_up(Decimal("105990.78")) == 105_991


class TestDaysInMonth:
    def test_feb_2026(self):
        assert days_in_month(2026, 2) == 28

    def test_feb_2028_leap(self):
        assert days_in_month(2028, 2) == 29

    def test_april(self):
        assert days_in_month(2026, 4) == 30


class TestClampDueDay:
    def test_due_day_31_clamps(self):
        assert clamp_due_day(2026, 2, 31) == 28
        assert clamp_due_day(2026, 4, 31) == 30
        assert clamp_due_day(2026, 6, 31) == 30


class TestMonthlyBoundaries:
    def test_first_boundary_strictly_after_start(self):
        t = terms(start_date=date(2026, 1, 1), due_day=1)
        assert cycle_boundaries(t, date(2026, 4, 1)) == [
            date(2026, 2, 1),
            date(2026, 3, 1),
            date(2026, 4, 1),
        ]

    def test_short_first_period(self):
        # start 20 Jan 2026, due_day 5 -> first boundary 5 Feb
        t = terms(start_date=date(2026, 1, 20), due_day=5)
        assert cycle_boundaries(t, date(2026, 2, 5)) == [date(2026, 2, 5)]
        assert cycle_boundaries(t, date(2026, 3, 5)) == [
            date(2026, 2, 5),
            date(2026, 3, 5),
        ]

    def test_due_day_31_boundaries(self):
        # start 1 Feb 2026, due_day 31: every short month clamps to month-end
        t = terms(start_date=date(2026, 2, 1), due_day=31)
        assert cycle_boundaries(t, date(2026, 6, 30)) == [
            date(2026, 2, 28),
            date(2026, 3, 31),
            date(2026, 4, 30),
            date(2026, 5, 31),
            date(2026, 6, 30),
        ]

    def test_due_day_31_short_first_period(self):
        # start 1 Jan 2026, due_day 31: b_1 = 31 Jan (strictly after start)
        t = terms(due_day=31)
        assert cycle_boundaries(t, date(2026, 1, 31)) == [date(2026, 1, 31)]

    def test_no_boundaries_before_first(self):
        t = terms(start_date=date(2026, 1, 1), due_day=1)
        assert cycle_boundaries(t, date(2026, 1, 15)) == []


class TestQuarterlyBoundaries:
    def test_quarterly_decision_14(self):
        # start 1 Jan 2026, due_day 1 -> b_1 = 1 Apr, then every 3rd month
        t = terms(start_date=date(2026, 1, 1), due_day=1, interest_cycle="QUARTERLY")
        assert cycle_boundaries(t, date(2026, 10, 1)) == [
            date(2026, 4, 1),
            date(2026, 7, 1),
            date(2026, 10, 1),
        ]


class TestAtMaturityBoundaries:
    def test_single_due_then_monthly(self):
        # DECISIONS #12: lump at maturity, then open-ended monthly accrual
        t = terms(
            start_date=date(2026, 1, 1),
            due_day=1,
            interest_cycle="AT_MATURITY",
            maturity_date=date(2026, 4, 1),
        )
        assert cycle_boundaries(t, date(2026, 6, 1)) == [
            date(2026, 4, 1),
            date(2026, 5, 1),
            date(2026, 6, 1),
        ]

    def test_maturity_after_up_to(self):
        t = terms(
            start_date=date(2026, 1, 1),
            due_day=1,
            interest_cycle="AT_MATURITY",
            maturity_date=date(2026, 4, 1),
        )
        assert cycle_boundaries(t, date(2026, 3, 31)) == []


def test_database_password_with_special_characters():
    from sqlalchemy.engine import make_url
    from loan_tracker.config import _database_url
    url = _database_url("postgresql+psycopg://loan_tracker@db:5432/loan_tracker", "p@ss:w/rd#9%x 944")
    u = make_url(url)
    assert (u.host, u.port, u.username, u.password, u.database) == (
        "db", 5432, "loan_tracker", "p@ss:w/rd#9%x 944", "loan_tracker")
    assert _database_url("postgresql+psycopg://a:b@h/d") == "postgresql+psycopg://a:b@h/d"
