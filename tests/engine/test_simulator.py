"""What-if simulator (Phase 12). Pure: the real engine on copies, nothing saved.

Loan: Rs 1,00,000 from 1 Jan 2026 at 2%/month, due on the 1st (Rs 2,000 a month).
Window: today = 1 Jan, horizon = 1 Jul (6 dues). Interest is assumed paid on each
due date in both the actual and the simulated path, so only the change differs."""
from decimal import Decimal

import pytest
from hypothesis import given, settings as hsettings, strategies as st

from loan_tracker.simulator import Scenario, new_loan_flows, project
from tests.engine.helpers import D, P, disburse, terms

T0, H = D(2026, 1, 1), D(2026, 7, 1)


def run(scenario=None, t=None, entries=None):
    t = t or terms()
    return project(t, entries if entries is not None else [disburse(t)], T0, H, scenario or Scenario("NONE"))


def test_baseline_pays_every_due_on_time():
    p = run()
    assert [(d.due_date, d.interest_paise) for d in p.dues] == [
        (D(2026, m, 1), 200_000) for m in range(2, 8)]
    assert (p.interest_paise, p.payments_paise, p.principal_left_paise, p.interest_left_paise) == (
        1_200_000, 1_200_000, P, 0)
    assert p.effective_rate == Decimal("2")


def test_extra_payment_halves_later_interest():
    p = run(Scenario("EXTRA_PAYMENT", D(2026, 3, 1), amount_paise=P // 2))
    assert p.interest_paise == 2 * 200_000 + 4 * 100_000          # Rs 4,000 saved over the window
    assert (p.principal_left_paise, p.payments_paise) == (P // 2, 800_000 + P // 2)


def test_payoff_mid_cycle_charges_interest_to_the_day():
    p = run(Scenario("PAYOFF", D(2026, 3, 15)))
    closing = 90_323                                               # 14 of 31 March days: 2,000 x 14/31
    assert p.interest_paise == 2 * 200_000 + closing
    assert (p.principal_left_paise, p.closed_on) == (0, D(2026, 3, 15))
    assert p.payments_paise == 2 * 200_000 + P + closing


def test_rate_change():
    p = run(Scenario("RATE_CHANGE", D(2026, 4, 1), new_rate=Decimal("1.5")))
    assert p.interest_paise == 3 * 200_000 + 3 * 150_000
    assert p.effective_rate == Decimal("1.5")


def test_missed_months_leave_arrears():
    p = run(Scenario("MISS", D(2026, 3, 1), months=2))
    assert (p.interest_paise, p.payments_paise, p.interest_left_paise) == (1_200_000, 800_000, 400_000)


def test_topup():
    p = run(Scenario("TOPUP", D(2026, 3, 1), amount_paise=P // 2))
    assert p.interest_paise == 2 * 200_000 + 4 * 300_000
    assert (p.topups_paise, p.principal_left_paise) == (P // 2, P + P // 2)


def test_principal_repaid_at_maturity():
    t = terms(maturity_date=D(2026, 6, 1))
    p = run(t=t)
    assert (p.interest_paise, p.principal_left_paise, p.closed_on) == (5 * 200_000, 0, D(2026, 6, 1))
    assert p.payments_paise == 5 * 200_000 + P


def test_scenario_date_must_be_in_window():
    with pytest.raises(ValueError, match="between"):
        run(Scenario("EXTRA_PAYMENT", D(2025, 12, 1), amount_paise=1))
    with pytest.raises(ValueError, match="amount"):
        run(Scenario("TOPUP", D(2026, 2, 1)))


def test_real_entries_are_not_changed():
    t = terms()
    entries = [disburse(t)]
    before = list(entries)
    run(Scenario("EXTRA_PAYMENT", D(2026, 3, 1), amount_paise=P // 2), t, entries)
    assert entries == before


def test_new_loan_flows():
    flows = new_loan_flows(terms(start_date=D(2026, 1, 15)), D(2026, 1, 15), D(2026, 4, 15))
    assert flows == [(D(2026, 1, 15), -P), (D(2026, 2, 1), 109_677),       # 17 of 31 January days
                     (D(2026, 3, 1), 200_000), (D(2026, 4, 1), 200_000)]
    taken = new_loan_flows(terms(direction="TAKEN", start_date=D(2026, 1, 15)), D(2026, 1, 15), D(2026, 2, 15))
    assert taken == [(D(2026, 1, 15), P), (D(2026, 2, 1), -109_677)]



@hsettings(max_examples=60, deadline=None)
@given(rate=st.decimals(min_value=Decimal("0.5"), max_value=Decimal("5"), places=2),
       due_day=st.integers(1, 31), months=st.integers(1, 18),
       cycle=st.sampled_from(["MONTHLY", "QUARTERLY"]), principal=st.integers(1_000, 50_000_000))
def test_on_time_path_pays_everything(rate, due_day, months, cycle, principal):
    t = terms(rate=rate, due_day=due_day, interest_cycle=cycle, principal_paise=principal)
    entries = [disburse(t)]
    snapshot = list(entries)
    horizon = D(2026, 1, 1).replace(year=2026 + (months // 12), month=1 + months % 12)
    p = project(t, entries, T0, horizon, Scenario("NONE"))
    assert p.interest_left_paise == 0 and p.principal_left_paise == principal
    assert p.payments_paise == p.interest_paise and all(d.paid for d in p.dues)
    assert entries == snapshot and len(entries) == 1        # the real entries are untouched
