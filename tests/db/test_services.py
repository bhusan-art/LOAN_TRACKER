"""Services against real PostgreSQL: Example A through the database gives
the same numbers as the pure engine; append-only and validation rules hold."""
from datetime import date as D
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from loan_tracker import reports, services as svc
from loan_tracker.db import models as m

P = 10_000_000


def make_loan(s, direction="GIVEN", book="PERSONAL", rate="2", due_day=1, name="Ravi",
              start=D(2026, 1, 1), principal=P, as_of=D(2026, 1, 1), **kw):
    party = svc.create_party(s, name)
    return svc.create_loan(s, party_id=party.id, direction=direction, book=book,
                           principal_paise=principal, start_date=start, rate=Decimal(rate),
                           due_day=due_day, as_of=as_of, **kw)


def example_a(s, as_of=D(2026, 4, 1)):
    loan = make_loan(s)
    p1, _ = svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2026, 2, 5),
                           amount_paise=150_000, as_of=D(2026, 2, 5))
    p2, res = svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2026, 3, 15),
                             amount_paise=3_250_000, as_of=as_of)
    return loan, p1, p2, res


class TestExampleAThroughDB:
    def test_balances_and_cache(self, s):
        loan, _, p2, res = example_a(s)
        b = res.balances
        assert (b.principal_outstanding_paise, b.interest_accrued_paise, b.payoff_today_paise) == (
            7_000_000, 567_097, 7_167_097)
        dues = s.scalars(select(m.Due).where(m.Due.loan_id == loan.id).order_by(m.Due.due_date)).all()
        assert [d.interest_paise for d in dues] == [200_000, 200_000, 167_097]
        allocs = s.scalars(select(m.Allocation).where(m.Allocation.ledger_entry_id == p2.id)).all()
        assert sorted((a.bucket, a.amount_paise) for a in allocs) == [
            ("INTEREST", 50_000), ("INTEREST", 200_000), ("PRINCIPAL", 3_000_000)]

    def test_reversal_recomputes_everything(self, s):
        loan, p1, _, _ = example_a(s)
        res = svc.reverse_entry(s, p1.id, entry_date=D(2026, 3, 20), as_of=D(2026, 4, 1))
        assert res.balances.principal_outstanding_paise == 7_150_000
        apr = s.scalar(select(m.Due).where(m.Due.loan_id == loan.id, m.Due.due_date == D(2026, 4, 1)))
        assert apr.interest_paise == 168_742
        with pytest.raises(svc.ServiceError, match="already reversed"):
            svc.reverse_entry(s, p1.id, entry_date=D(2026, 3, 21), as_of=D(2026, 4, 1))

    def test_payoff_closes_loan(self, s):
        loan = make_loan(s, as_of=D(2026, 4, 15))
        svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2026, 4, 15),
                       amount_paise=10_693_333, as_of=D(2026, 4, 15))
        assert loan.status == "CLOSED"


class TestLedgerIsAppendOnly:
    def test_update_blocked_by_trigger(self, s):
        loan, p1, _, _ = example_a(s)
        with pytest.raises(DBAPIError, match="append-only"):
            with s.begin_nested():
                s.execute(text("UPDATE ledger_entry SET amount_paise = 1 WHERE id = :i"), {"i": p1.id})

    def test_delete_blocked_by_trigger(self, s):
        loan, p1, _, _ = example_a(s)
        with pytest.raises(DBAPIError, match="append-only"):
            with s.begin_nested():
                s.execute(text("DELETE FROM ledger_entry WHERE id = :i"), {"i": p1.id})

    def test_party_cannot_be_deleted_but_can_be_edited(self, s):
        party = svc.create_party(s, "Mahesh", phone="1")
        svc.update_party(s, party.id, phone="98480 00000")
        assert party.phone == "98480 00000"
        with pytest.raises(DBAPIError, match="cannot be deleted"):
            with s.begin_nested():
                s.execute(text("DELETE FROM party WHERE id = :i"), {"i": party.id})


class TestValidation:
    def test_book_required(self, s):
        party = svc.create_party(s, "X")
        with pytest.raises(svc.ServiceError, match="book"):
            svc.create_loan(s, party_id=party.id, direction="GIVEN", book="", principal_paise=P,
                            start_date=D(2026, 1, 1), rate=Decimal(2), as_of=D(2026, 1, 1))

    def test_future_entry_rejected(self, s):
        loan = make_loan(s)
        with pytest.raises(svc.ServiceError, match="future"):
            svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2026, 5, 1),
                           amount_paise=1, as_of=D(2026, 4, 1))

    def test_disbursement_cannot_be_reversed(self, s):
        loan = make_loan(s)
        with pytest.raises(svc.ServiceError, match="disbursement"):
            svc.reverse_entry(s, loan.entries[0].id, entry_date=D(2026, 1, 2), as_of=D(2026, 1, 2))

    def test_rupee_parsing(self):
        assert svc.rupees_to_paise("1,00,000.50") == 10_000_050
        assert svc.rupees_to_paise("₹1,500") == 150_000
        with pytest.raises(svc.ServiceError):
            svc.rupees_to_paise("1.005")


class TestFunding:
    def test_cross_book_needs_confirmation(self, s):
        taken = make_loan(s, direction="TAKEN", book="PERSONAL", rate="1", name="Relative")
        given = make_loan(s, direction="GIVEN", book="BUSINESS", rate="2.5")
        with pytest.raises(svc.ServiceError, match="confirm"):
            svc.link_funding(s, taken_loan_id=taken.id, given_loan_id=given.id,
                             amount_paise=5_000_000, link_date=D(2026, 1, 1))
        svc.link_funding(s, taken_loan_id=taken.id, given_loan_id=given.id, amount_paise=5_000_000,
                         link_date=D(2026, 1, 1), confirm_cross_book=True)
        assert len(reports.cross_book_report(s, D(2026, 1, 2))) == 1

    def test_direction_and_overlink_checks(self, s):
        taken = make_loan(s, direction="TAKEN", name="Suresh")
        given = make_loan(s, direction="GIVEN")
        with pytest.raises(svc.ServiceError, match="BORROWED"):
            svc.link_funding(s, taken_loan_id=given.id, given_loan_id=taken.id,
                             amount_paise=1, link_date=D(2026, 1, 1))
        with pytest.raises(svc.ServiceError, match="more than"):
            svc.link_funding(s, taken_loan_id=taken.id, given_loan_id=given.id,
                             amount_paise=P + 1, link_date=D(2026, 1, 1))


class TestDashboard:
    def test_example_b_portfolio(self, s):
        as_of = D(2026, 1, 2)
        suresh = make_loan(s, direction="TAKEN", book="BUSINESS", rate="1.5", due_day=5, name="Suresh")
        ravi = make_loan(s, direction="GIVEN", book="BUSINESS", rate="2.5", due_day=10, name="Ravi")
        svc.link_funding(s, taken_loan_id=suresh.id, given_loan_id=ravi.id, amount_paise=P,
                         link_date=D(2026, 1, 1))
        db = reports.dashboard(s, as_of, book="BUSINESS")
        assert (db.lent_out_paise, db.borrowed_paise, db.own_capital_deployed_paise) == (P, P, 0)
        assert [w.kind for w in db.warnings] == ["WARNING"]
        assert db.idle_cost_paise_per_cycle == 0
        assert reports.dashboard(s, as_of, book="PERSONAL").lent_out_paise == 0

    def test_repayment_shrinks_link_exposure(self, s):
        as_of = D(2026, 1, 2)
        suresh = make_loan(s, direction="TAKEN", book="PERSONAL", rate="1.5", due_day=5, name="Suresh")
        ravi = make_loan(s, direction="GIVEN", book="BUSINESS", rate="2.5", due_day=10, name="Ravi")
        svc.link_funding(s, taken_loan_id=suresh.id, given_loan_id=ravi.id, amount_paise=P // 2,
                         link_date=D(2026, 1, 1), confirm_cross_book=True)
        svc.post_entry(s, ravi.id, type="PAYMENT", entry_date=as_of, as_of=as_of, amount_paise=P // 2)
        db = reports.dashboard(s, as_of, book="BUSINESS")   # Suresh is in the other book
        # Ravi repaid half: the Rs 50,000 link now funds Rs 25,000; Rs 25,000 of his balance is own money
        assert (db.lent_out_paise, db.own_capital_deployed_paise) == (P // 2, P // 4)
        everything = reports.dashboard(s, as_of)
        assert everything.idle_cost_paise_per_cycle == 112_500   # 75,000 of Suresh's 1 lakh unlinked at 1.5%

    def test_cash_calendar_next_30_days(self, s):
        as_of = D(2026, 1, 2)
        make_loan(s, direction="TAKEN", book="BUSINESS", rate="1.5", due_day=5, name="Suresh")
        make_loan(s, direction="GIVEN", book="BUSINESS", rate="2.5", due_day=10, name="Ravi")
        days = reports.cash_calendar(s, as_of, days=30)
        by_day = {d.day: d for d in days}
        # Suresh: 5 Jan due = 4 of 31 days at 1.5% = 19_355; Ravi 10 Jan = 9 of 31 days at 2.5% = 72_581
        assert by_day[D(2026, 1, 5)].money_out[0].amount_paise == 19_355
        assert by_day[D(2026, 1, 10)].money_in[0].amount_paise == 72_581
        assert days[-1].running_paise == sum(d.net_paise for d in days)

    def test_fy_report_and_csv(self, s):
        example_a(s)
        fy = reports.fy_interest_report(s, D(2026, 4, 1))
        assert {(r["fy"], r["book"]) for r in fy} == {("FY 2025-26", "PERSONAL"), ("FY 2026-27", "PERSONAL")}
        csv_text = reports.export_csv(s, D(2026, 4, 1))
        assert "PRINCIPAL,30000.00" in csv_text


def test_csv_neutralises_formulas(s):
    make_loan(s, name="=HYPERLINK(\"http://x\")")
    assert "'=HYPERLINK" in reports.export_csv(s, D(2026, 1, 2))


class TestAuditLog:
    def test_append_only_and_no_secrets(self, s):
        row = svc.audit(s, "owner", "LOGIN_OK", "app_user", 1, ip="10.0.0.5",
                        password="hunter2", totp_code="123456", note="hello")
        assert row.detail == {"note": "hello"}
        from sqlalchemy import text
        with pytest.raises(Exception, match="append-only"):
            with s.begin_nested():
                s.execute(text("UPDATE audit_log SET actor = 'x' WHERE id = :i"), {"i": row.id})
        with pytest.raises(Exception, match="append-only"):
            with s.begin_nested():
                s.execute(text("DELETE FROM audit_log WHERE id = :i"), {"i": row.id})


class TestCollections:
    """Loan: Rs 1 lakh given 1 Jan at 2%/month, due day 1 -> Rs 2,000 due 1 Feb, overdue after 4 Feb."""

    def test_validation(self, s):
        loan = make_loan(s)
        taken = make_loan(s, direction="TAKEN", name="Lender")
        a = dict(actor="owner", as_of=D(2026, 2, 10))
        with pytest.raises(svc.ServiceError, match="money you lent"):
            svc.log_contact(s, taken.id, **a)
        with pytest.raises(svc.ServiceError, match="amount"):
            svc.log_contact(s, loan.id, outcome="PROMISED", promise_date=D(2026, 2, 15), **a)
        with pytest.raises(svc.ServiceError, match="date they will pay"):
            svc.log_contact(s, loan.id, outcome="PROMISED", promise_amount_paise=100, **a)
        with pytest.raises(svc.ServiceError, match="future"):
            svc.log_contact(s, loan.id, contact_date=D(2026, 2, 11), **a)
        with pytest.raises(svc.ServiceError, match="Promised to pay"):
            svc.log_contact(s, loan.id, outcome="REACHED", promise_amount_paise=100, **a)
        with pytest.raises(svc.ServiceError, match="follow-up"):
            svc.log_contact(s, loan.id, next_follow_up=D(2026, 2, 9), **a)

    def test_promise_follows_real_payments_and_reversals(self, s):
        loan = make_loan(s)
        _, p = svc.log_contact(s, loan.id, actor="owner", as_of=D(2026, 2, 10), outcome="PROMISED",
                               promise_amount_paise=200_000, promise_date=D(2026, 2, 15))
        assert svc.promise_state(p, svc.get_loan(s, loan.id), D(2026, 2, 12)).status == "PENDING"
        e, _ = svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2026, 2, 14), amount_paise=200_000,
                              as_of=D(2026, 2, 14))
        s.expire_all()
        assert svc.promise_state(p, svc.get_loan(s, loan.id), D(2026, 2, 20)).status == "KEPT"
        svc.reverse_entry(s, e.id, entry_date=D(2026, 2, 20), as_of=D(2026, 2, 20))
        s.expire_all()
        st = svc.promise_state(p, svc.get_loan(s, loan.id), D(2026, 2, 20))
        assert (st.status, st.short_paise) == ("BROKEN", 200_000)

    def test_promise_never_changes_money(self, s):
        loan = make_loan(s)
        before = svc.compute_loan(svc.get_loan(s, loan.id), D(2026, 2, 20)).balances
        svc.log_contact(s, loan.id, actor="owner", as_of=D(2026, 2, 10), outcome="PROMISED",
                        promise_amount_paise=200_000, promise_date=D(2026, 2, 15))
        s.expire_all()
        assert svc.compute_loan(svc.get_loan(s, loan.id), D(2026, 2, 20)).balances == before

    def test_history_is_protected(self, s):
        loan = make_loan(s)
        act, p = svc.log_contact(s, loan.id, actor="owner", as_of=D(2026, 2, 10), outcome="PROMISED",
                                 promise_amount_paise=100, promise_date=D(2026, 2, 15))
        for sql in ("UPDATE collection_activity SET note = 'x' WHERE id = :i",
                    "DELETE FROM collection_activity WHERE id = :i"):
            with pytest.raises(DBAPIError, match="append-only"):
                with s.begin_nested():
                    s.execute(text(sql), {"i": act.id})
        with pytest.raises(DBAPIError, match="only be cancelled"):
            with s.begin_nested():
                s.execute(text("UPDATE payment_promise SET amount_paise = 1 WHERE id = :i"), {"i": p.id})
        with pytest.raises(DBAPIError, match="cannot be deleted"):
            with s.begin_nested():
                s.execute(text("DELETE FROM payment_promise WHERE id = :i"), {"i": p.id})
        svc.cancel_promise(s, p.id, "settled by phone")
        with pytest.raises(svc.ServiceError, match="already cancelled"):
            svc.cancel_promise(s, p.id)
        assert svc.promise_state(p, svc.get_loan(s, loan.id), D(2026, 3, 1)).status == "CANCELLED"


class TestCollectionsOverview:
    def test_who_to_chase(self, s):
        loan = make_loan(s, name="Ravi")
        make_loan(s, name="Quiet")
        ov = reports.collections_overview(s, D(2026, 2, 10))
        assert {i.party for i in ov.not_contacted} == {"Ravi", "Quiet"}
        assert ov.not_contacted[0].overdue_paise == 200_000

        svc.log_contact(s, loan.id, actor="o", as_of=D(2026, 2, 10), outcome="NO_ANSWER",
                        next_follow_up=D(2026, 2, 12), note="phone off")
        ov = reports.collections_overview(s, D(2026, 2, 11))
        assert [i.party for i in ov.not_contacted] == ["Quiet"] and ov.follow_ups == []
        ov = reports.collections_overview(s, D(2026, 2, 12))
        assert [(i.party, i.detail) for i in ov.follow_ups] == [("Ravi", "Follow-up today · last: phone off")]

        svc.log_contact(s, loan.id, actor="o", as_of=D(2026, 2, 12), outcome="PROMISED",
                        promise_amount_paise=200_000, promise_date=D(2026, 2, 15))
        ov = reports.collections_overview(s, D(2026, 2, 13))
        assert [(i.party, i.detail) for i in ov.promises_due] == [("Ravi", "Promised in 2 days")]
        assert "Ravi" not in {i.party for i in ov.not_contacted}   # a pending promise is enough for now

        ov = reports.collections_overview(s, D(2026, 2, 16))
        assert [(i.party, i.short_paise) for i in ov.broken] == [("Ravi", 200_000)]
        svc.log_contact(s, loan.id, actor="o", as_of=D(2026, 2, 16), outcome="REACHED")
        assert reports.collections_overview(s, D(2026, 2, 16)).broken == []   # chased since

    def test_whatsapp_link_needs_a_phone(self, s):
        loan = make_loan(s, name="Ravi")
        assert reports.collections_overview(s, D(2026, 2, 10)).not_contacted[0].wa_url is None
        svc.update_party(s, loan.party_id, name="Ravi", phone="98480 12345")
        s.expire_all()
        url = reports.collections_overview(s, D(2026, 2, 10)).not_contacted[0].wa_url
        assert url.startswith("https://wa.me/919848012345?text=Hello%20Ravi")


class TestLoanRowLock:
    """Writes lock the loan row so concurrent writes to one loan run in turn."""

    def _sql(self, s, fn):
        from sqlalchemy import event
        seen = []
        conn = s.connection()
        listen = lambda *a: seen.append(a[2])
        event.listen(conn, "before_cursor_execute", listen)
        try:
            fn()
        finally:
            event.remove(conn, "before_cursor_execute", listen)
        return seen

    def test_payment_reversal_and_status_lock_the_loan(self, s):
        loan = make_loan(s)
        locked = lambda sql: any("FROM loan" in q and "FOR UPDATE" in q for q in sql)
        holder = {}
        sql = self._sql(s, lambda: holder.update(e=svc.post_entry(
            s, loan.id, type="PAYMENT", entry_date=D(2026, 2, 1), amount_paise=100, as_of=D(2026, 2, 1))[0]))
        assert locked(sql)
        assert locked(self._sql(s, lambda: svc.reverse_entry(s, holder["e"].id, entry_date=D(2026, 2, 2),
                                                             as_of=D(2026, 2, 2))))
        assert locked(self._sql(s, lambda: svc.set_owner_status(s, loan.id, "SETTLED", D(2026, 2, 2))))
