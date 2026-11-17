"""Property tests: the engine's promises hold for random ledgers (engine.md §5.3)."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from loan_tracker.engine import compute
from tests.engine.helpers import D, entry, terms

START = D(2026, 1, 1)


@st.composite
def ledgers(draw):
    t = terms(
        principal_paise=draw(st.integers(100_000, 50_000_000)),
        rate=Decimal(draw(st.sampled_from(["0.5", "1", "1.5", "2", "2.5", "3", "36"]))),
        rate_unit=draw(st.sampled_from(["PER_MONTH", "PER_MONTH", "PER_YEAR"])),
        interest_cycle=draw(st.sampled_from(["MONTHLY", "MONTHLY", "QUARTERLY", "AT_MATURITY"])),
        due_day=draw(st.integers(1, 31)),
        start_date=START + timedelta(days=draw(st.integers(0, 60))),
        maturity_date=None,
    )
    if t.interest_cycle == "AT_MATURITY":
        t = replace(t, maturity_date=t.start_date + timedelta(days=draw(st.integers(30, 200))))
    es = [entry("DISBURSE", t.start_date, t.principal_paise, entry_id=1)]
    n = draw(st.integers(0, 12))
    for i in range(n):
        d = t.start_date + timedelta(days=draw(st.integers(0, 400)))
        kind = draw(st.sampled_from(["PAYMENT", "PAYMENT", "PAYMENT", "TOPUP", "RATE_CHANGE", "WAIVER"]))
        if kind == "RATE_CHANGE":
            es.append(entry(kind, d, new_rate=draw(st.sampled_from(["1", "2", "3"])), entry_id=i + 2))
        else:
            es.append(entry(kind, d, draw(st.integers(1, 20_000_000)), entry_id=i + 2))
    if draw(st.booleans()) and n:
        es.append(entry("REVERSAL", t.start_date + timedelta(days=401),
                        reverses=draw(st.integers(2, n + 1)), entry_id=99))
    return t, es


AS_OF = START + timedelta(days=500)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(ledgers())
def test_money_is_conserved(case):
    t, es = case
    res = compute(t, es, AS_OF)
    live_paid = sum(e.amount_paise for e in es if e.type == "PAYMENT"
                    and e.entry_id not in {x.reverses for x in es if x.type == "REVERSAL"})
    assert sum(a.amount_paise for a in res.allocations) + res.balances.credit_paise == live_paid
    assert all(a.amount_paise > 0 for a in res.allocations)
    assert res.balances.principal_outstanding_paise >= 0
    for d in res.dues:
        assert d.paid_paise + d.waived_paise <= d.due.interest_paise + d.penalty_paise
        assert d.outstanding_paise >= 0


@settings(max_examples=100, deadline=None, derandomize=True)
@given(ledgers(), st.integers(1, 499))
def test_history_never_changes(case, offset):
    """Dues realized by an earlier date are identical when computed later."""
    t, es = case
    early = START + timedelta(days=offset)
    a = compute(t, es, early)
    b = compute(t, es, AS_OF)
    later = {d.due.period_start: d.due for d in b.dues}
    for d in a.dues:
        assert later[d.due.period_start] == d.due


@settings(max_examples=100, deadline=None, derandomize=True)
@given(ledgers())
def test_reversal_equals_absence(case):
    t, es = case
    rev = [e for e in es if e.type == "REVERSAL"]
    if not rev:
        return
    gone = {rev[0].reverses}
    without = [e for e in es if e.type != "REVERSAL" and e.entry_id not in gone]
    a, b = compute(t, es, AS_OF), compute(t, without, AS_OF)
    assert a.balances == b.balances and a.dues == b.dues


@settings(max_examples=100, deadline=None, derandomize=True)
@given(ledgers())
def test_given_and_taken_identical(case):
    t, es = case
    g = compute(t, es, AS_OF)
    k = compute(replace(t, direction="TAKEN", book="BUSINESS"), es, AS_OF)
    assert g.balances == k.balances and g.dues == k.dues


@settings(max_examples=100, deadline=None, derandomize=True)
@given(ledgers())
def test_same_input_same_output(case):
    t, es = case
    assert compute(t, es, AS_OF) == compute(t, list(reversed(es)), AS_OF)
