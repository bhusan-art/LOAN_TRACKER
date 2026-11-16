"""Phase 2.2 acceptance tests — accrual and due generation.

engine.md §3.1/§4/§5, DECISIONS #12/#14; pinned 2026 (2028 for the leap-year
case), paise, exact expected values. Principal timeline is the Phase 2.2
input (payment-driven principal changes arrive with the waterfall in 2.3).
"""
import pytest
from datetime import date
from decimal import Decimal

from loan_tracker.engine.accrual import (
    accrue,
    generate_dues,
    net_rate,
    open_period,
    projected_due,
)
from loan_tracker.engine.types import LedgerEntry, LoanTerms

TL_FULL = [(date(2026, 1, 1), 10_000_000)]
TL_EXAMPLE_A = [(date(2026, 1, 1), 10_000_000), (date(2026, 3, 15), 7_000_000)]
TL_ZEROED_15_APR = [(date(2026, 1, 1), 10_000_000), (date(2026, 4, 15), 0)]


def terms(**over) -> LoanTerms:
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


def rate_change(new_rate: Decimal, d: date, entry_id: int = 1) -> list[LedgerEntry]:
    return [
        LedgerEntry(
            entry_id=entry_id,
            entry_date=d,
            type="RATE_CHANGE",
            amount_paise=None,
            new_rate=new_rate,
            reference=None,
            reverses=None,
        )
    ]


class TestNoPayments:
    def test_feb_mar_apr_dues(self):
        dues, _ = generate_dues(terms(), [], date(2026, 4, 1), TL_FULL)
        assert [d.interest_paise for d in dues] == [200_000, 200_000, 200_000]
        assert (dues[0].period_start, dues[0].period_end, dues[0].due_date) == (
            date(2026, 1, 1), date(2026, 1, 31), date(2026, 2, 1)
        )

    def test_accrued_realized_basis_open_zero_on_boundary(self):
        res = accrue(terms(), [], date(2026, 4, 1), TL_FULL)
        assert res.interest_accrued_paise == 600_000
        assert res.open_period_paise == 0

    def test_projected_at_15_apr(self):
        t = terms()
        proj = projected_due(t, [], date(2026, 4, 15), TL_FULL)
        assert proj is not None
        assert (proj.period_start, proj.due_date) == (date(2026, 4, 1), date(2026, 5, 1))
        assert proj.interest_paise == 200_000


class TestRateChangeMidCycle:
    def test_march_due_250_000(self):
        # §5.3 test_rate_change_mid_cycle: [1 Feb,14 Feb] 100_000 + [15 Feb,28 Feb] 150_000
        t = terms()
        entries = rate_change(Decimal("3"), date(2026, 2, 15))
        dues, _ = generate_dues(t, entries, date(2026, 3, 1), TL_FULL)
        assert [d.interest_paise for d in dues] == [200_000, 250_000]

    def test_segments_split_at_event_and_month_edge(self):
        t = terms()
        entries = rate_change(Decimal("3"), date(2026, 2, 15))
        res = accrue(t, entries, date(2026, 3, 1), TL_FULL)
        assert [s.to_day for s in res.segments] == [
            date(2026, 1, 31), date(2026, 2, 14), date(2026, 2, 28)
        ]
        assert [s.rate for s in res.segments] == [Decimal("2"), Decimal("2"), Decimal("3")]
        assert [s.days for s in res.segments] == [31, 14, 14]

    def test_net_rate(self):
        t = terms()
        entries = rate_change(Decimal("3"), date(2026, 2, 15))
        assert net_rate(t, entries) == [
            (date(2026, 1, 1), Decimal("2")),
            (date(2026, 2, 15), Decimal("3")),
        ]


class TestShortFirstPeriod:
    def test_feb_5_due_full_cycle(self):
        # DECISIONS #15: a no-event cycle earns exactly P * r, so the short
        # first period [20 Jan, 4 Feb] (15 days) is a full cycle -> 200_000 p
        t = terms(start_date=date(2026, 1, 20), due_day=5)
        tl = [(date(2026, 1, 20), 10_000_000)]
        dues, _ = generate_dues(t, [], date(2026, 2, 5), tl)
        assert [d.interest_paise for d in dues] == [200_000]
        assert (dues[0].period_start, dues[0].period_end, dues[0].due_date) == (
            date(2026, 1, 20), date(2026, 2, 4), date(2026, 2, 5)
        )


class TestQuarterly:
    def test_apr_and_jul_dues(self):
        # DECISIONS #14: b1 = 1 Apr, then 1 Jul
        t = terms(interest_cycle="QUARTERLY")
        res = accrue(t, [], date(2026, 7, 1), TL_FULL)
        assert [d.interest_paise for d in res.periods] == [600_000, 600_000]
        assert res.interest_accrued_paise == 1_200_000


class TestAtMaturity:
    def test_lump_due_then_monthly(self):
        # single lump at maturity, then open-ended monthly accrual (#12)
        t = terms(interest_cycle="AT_MATURITY", maturity_date=date(2026, 4, 1))
        dues, _ = generate_dues(t, [], date(2026, 5, 1), TL_FULL)
        assert [d.interest_paise for d in dues] == [600_000, 200_000]
        assert [d.due_date for d in dues] == [date(2026, 4, 1), date(2026, 5, 1)]


class TestPostMaturity:
    def test_accrued_to_1_jun(self):
        # #12: monthly dues keep generating past maturity_date; 5 x 200_000
        t = terms(maturity_date=date(2026, 5, 1))
        res = accrue(t, [], date(2026, 6, 1), TL_FULL)
        assert res.interest_accrued_paise == 1_000_000
        assert [d.interest_paise for d in res.periods] == [200_000] * 5


class TestOpenPeriod:
    def test_15_apr_open_tail(self):
        # #11: [1 Apr, 14 Apr] = 14 days @ 10_000_000 -> 93_333.33 -> 93_333 p
        t = terms()
        assert open_period(t, [], date(2026, 4, 15), TL_FULL) == 93_333
        res = accrue(t, [], date(2026, 4, 15), TL_FULL)
        assert res.open_period_paise == 93_333
        assert res.interest_accrued_paise == 600_000  # 1 Apr due realized (due_date <= as_of)

    def test_zero_on_boundary(self):
        assert open_period(terms(), [], date(2026, 3, 1), TL_FULL) == 0


class TestExampleATimeline:
    def test_march_cycle_31_days_pro_rates(self):
        # #15: mid-cycle principal change prorates by days in THAT cycle.
        # March cycle [1 Mar, 31 Mar] = 31 days; 10M until 14 Mar (14 days),
        # 7M from 15 Mar (17 days): 200_000*14/31 + 200_000*17/31
        # = 90_322.58 + 109_677.42 = 200_000 -> due stays 167_097 (sum of both)
        t = terms()
        res = accrue(t, [], date(2026, 4, 1), TL_EXAMPLE_A)
        apr = [d for d in res.periods if d.due_date == date(2026, 4, 1)][0]
        assert apr.interest_paise == 167_097


class TestPrincipalReductionMidCycle:
    def test_halved_on_15_feb(self):
        # #15: principal drops 10M -> 5M on 15 Feb; Feb cycle [1 Feb, 28 Feb]
        # = 28 days, prorated 14/28 at 10M + 14/28 at 5M:
        # 200_000*14/28 + 100_000*14/28 = 100_000 + 50_000 = 150_000
        t = terms()
        tl = [(date(2026, 1, 1), 10_000_000), (date(2026, 2, 15), 5_000_000)]
        dues, _ = generate_dues(t, [], date(2026, 3, 1), tl)
        assert [d.interest_paise for d in dues] == [200_000, 150_000]

    def test_reduction_pro_rates_by_cycle_days(self):
        # same principle: a principal cut prorates by the containing cycle's
        # total days (here 31-day March), not by calendar month length
        t = terms()
        tl = [(date(2026, 1, 1), 10_000_000), (date(2026, 3, 15), 7_000_000)]
        res = accrue(t, [], date(2026, 4, 1), tl)
        apr = [d for d in res.periods if d.due_date == date(2026, 4, 1)][0]
        assert apr.interest_paise == 167_097


class TestDueDay31:
    def test_full_cycles_earn_exactly_p_r(self):
        # DECISIONS #15: a due day 31 cycle earns exactly P * r regardless of
        # clamped day counts — 200_000 x4 for 28/32/30/31-day cycles
        t = terms(start_date=date(2026, 2, 1), due_day=31)
        tl = [(date(2026, 2, 1), 10_000_000)]
        dues, _ = generate_dues(t, [], date(2026, 5, 31), tl)
        assert [d.due_date for d in dues] == [
            date(2026, 2, 28),
            date(2026, 3, 31),
            date(2026, 4, 30),
            date(2026, 5, 31),
        ]
        assert [d.interest_paise for d in dues] == [200_000] * 4


class TestLeapYear:
    def test_due_day_1_period_spans_29_feb(self):
        # 29/29 = full month -> 200_000 p
        t = terms(start_date=date(2028, 2, 1), due_day=1)
        tl = [(date(2028, 2, 1), 10_000_000)]
        dues, _ = generate_dues(t, [], date(2028, 3, 1), tl)
        assert [d.interest_paise for d in dues] == [200_000]
        assert (dues[0].period_end, dues[0].due_date) == (date(2028, 2, 29), date(2028, 3, 1))

    def test_due_day_31_clamps_to_29_feb(self):
        # period [1 Feb, 28 Feb] = 28 of 29 leap days -> 193_103 p
        t = terms(start_date=date(2028, 2, 1), due_day=31)
        tl = [(date(2028, 2, 1), 10_000_000)]
        dues, _ = generate_dues(t, [], date(2028, 2, 29), tl)
        assert [d.interest_paise for d in dues] == [193_103]
        assert dues[0].due_date == date(2028, 2, 29)


class TestZeroPrincipal:
    def test_open_zero_and_no_projection(self):
        # P = 0 from 15 Apr: the open tail [1 Apr, 14 Apr] realizes as a
        # CLOSING DUE on 15 Apr (payoff rule, §3.1); no dues after P = 0.
        t = terms()
        assert open_period(t, [], date(2026, 4, 20), TL_ZEROED_15_APR) == 0
        assert projected_due(t, [], date(2026, 4, 20), TL_ZEROED_15_APR) is None
        res = accrue(t, [], date(2026, 4, 20), TL_ZEROED_15_APR)
        assert [d.interest_paise for d in res.periods] == [200_000, 200_000, 200_000, 93_333]
        assert [d.due_date for d in res.periods] == [
            date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1), date(2026, 4, 15)
        ]
        assert res.interest_accrued_paise == 693_333


class TestPerYear:
    def test_january_31_days(self):
        # P * r/100 * 31/365 = 600_000 * 31/365 = 50_958.90 -> 50_959 p
        t = terms(rate=Decimal("6"), rate_unit="PER_YEAR")
        dues, _ = generate_dues(t, [], date(2026, 2, 1), TL_FULL)
        assert [d.interest_paise for d in dues] == [50_959]


class TestNotImplemented:
    def test_compound_raises(self):
        t = terms(interest_type="COMPOUND")
        with pytest.raises(NotImplementedError):
            accrue(t, [], date(2026, 2, 1), TL_FULL)
