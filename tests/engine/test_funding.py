"""Spread and cash-flow mismatch (Examples B and C; 04.7, 04.8)."""
from decimal import Decimal

from loan_tracker.engine import FundingLink, compute, link_exposure, mismatch_check, spread
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


class TestLinkExposure:
    """A link only counts what is still outstanding on both loans (Phase 8)."""

    def test_untouched_loans_count_full_amount(self):
        assert link_exposure(LINK, {10: SURESH, 20: RAVI}, {10: P, 20: P}) == P

    def test_given_half_repaid_halves_the_link(self):
        assert link_exposure(LINK, {10: SURESH, 20: RAVI}, {10: P, 20: P // 2}) == P // 2

    def test_smaller_ratio_wins(self):
        # Ravi 75% outstanding, Suresh 40% outstanding -> 40% of the link
        assert link_exposure(LINK, {10: SURESH, 20: RAVI}, {10: P * 40 // 100, 20: P * 75 // 100}) == 4_000_000

    def test_closed_loan_counts_zero(self):
        assert link_exposure(LINK, {10: SURESH, 20: RAVI}, {10: P, 20: 0}) == 0

    def test_topup_never_raises_above_link_amount(self):
        assert link_exposure(LINK, {10: SURESH, 20: RAVI}, {10: P, 20: P * 2}) == P

    def test_partial_link_scales_by_ratio(self):
        link = FundingLink(10, 20, 3_000_000, D(2026, 1, 1))  # Rs 30,000 of Suresh's 1 lakh
        # Ravi has repaid 1/3 of his principal -> Rs 20,000 of the link still out
        assert link_exposure(link, {10: SURESH, 20: RAVI}, {10: P, 20: P * 2 // 3}) == 2_000_000

    def test_rounds_half_up_to_paise(self):
        link = FundingLink(10, 20, 1, D(2026, 1, 1))
        assert link_exposure(link, {10: SURESH, 20: RAVI}, {10: P, 20: P // 2}) == 1  # 0.5 -> 1

    def test_spread_uses_outstanding_amounts(self):
        rep = spread([LINK], {10: SURESH, 20: RAVI}, outstanding={10: P, 20: P // 2})
        assert rep.links[0].exposure_paise == P // 2
        assert rep.links[0].spread_paise_per_cycle == 50_000   # half of 100_000

    def test_idle_cost_frees_up_as_given_loan_repays(self):
        # Ravi repaid half: half of Suresh's 10 lakh now sits unlinked at 1.5%
        rep = spread([LINK], {10: SURESH, 20: RAVI}, outstanding={10: P, 20: P // 2})
        assert rep.idle_cost_paise_per_cycle == 75_000

    def test_without_outstanding_keeps_original_amounts(self):
        rep = spread([LINK], {10: SURESH, 20: RAVI})
        assert rep.links[0].exposure_paise == P
