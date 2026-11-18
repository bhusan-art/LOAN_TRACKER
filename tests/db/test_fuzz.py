"""Randomised end-to-end check through services and the real database (review after Phase 13).

The pure engine has its own property tests; this one also exercises the
database rules (CHECK constraints, triggers, the due/allocation caches), which
is where the one-day-period bug hid. Every step must either succeed or fail
with a ServiceError (a message for the owner) - never an integrity error."""
from datetime import date, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings, strategies as st
from sqlalchemy import func, select

from loan_tracker import services as svc
from loan_tracker.db import models as m

START = date(2025, 1, 1)
AS_OF = date(2026, 3, 1)

op = st.one_of(
    st.tuples(st.just("PAYMENT"), st.integers(-30, 420), st.integers(1, 3_000_000)),
    st.tuples(st.just("TOPUP"), st.integers(-30, 420), st.integers(1, 2_000_000)),
    st.tuples(st.just("WAIVER"), st.integers(-30, 420), st.integers(1, 200_000)),
    st.tuples(st.just("PENALTY"), st.integers(-30, 420), st.integers(1, 50_000)),
    st.tuples(st.just("RATE_CHANGE"), st.integers(-30, 420), st.decimals("0", "4", places=2)),
    st.tuples(st.just("REVERSE"), st.integers(0, 30), st.just(0)),
    st.tuples(st.just("PAYOFF"), st.integers(-30, 420), st.just(0)),
)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(due_day=st.integers(1, 31), start_shift=st.integers(0, 60), cycle=st.sampled_from(["MONTHLY", "QUARTERLY"]),
       maturity_months=st.one_of(st.none(), st.integers(1, 14)), direction=st.sampled_from(["GIVEN", "TAKEN"]),
       principal=st.integers(1_000, 50_000_000), ops=st.lists(op, max_size=12))
def test_random_loans_never_break_the_database(s, due_day, start_shift, cycle, maturity_months, direction,
                                               principal, ops):
    sp = s.begin_nested()
    try:
        start = START + timedelta(days=start_shift)
        maturity = None
        if maturity_months:
            y, mth = divmod(start.month - 1 + maturity_months, 12)
            maturity = date(start.year + y, mth + 1, min(start.day, 28))
        party = svc.create_party(s, "Fuzz")
        loan = svc.create_loan(s, party_id=party.id, direction=direction, book="BUSINESS", principal_paise=principal,
                               start_date=start, rate=Decimal("2"), due_day=due_day, interest_cycle=cycle,
                               maturity_date=maturity, as_of=start)
        posted, wrote = [], False
        for kind, offset, value in ops:
            when = min(start + timedelta(days=offset), AS_OF)
            try:
                if kind == "REVERSE":
                    if not posted:
                        continue
                    svc.reverse_entry(s, posted[offset % len(posted)], entry_date=AS_OF, as_of=AS_OF)
                elif kind == "PAYOFF":
                    owed = svc.compute_loan(svc.get_loan(s, loan.id), when).balances.payoff_today_paise
                    if not owed:
                        continue
                    e, _ = svc.post_entry(s, loan.id, type="PAYMENT", entry_date=when, amount_paise=owed,
                                          as_of=AS_OF)
                    posted.append(e.id)
                elif kind == "RATE_CHANGE":
                    e, _ = svc.post_entry(s, loan.id, type="RATE_CHANGE", entry_date=when, new_rate=value, as_of=AS_OF)
                    posted.append(e.id)
                elif kind != "REVERSE":
                    e, _ = svc.post_entry(s, loan.id, type=kind, entry_date=when, amount_paise=value, as_of=AS_OF)
                    posted.append(e.id)
                wrote = True                           # the caches were just rebuilt as of AS_OF
            except svc.ServiceError:
                pass                                   # a refusal with a message is fine
            if not wrote:
                continue
            # invariants after every step
            fresh = svc.compute_loan(svc.get_loan(s, loan.id), AS_OF)
            b = fresh.balances
            assert min(b.principal_outstanding_paise, b.interest_outstanding_paise, b.penalty_outstanding_paise,
                       b.payoff_today_paise) >= 0
            cached = s.execute(select(func.count(), func.coalesce(func.sum(m.Due.interest_paise), 0))
                               .where(m.Due.loan_id == loan.id)).one()
            svc.recompute_loan(s, svc.get_loan(s, loan.id), AS_OF)
            rebuilt = s.execute(select(func.count(), func.coalesce(func.sum(m.Due.interest_paise), 0))
                                .where(m.Due.loan_id == loan.id)).one()
            assert tuple(cached) == tuple(rebuilt), "due cache drifted from the ledger"
    finally:
        sp.rollback()
