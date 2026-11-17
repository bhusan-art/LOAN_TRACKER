"""Payment waterfall, reversals, payoff and credit (engine.md §3.2, §5;
DECISIONS #11, #13)."""
from loan_tracker.engine import compute
from tests.engine.helpers import D, P, disburse, entry, example_a, pay, terms


def rows(res, entry_id):
    return [(a.bucket, a.due_period_start, a.amount_paise) for a in res.allocations
            if a.entry_id == entry_id]


class TestExampleA:
    def test_5_feb_payment_partially_pays_feb_due(self):
        t, es, p1, _ = example_a()
        res = compute(t, es, D(2026, 2, 5))
        assert rows(res, p1.entry_id) == [("INTEREST", D(2026, 1, 1), 150_000)]
        assert res.dues[0].outstanding_paise == 50_000

    def test_15_mar_payment(self):
        t, es, _, p2 = example_a()
        res = compute(t, es, D(2026, 4, 1))
        assert rows(res, p2.entry_id) == [
            ("INTEREST", D(2026, 1, 1), 50_000),
            ("INTEREST", D(2026, 2, 1), 200_000),
            ("PRINCIPAL", None, 3_000_000),
        ]
        assert res.balances.principal_outstanding_paise == 7_000_000
        assert res.dues[-1].due.interest_paise == 167_097  # Apr due


class TestReversal:
    def test_reversing_5_feb_payment(self):
        t, es, p1, p2 = example_a()
        es = es + [entry("REVERSAL", D(2026, 3, 20), reverses=p1.entry_id)]
        res = compute(t, es, D(2026, 4, 1))
        assert rows(res, p1.entry_id) == []
        assert rows(res, p2.entry_id) == [
            ("INTEREST", D(2026, 1, 1), 200_000),
            ("INTEREST", D(2026, 2, 1), 200_000),
            ("PRINCIPAL", None, 2_850_000),
        ]
        assert res.balances.principal_outstanding_paise == 7_150_000
        assert res.balances.credit_paise == 0
        assert res.dues[-1].due.interest_paise == 168_742

    def test_reversal_equals_never_entered(self):
        t, es, p1, _ = example_a()
        a = compute(t, es + [entry("REVERSAL", D(2026, 3, 20), reverses=p1.entry_id)], D(2026, 5, 1))
        b = compute(t, [e for e in es if e is not p1], D(2026, 5, 1))
        assert a.balances == b.balances
        assert a.dues == b.dues


class TestPayoff:
    def test_mid_cycle_payoff_charges_to_repayment_day(self):
        # DECISIONS #11: dues 1 Feb, 1 Mar, 1 Apr (600_000) + closing [1 Apr, 14 Apr]
        # 93_333 + principal 10_000_000 = 10_693_333
        t = terms()
        before = compute(t, [disburse(t)], D(2026, 4, 15))
        assert before.balances.payoff_today_paise == 10_693_333
        p = pay(D(2026, 4, 15), 10_693_333)
        res = compute(t, [disburse(t), p], D(2026, 6, 1))
        assert rows(res, p.entry_id) == [
            ("INTEREST", D(2026, 1, 1), 200_000),
            ("INTEREST", D(2026, 2, 1), 200_000),
            ("INTEREST", D(2026, 3, 1), 200_000),
            ("INTEREST", D(2026, 4, 1), 93_333),
            ("PRINCIPAL", None, 10_000_000),
        ]
        closing = res.dues[-1].due
        assert (closing.period_start, closing.period_end, closing.due_date) == (
            D(2026, 4, 1), D(2026, 4, 14), D(2026, 4, 15))
        assert len(res.dues) == 4  # nothing generated after principal = 0
        assert res.balances.credit_paise == 0
        assert res.balances.payoff_today_paise == 0
        assert res.loan_status == "CLOSED"
        assert res.projected_due is None

    def test_payoff_on_boundary_has_no_closing_due(self):
        t = terms()
        p = pay(D(2026, 3, 1), 10_400_000)
        res = compute(t, [disburse(t), p], D(2026, 4, 1))
        assert [d.due.due_date for d in res.dues] == [D(2026, 2, 1), D(2026, 3, 1)]
        assert res.loan_status == "CLOSED"

    def test_short_payoff_keeps_small_principal_accruing(self):
        # clears dues + principal but not the open interest: the closing due is
        # paid first (interest before principal), 93_333 principal is left
        t = terms()
        p = pay(D(2026, 4, 15), 10_600_000)
        res = compute(t, [disburse(t), p], D(2026, 5, 1))
        assert res.balances.principal_outstanding_paise == 93_333
        assert res.dues[-1].due.period_start == D(2026, 4, 15)  # resumes from repayment day
        assert res.loan_status == "ACTIVE"


class TestEdges:
    def test_payment_on_disbursement_day_is_principal(self):
        t = terms()
        p = pay(D(2026, 1, 1), 1_000_000)
        res = compute(t, [disburse(t), p], D(2026, 2, 1))
        assert rows(res, p.entry_id) == [("PRINCIPAL", None, 1_000_000)]
        assert res.dues[0].due.interest_paise == 180_000

    def test_overpayment_becomes_credit(self):
        t = terms()
        p = pay(D(2026, 2, 1), 10_300_000)
        res = compute(t, [disburse(t), p], D(2026, 3, 1))
        assert sum(a.amount_paise for a in res.allocations) == 10_200_000
        assert res.balances.credit_paise == 100_000
        assert res.balances.payoff_today_paise == 0
        assert res.loan_status == "CLOSED"

    def test_topup_increases_principal_from_its_date(self):
        # Feb cycle: 14 days at 10M + 14 days at 15M = 100_000 + 150_000
        t = terms()
        es = [disburse(t), entry("TOPUP", D(2026, 2, 15), 5_000_000)]
        res = compute(t, es, D(2026, 3, 1))
        assert [d.due.interest_paise for d in res.dues] == [200_000, 250_000]

    def test_entries_after_as_of_ignored(self):
        t = terms()
        res = compute(t, [disburse(t), pay(D(2026, 5, 1), 999)], D(2026, 2, 1))
        assert res.allocations == ()

    def test_every_payment_fully_accounted(self):
        t, es, _, _ = example_a()
        res = compute(t, es, D(2026, 6, 1))
        paid = sum(e.amount_paise for e in es if e.type == "PAYMENT")
        assert sum(a.amount_paise for a in res.allocations) + res.balances.credit_paise == paid
        assert all(a.amount_paise > 0 for a in res.allocations)
