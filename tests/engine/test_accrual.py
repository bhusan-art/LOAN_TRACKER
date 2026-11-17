"""Accrual and due generation (engine.md §3.1/§4, DECISIONS #10-#15).

All amounts paise, exact integers. Interest pro-rates only for periods that
are not a complete cycle (DECISIONS #15).
"""
import pytest

from loan_tracker.engine import accrue, compute
from loan_tracker.engine.accrual import net_rate
from tests.engine.helpers import D, P, disburse, entry, pay, terms


def dues_of(t, entries, as_of):
    return [d.interest_paise for d in accrue(t, entries, as_of).periods]


class TestNoPayments:
    def test_feb_mar_apr_dues(self):
        t = terms()
        res = accrue(t, [disburse(t)], D(2026, 4, 1))
        assert [d.interest_paise for d in res.periods] == [200_000, 200_000, 200_000]
        first = res.periods[0]
        assert (first.period_start, first.period_end, first.due_date) == (
            D(2026, 1, 1), D(2026, 1, 31), D(2026, 2, 1))

    def test_accrued_realized_basis_open_zero_on_boundary(self):
        t = terms()
        res = accrue(t, [disburse(t)], D(2026, 4, 1))
        assert res.interest_accrued_paise == 600_000
        assert res.open_period_paise == 0

    def test_projected_at_15_apr(self):
        t = terms()
        proj = accrue(t, [disburse(t)], D(2026, 4, 15)).projected_due
        assert (proj.period_start, proj.due_date, proj.interest_paise) == (
            D(2026, 4, 1), D(2026, 5, 1), 200_000)

    def test_no_disbursement_no_interest(self):
        assert dues_of(terms(), [], D(2026, 4, 1)) == []


class TestRateChangeMidCycle:
    def test_march_due_250_000(self):
        # Feb cycle 28 days: 14 days at 2% + 14 days at 3% = 100_000 + 150_000
        t = terms()
        es = [disburse(t), entry("RATE_CHANGE", D(2026, 2, 15), new_rate="3")]
        assert dues_of(t, es, D(2026, 3, 1)) == [200_000, 250_000]

    def test_segments_split_at_event_and_boundary(self):
        t = terms()
        es = [disburse(t), entry("RATE_CHANGE", D(2026, 2, 15), new_rate="3")]
        res = accrue(t, es, D(2026, 3, 1))
        assert [(s.to_day, s.rate, s.days) for s in res.segments] == [
            (D(2026, 1, 31), 2, 31), (D(2026, 2, 14), 2, 14), (D(2026, 2, 28), 3, 14)]

    def test_net_rate(self):
        t = terms()
        assert net_rate(t, [entry("RATE_CHANGE", D(2026, 2, 15), new_rate="3")]) == [
            (D(2026, 1, 1), 2), (D(2026, 2, 15), 3)]


class TestDecision15FullCycles:
    def test_due_day_15_every_full_cycle_exactly_p_r(self):
        # 15 Jan-14 Feb (31 days) and 15 Feb-14 Mar (28 days) both earn 200_000
        t = terms(start_date=D(2026, 1, 15), due_day=15)
        assert dues_of(t, [disburse(t)], D(2026, 4, 15)) == [200_000] * 3

    def test_short_first_period_pro_rated(self):
        # start 20 Jan, due_day 5: 16 days of the 31-day cycle [5 Jan, 4 Feb]
        # 200_000 x 16/31 = 103_225.81 -> 103_226
        t = terms(start_date=D(2026, 1, 20), due_day=5)
        res = accrue(t, [disburse(t)], D(2026, 3, 5))
        assert [d.interest_paise for d in res.periods] == [103_226, 200_000]
        assert (res.periods[0].period_start, res.periods[0].due_date) == (D(2026, 1, 20), D(2026, 2, 5))

    def test_due_day_31_first_partial_then_full(self):
        # start 1 Feb, due_day 31 -> 28 Feb: 27 of 28 days = 192_857.14 -> 192_857
        t = terms(start_date=D(2026, 2, 1), due_day=31)
        res = accrue(t, [disburse(t)], D(2026, 5, 31))
        assert [d.due_date for d in res.periods] == [
            D(2026, 2, 28), D(2026, 3, 31), D(2026, 4, 30), D(2026, 5, 31)]
        assert [d.interest_paise for d in res.periods] == [192_857, 200_000, 200_000, 200_000]

    def test_leap_year_due_day_31(self):
        # start 1 Feb 2028 -> 29 Feb: 28 of 29 days = 193_103.45 -> 193_103
        t = terms(start_date=D(2028, 2, 1), due_day=31)
        assert dues_of(t, [disburse(t)], D(2028, 2, 29)) == [193_103]

    def test_leap_year_due_day_1_full(self):
        t = terms(start_date=D(2028, 2, 1))
        res = accrue(t, [disburse(t)], D(2028, 3, 1))
        assert [d.interest_paise for d in res.periods] == [200_000]
        assert res.periods[0].period_end == D(2028, 2, 29)

    def test_principal_cut_pro_rates_by_cycle_days(self):
        # Feb cycle (28 days): 14 at 10M, 14 at 5M -> 100_000 + 50_000
        t = terms()
        es = [disburse(t), pay(D(2026, 2, 1), 200_000), pay(D(2026, 2, 15), 5_000_000)]
        assert dues_of(t, es, D(2026, 3, 1)) == [200_000, 150_000]


class TestCycles:
    def test_quarterly_decision_14(self):
        t = terms(interest_cycle="QUARTERLY")
        res = accrue(t, [disburse(t)], D(2026, 7, 1))
        assert [(d.due_date, d.interest_paise) for d in res.periods] == [
            (D(2026, 4, 1), 600_000), (D(2026, 7, 1), 600_000)]
        assert res.interest_accrued_paise == 1_200_000

    def test_at_maturity_lump_then_monthly(self):
        t = terms(interest_cycle="AT_MATURITY", maturity_date=D(2026, 4, 1))
        res = accrue(t, [disburse(t)], D(2026, 5, 1))
        assert [(d.due_date, d.interest_paise) for d in res.periods] == [
            (D(2026, 4, 1), 600_000), (D(2026, 5, 1), 200_000)]

    def test_post_maturity_keeps_accruing(self):
        # DECISIONS #12: 5 monthly dues by 1 Jun even though maturity is 1 May
        t = terms(maturity_date=D(2026, 5, 1))
        res = accrue(t, [disburse(t)], D(2026, 6, 1))
        assert res.interest_accrued_paise == 1_000_000

    def test_per_year_rate_uses_365(self):
        # 6% a year: Rs 6,000 x 31/365 = 50_958.90 -> 50_959
        t = terms(rate=6, rate_unit="PER_YEAR")
        assert dues_of(t, [disburse(t)], D(2026, 2, 1)) == [50_959]


class TestOpenPeriod:
    def test_15_apr_open_tail(self):
        # [1 Apr, 14 Apr] = 14 of 30 days -> 93_333.33 -> 93_333
        t = terms()
        res = accrue(t, [disburse(t)], D(2026, 4, 15))
        assert res.open_period_paise == 93_333
        assert res.interest_accrued_paise == 600_000

    def test_zero_on_boundary(self):
        t = terms()
        assert accrue(t, [disburse(t)], D(2026, 3, 1)).open_period_paise == 0


class TestNotImplemented:
    def test_compound_raises(self):
        t = terms(interest_type="COMPOUND")
        with pytest.raises(NotImplementedError):
            compute(t, [disburse(t)], D(2026, 2, 1))

    def test_capitalise_raises(self):
        t = terms()
        with pytest.raises(NotImplementedError):
            compute(t, [disburse(t), entry("CAPITALISE", D(2026, 2, 1), 1)], D(2026, 2, 1))
