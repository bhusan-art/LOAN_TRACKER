"""Statuses, balances, loan status and compute() (engine.md §1, §5.2; 04.3-04.6)."""
from dataclasses import replace

from loan_tracker.engine import aging_bucket, compute
from tests.engine.helpers import D, disburse, entry, example_a, pay, terms


class TestExampleACanonical:
    def test_balances_on_1_apr(self):
        t, es, _, _ = example_a()
        b = compute(t, es, D(2026, 4, 1)).balances
        assert b.interest_accrued_paise == 567_097
        assert b.interest_paid_paise == 400_000
        assert b.interest_outstanding_paise == 167_097
        assert b.open_period_paise == 0
        assert b.payoff_today_paise == 7_167_097


class TestDueStatus:
    def statuses(self, as_of):
        t, es, _, _ = example_a()
        return {d.due.due_date: d for d in compute(t, es, as_of).dues}

    def test_feb_due_partial_inside_grace(self):
        assert self.statuses(D(2026, 2, 3))[D(2026, 2, 1)].status == "PARTIAL"

    def test_feb_due_overdue_after_grace(self):
        feb = self.statuses(D(2026, 3, 5))[D(2026, 2, 1)]
        assert feb.status == "OVERDUE"
        assert feb.outstanding_paise == 50_000
        assert feb.overdue_days == 29  # 5 Mar - (1 Feb + 3 grace days)
        assert aging_bucket(feb.overdue_days) == "0_30"

    def test_paid_after_15_mar(self):
        s = self.statuses(D(2026, 3, 16))
        assert s[D(2026, 2, 1)].status == "PAID"
        assert s[D(2026, 3, 1)].status == "PAID"

    def test_fully_waived_due(self):
        t = terms()
        res = compute(t, [disburse(t), entry("WAIVER", D(2026, 2, 2))], D(2026, 3, 1))
        assert res.dues[0].status == "WAIVED"
        assert res.dues[0].waived_paise == 200_000
        assert res.balances.interest_waived_paise == 200_000

    def test_partial_waiver_amount(self):
        t = terms()
        es = [disburse(t), entry("WAIVER", D(2026, 2, 2), 50_000), pay(D(2026, 2, 3), 150_000)]
        assert compute(t, es, D(2026, 2, 3)).dues[0].status == "PAID"


class TestLoanStatus:
    def test_active(self):
        t = terms()
        assert compute(t, [disburse(t)], D(2026, 2, 3)).loan_status == "ACTIVE"

    def test_overdue(self):
        t = terms()
        assert compute(t, [disburse(t)], D(2026, 2, 5)).loan_status == "OVERDUE"

    def test_matured_with_overdue_dues(self):
        t = terms(maturity_date=D(2026, 5, 1))
        res = compute(t, [disburse(t)], D(2026, 6, 1))
        assert res.loan_status == "MATURED"
        assert {d.status for d in res.dues[:-1]} == {"OVERDUE"}
        assert res.projected_due is not None  # keeps accruing (#12)


class TestPenalty:
    def test_penalty_paid_before_interest(self):
        t = terms()
        es = [disburse(t), entry("PENALTY", D(2026, 2, 10), 5_000), pay(D(2026, 2, 11), 100_000)]
        res = compute(t, es, D(2026, 2, 11))
        assert [(a.bucket, a.amount_paise) for a in res.allocations] == [
            ("PENALTY", 5_000), ("INTEREST", 95_000)]


class TestDirectionSymmetry:
    def test_taken_identical_to_given(self):
        t, es, _, _ = example_a()
        g = compute(t, es, D(2026, 6, 1))
        k = compute(replace(t, direction="TAKEN"), es, D(2026, 6, 1))
        assert g.balances == k.balances
        assert g.dues == k.dues
        assert g.allocations == k.allocations
