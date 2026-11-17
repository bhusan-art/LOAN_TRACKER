"""Spread and cash-flow mismatch (Examples B and C; 04.7, 04.8)."""
from decimal import Decimal

from loan_tracker.engine import FundingLink, compute, mismatch_check, spread
from tests.engine.helpers import D, P, disburse, pay, terms

SURESH = terms(loan_id=10, direction="TAKEN", book="BUSINESS", rate=Decimal("1.5"), due_day=5)
RAVI = terms(loan_id=20, direction="GIVEN", book="BUSINESS", rate=Decimal("2.5"), due_day=10)
LINK = FundingLink(taken_loan_id=10, given_loan_id=20, amount_paise=P, link_date=D(2026, 1, 1))


def results(as_of, ravi_payments=()):
    return {
        10: compute(SURESH, [disburse(SURESH)], as_of),
        20: compute(RAVI, [disburse(RAVI), *ravi_payments], as_of),
    }


class TestExampleB:
    def test_spread_100_000_per_month(self):
        rep = spread([LINK], {10: SURESH, 20: RAVI})
        assert rep.links[0].spread_paise_per_cycle == 100_000
        assert not rep.links[0].negative and not rep.links[0].cross_book
        assert rep.idle_cost_paise_per_cycle == 0

    def test_payable_before_receivable_warning(self):
        # on 2 Jan: Suresh is next due 5 Jan, Ravi only on 10 Jan
        flags = mismatch_check([LINK], results(D(2026, 1, 2)))
        assert [(f.kind, f.pay_date, f.receive_date) for f in flags] == [
            ("WARNING", D(2026, 1, 5), D(2026, 1, 10))]

    def test_risk_when_ravi_overdue(self):
        flags = mismatch_check([LINK], results(D(2026, 2, 20)))
        assert "RISK" in [f.kind for f in flags]
        assert [f for f in flags if f.kind == "RISK"][0].amount_paise == 150_000

    def test_no_risk_when_ravi_pays(self):
        # Ravi's dues: 10 Jan = 9 of 31 days (72_581) + 10 Feb full (250_000)
        flags = mismatch_check([LINK], results(D(2026, 2, 20), [pay(D(2026, 2, 10), 322_581)]))
        assert "RISK" not in [f.kind for f in flags]

    def test_written_off_exposure(self):
        flags = mismatch_check([LINK], results(D(2026, 1, 2)), written_off=frozenset({20}))
        assert flags[0].kind == "EXPOSURE"
        assert flags[0].amount_paise == P


class TestExampleC:
    def test_cross_book_flag(self):
        relative = terms(loan_id=30, direction="TAKEN", book="PERSONAL", principal_paise=5_000_000,
                         rate=Decimal("1"))
        link = FundingLink(30, 20, 5_000_000, D(2026, 1, 1))
        rep = spread([link], {30: relative, 20: RAVI})
        assert rep.links[0].cross_book
        assert rep.links[0].spread_paise_per_cycle == 75_000  # 50,000 x 1.5%


class TestFlags:
    def test_negative_spread_flagged(self):
        cheap = terms(loan_id=21, direction="GIVEN", rate=Decimal("1"))
        rep = spread([FundingLink(10, 21, P, D(2026, 1, 1))], {10: SURESH, 21: cheap})
        assert rep.negative_spread and rep.links[0].spread_paise_per_cycle == -50_000

    def test_idle_cost_on_unlinked_borrowing(self):
        rep = spread([], {10: SURESH})
        assert rep.idle_cost_paise_per_cycle == 150_000

    def test_per_year_normalised_to_month(self):
        yearly = terms(loan_id=11, direction="TAKEN", rate=Decimal("12"), rate_unit="PER_YEAR")
        rep = spread([FundingLink(11, 20, P, D(2026, 1, 1))], {11: yearly, 20: RAVI})
        assert rep.links[0].spread_paise_per_cycle == 150_000  # 2.5% - 1%
