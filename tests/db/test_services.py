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


class TestReceipts:
    """Example A loan: Rs 1 lakh at 2%/month from 1 Jan, due day 1."""

    def test_payment_issues_numbered_receipt_with_split(self, s):
        loan, p1, p2, _ = example_a(s)
        r1, r2 = (s.scalar(select(m.Receipt).where(m.Receipt.ledger_entry_id == p.id)) for p in (p1, p2))
        assert (r1.number, r2.number) == ("RCPT-2026-0001", "RCPT-2026-0002")
        # p1 Rs 1,500 on 5 Feb: all interest (Rs 2,000 due 1 Feb), Rs 500 interest still owed
        assert (r1.interest_paise, r1.principal_paise, r1.credit_paise) == (150_000, 0, 0)
        assert (r1.principal_left_paise, r1.interest_left_paise) == (P, 50_000)
        # p2 Rs 32,500 on 15 Mar: oldest interest first (500 + 2,000), the rest to principal
        assert (r2.interest_paise, r2.principal_paise) == (250_000, 3_000_000)
        assert r2.principal_left_paise == P - 3_000_000
        assert r2.issued_on == D(2026, 4, 1) and r2.payment_date == D(2026, 3, 15)

    def test_taken_loans_get_no_receipt(self, s):
        loan = make_loan(s, direction="TAKEN")
        e, _ = svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2026, 2, 1), amount_paise=100,
                              as_of=D(2026, 2, 1))
        assert s.scalar(select(m.Receipt).where(m.Receipt.ledger_entry_id == e.id)) is None
        with pytest.raises(svc.ServiceError, match="given loans"):
            svc.issue_receipt(s, e.id, D(2026, 2, 1))

    def test_reversal_cancels_receipt_and_number_is_not_reused(self, s):
        loan, p1, _, _ = example_a(s)
        svc.reverse_entry(s, p1.id, entry_date=D(2026, 4, 2), as_of=D(2026, 4, 2), note="wrong loan")
        r1 = s.scalar(select(m.Receipt).where(m.Receipt.ledger_entry_id == p1.id))
        assert r1.cancelled_at is not None and r1.cancel_reason == "payment reversed on 02 Apr 2026: wrong loan"
        e, _ = svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2026, 4, 2), amount_paise=100,
                              as_of=D(2026, 4, 2))
        assert s.scalar(select(m.Receipt.number).where(m.Receipt.ledger_entry_id == e.id)) == "RCPT-2026-0003"
        with pytest.raises(svc.ServiceError, match="reversed"):
            svc.issue_receipt(s, p1.id, D(2026, 4, 2))

    def test_overpayment_shows_as_credit(self, s):
        loan = make_loan(s)
        e, _ = svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2026, 1, 1), amount_paise=P + 500,
                              as_of=D(2026, 1, 1))
        r = s.scalar(select(m.Receipt).where(m.Receipt.ledger_entry_id == e.id))
        assert (r.principal_paise, r.credit_paise, r.principal_left_paise) == (P, 500, 0)

    def test_numbering_restarts_each_year(self, s):
        loan = make_loan(s, start=D(2025, 12, 1), as_of=D(2025, 12, 1))
        e1, _ = svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2025, 12, 20), amount_paise=100,
                               as_of=D(2025, 12, 20))
        e2, _ = svc.post_entry(s, loan.id, type="PAYMENT", entry_date=D(2026, 1, 2), amount_paise=100,
                               as_of=D(2026, 1, 2))
        nums = [s.scalar(select(m.Receipt.number).where(m.Receipt.ledger_entry_id == e.id)) for e in (e1, e2)]
        assert nums == ["RCPT-2025-0001", "RCPT-2026-0001"]

    def test_backfill_older_payments_in_payment_order(self, s):
        loan = make_loan(s)
        # simulate payments recorded before Phase 10: insert ledger rows directly, no receipts
        for d, amt in ((D(2026, 3, 1), 200), (D(2026, 2, 1), 100)):
            s.add(m.LedgerEntry(loan_id=loan.id, entry_date=d, type="PAYMENT", amount_paise=amt))
        s.flush()
        assert len(svc.payments_without_receipt(s)) == 2
        made = svc.issue_missing_receipts(s, D(2026, 4, 1))
        assert [(r.payment_date, r.number) for r in made] == [(D(2026, 2, 1), "RCPT-2026-0001"),
                                                              (D(2026, 3, 1), "RCPT-2026-0002")]
        assert svc.payments_without_receipt(s) == []
        with pytest.raises(svc.ServiceError, match="already has receipt RCPT-2026-0001"):
            svc.issue_receipt(s, made[0].ledger_entry_id, D(2026, 4, 1))

    def test_receipt_rows_are_protected(self, s):
        _, p1, _, _ = example_a(s)
        rid = s.scalar(select(m.Receipt.id).where(m.Receipt.ledger_entry_id == p1.id))
        for sql, msg in (("UPDATE receipt SET amount_paise = 1 WHERE id = :i", "only be cancelled"),
                         ("DELETE FROM receipt WHERE id = :i", "cannot be deleted")):
            with pytest.raises(DBAPIError, match=msg):
                with s.begin_nested():
                    s.execute(text(sql), {"i": rid})


def _suresh_ravi(s):
    """Suresh lends you Rs 1 lakh at 1.5% (due 5th); you lend Ravi Rs 1 lakh at 2.5% (due 10th)."""
    suresh = make_loan(s, direction="TAKEN", book="BUSINESS", rate="1.5", due_day=5, name="Suresh")
    ravi = make_loan(s, direction="GIVEN", book="BUSINESS", rate="2.5", due_day=10, name="Ravi")
    return suresh, ravi


class TestCashAccounts:
    def test_accounts_and_balance_history(self, s):
        a = svc.create_cash_account(s, name="SBI", kind="BANK", book="BUSINESS", balance_paise=500_000,
                                    as_of=D(2026, 3, 1))
        svc.create_cash_account(s, name="Wallet", kind="CASH", book="PERSONAL", balance_paise=20_000,
                                as_of=D(2026, 3, 1))
        svc.set_cash_balance(s, a.id, balance_paise=-10_000, on=D(2026, 3, 5), as_of=D(2026, 3, 5), note="OD")
        latest = {acc.name: b.balance_paise for acc, b in svc.latest_balances(s)}
        assert latest == {"SBI": -10_000, "Wallet": 20_000}
        assert [acc.name for acc, _ in svc.latest_balances(s, "PERSONAL")] == ["Wallet"]
        assert [b.balance_paise for b in svc.balance_history(s, a.id)] == [-10_000, 500_000]
        # a late-entered older reading does not replace a newer one
        svc.set_cash_balance(s, a.id, balance_paise=1, on=D(2026, 3, 2), as_of=D(2026, 3, 6))
        assert dict((acc.name, b.balance_paise) for acc, b in svc.latest_balances(s))["SBI"] == -10_000
        svc.set_cash_account_active(s, a.id, False)
        assert [acc.name for acc, _ in svc.latest_balances(s)] == ["Wallet"]

    def test_validation_and_protection(self, s):
        a = svc.create_cash_account(s, name="SBI", kind="BANK", book="BUSINESS", balance_paise=0,
                                    as_of=D(2026, 3, 1))
        with pytest.raises(svc.ServiceError, match="already exists"):
            svc.create_cash_account(s, name="sbi", kind="BANK", book="BUSINESS", balance_paise=0, as_of=D(2026, 3, 1))
        with pytest.raises(svc.ServiceError, match="future"):
            svc.set_cash_balance(s, a.id, balance_paise=1, on=D(2026, 3, 2), as_of=D(2026, 3, 1))
        with pytest.raises(svc.ServiceError, match="today or later"):
            svc.add_planned_item(s, book="BUSINESS", item_date=D(2026, 2, 28), direction="OUT",
                                 amount_paise=1, description="rent", as_of=D(2026, 3, 1))
        with pytest.raises(svc.ServiceError, match="say what"):
            svc.add_planned_item(s, book="BUSINESS", item_date=D(2026, 3, 2), direction="OUT",
                                 amount_paise=1, description=" ", as_of=D(2026, 3, 1))
        it = svc.add_planned_item(s, book="BUSINESS", item_date=D(2026, 3, 2), direction="OUT",
                                  amount_paise=1, description="rent", as_of=D(2026, 3, 1))
        bid = svc.balance_history(s, a.id)[0].id
        for sql, msg in (("UPDATE cash_balance SET balance_paise = 9 WHERE id = :b", "append-only"),
                         ("DELETE FROM cash_balance WHERE id = :b", "append-only"),
                         ("UPDATE planned_cash_item SET amount_paise = 9 WHERE id = :p", "only be cancelled"),
                         ("DELETE FROM planned_cash_item WHERE id = :p", "cannot be deleted")):
            with pytest.raises(DBAPIError, match=msg):
                with s.begin_nested():
                    s.execute(text(sql), {"b": bid, "p": it.id})
        svc.cancel_planned_item(s, it.id)
        with pytest.raises(svc.ServiceError, match="already cancelled"):
            svc.cancel_planned_item(s, it.id)


class TestCashForecast:
    def test_shortfall_from_real_balance_and_planned_items(self, s):
        _suresh_ravi(s)
        t = D(2026, 1, 2)
        acc = svc.create_cash_account(s, name="SBI", kind="BANK", book="BUSINESS", balance_paise=10_000, as_of=t)
        f = reports.cash_forecast(s, t, 30)
        out_jan5 = sum(i.amount_paise for d in f.calendar if d.day == D(2026, 1, 5) for i in d.money_out)
        assert out_jan5 == 19_355                                   # Suresh, 4 days of January
        assert (f.opening_paise, f.expected.short_date, f.expected.short_paise) == (10_000, D(2026, 1, 5), 9_355)
        assert reports.cash_warnings(f) == [
            "Cash: you may be ₹93.55 short on 05 Jan 2026, even if everyone pays on time."]
        # plan Rs 100 coming in on the 3rd: no shortfall any more
        svc.add_planned_item(s, book="BUSINESS", item_date=D(2026, 1, 3), direction="IN", amount_paise=10_000,
                             description="Sold old laptop", as_of=t)
        f = reports.cash_forecast(s, t, 30)
        assert f.expected.short_date is None and reports.cash_warnings(f) == []
        planned = [i for d in f.calendar for i in d.money_in if i.status == "PLANNED"]
        assert [(i.party, i.amount_paise) for i in planned] == [("Sold old laptop", 10_000)]
        # a minimum balance turns it into a warning again
        f = reports.cash_forecast(s, t, 30, buffer_paise=50_000)
        # Rs 100 in hand is already under the Rs 500 minimum: short from today, worst dip on the 5th
        assert (f.expected.short_date, f.expected.short_paise) == (D(2026, 1, 2), 50_000 - (20_000 - 19_355))
        # running total on the calendar starts from the real balance
        assert f.calendar[0].day == D(2026, 1, 3) and f.calendar[0].running_paise == 20_000
        assert acc.id

    def test_cautious_view_leaves_out_late_borrowers(self, s):
        suresh, ravi = _suresh_ravi(s)
        owed = svc.compute_loan(svc.get_loan(s, suresh.id), D(2026, 2, 6)).balances.interest_outstanding_paise
        svc.post_entry(s, suresh.id, type="PAYMENT", entry_date=D(2026, 2, 6), amount_paise=owed,
                       as_of=D(2026, 2, 6))                         # you are up to date with Suresh
        t = D(2026, 2, 20)                                          # Ravi has paid nothing: late
        svc.create_cash_account(s, name="SBI", kind="BANK", book="BUSINESS", balance_paise=0, as_of=t)
        f = reports.cash_forecast(s, t, 30)
        assert f.expected.short_date is None                        # Ravi's arrears land today
        assert (f.cautious.short_date, f.cautious.short_paise) == (D(2026, 3, 5), 150_000)
        assert f.risky_in_paise > 0
        assert reports.cash_warnings(f) == [
            "Cash: if borrowers who are already late do not pay, you may be ₹1,500.00 short on 05 Mar 2026."]

    def test_no_accounts_no_cash_warning_and_stale_warning(self, s):
        _suresh_ravi(s)
        assert reports.cash_warnings(reports.cash_forecast(s, D(2026, 1, 2), 30)) == []
        svc.create_cash_account(s, name="SBI", kind="BANK", book="BUSINESS", balance_paise=10**9,
                                as_of=D(2026, 1, 1))
        lines = reports.cash_warnings(reports.cash_forecast(s, D(2026, 1, 20), 30))
        assert lines == ["Cash balances were last updated 19 days ago; update them so the forecast is right."]

    def test_book_filter_and_dashboard(self, s):
        _suresh_ravi(s)
        t = D(2026, 1, 2)
        svc.create_cash_account(s, name="Home", kind="CASH", book="PERSONAL", balance_paise=10**9, as_of=t)
        f = reports.cash_forecast(s, t, 30, "BUSINESS")
        assert f.has_accounts is False and f.opening_paise == 0
        svc.create_cash_account(s, name="SBI", kind="BANK", book="BUSINESS", balance_paise=0, as_of=t)
        db = reports.dashboard(s, t, "BUSINESS")
        assert any(w.startswith("Cash: you may be ₹193.55 short on 05 Jan 2026") for w in db.warning_text)
        assert not any(w.startswith("Cash:") for w in reports.dashboard(s, t).warning_text)  # Home cash covers it


@pytest.mark.parametrize("bad", ["Infinity", "-inf", "NaN", "abc", "1.005"])
def test_rupees_to_paise_rejects_non_amounts(bad):
    with pytest.raises(svc.ServiceError):
        svc.rupees_to_paise(bad)


def test_rupees_to_paise_negative_for_overdraft():
    assert svc.rupees_to_paise("-5,000.50") == -500_050


class TestSimulator:
    """Suresh lends you Rs 1 lakh at 1.5%; all of it funds Ravi's Rs 1 lakh at 2.5% (Rs 1,000/month spread)."""

    def _setup(self, s):
        suresh, ravi = _suresh_ravi(s)
        svc.link_funding(s, taken_loan_id=suresh.id, given_loan_id=ravi.id, amount_paise=P,
                         link_date=D(2026, 1, 1))
        return suresh, ravi

    def test_early_payoff_frees_the_borrowed_money(self, s):
        from loan_tracker.simulator import Scenario
        _, ravi = self._setup(s)
        sim = reports.simulate_loan(s, ravi.id, D(2026, 1, 1), D(2026, 7, 1), Scenario("PAYOFF", D(2026, 3, 10)))
        assert sim.actual.principal_left_paise == P and sim.simulated.principal_left_paise == 0
        assert sim.simulated.interest_paise < sim.actual.interest_paise
        [eff] = sim.links
        assert (eff.exposure_actual, eff.spread_actual) == (P, 100_000)
        assert (eff.exposure_sim, eff.spread_sim) == (0, 0)       # Suresh's money now idle, earning nothing
        assert eff.other.party.name == "Suresh"

    def test_rate_cut_turns_spread_negative(self, s):
        from decimal import Decimal
        from loan_tracker.simulator import Scenario
        _, ravi = self._setup(s)
        sim = reports.simulate_loan(s, ravi.id, D(2026, 1, 1), D(2026, 7, 1),
                                    Scenario("RATE_CHANGE", D(2026, 4, 10), new_rate=Decimal("1")))
        assert sim.links[0].spread_sim == -50_000

    def test_simulation_saves_nothing(self, s):
        from loan_tracker.simulator import Scenario
        _, ravi = self._setup(s)
        from sqlalchemy import func
        count = lambda: s.scalar(select(func.count()).select_from(m.LedgerEntry))
        before = count()
        reports.simulate_loan(s, ravi.id, D(2026, 1, 1), D(2026, 7, 1),
                              Scenario("EXTRA_PAYMENT", D(2026, 2, 1), amount_paise=100))
        assert count() == before

    def test_bad_scenario_is_a_friendly_error(self, s):
        from loan_tracker.simulator import Scenario
        _, ravi = self._setup(s)
        with pytest.raises(svc.ServiceError, match="between"):
            reports.simulate_loan(s, ravi.id, D(2026, 1, 1), D(2026, 7, 1),
                                  Scenario("TOPUP", D(2027, 1, 1), amount_paise=5))

    def test_new_loan_changes_the_cash_forecast(self, s):
        from decimal import Decimal
        self._setup(s)
        svc.create_cash_account(s, name="SBI", kind="BANK", book="BUSINESS", balance_paise=200_000,
                                as_of=D(2026, 1, 1))       # covers Suresh's 5 Feb interest before Ravi pays
        sim = reports.simulate_new_loan(s, D(2026, 1, 1), 60, direction="GIVEN", book="BUSINESS",
                                        principal_paise=P, rate=Decimal("2"), start_date=D(2026, 1, 15),
                                        due_day=1)
        assert sim.monthly_interest_paise == 200_000
        assert sim.interest_in_window_paise == 109_677 + 200_000    # 1 Feb (17 days) and 1 Mar
        assert sim.before.expected.short_date is None
        assert sim.after.expected.short_date == D(2026, 1, 15)     # you would need the Rs 1 lakh first
        labels = [i.party for d in sim.after.calendar for i in d.money_out if i.status == "SIMULATED"]
        assert labels == ["New loan you give (principal)"]


BANK_CSV = b"""Date,Narration,Chq./Ref.No.,Withdrawal Amt.,Deposit Amt.,Closing Balance
05/02/26,UPI-RAVI-603612345,603612345,,1500.00,26500.00
10/02/26,UPI-RAVI-603612999,603612999,,2000.00,28500.00
12/02/26,ATM WDL,,500.00,,28000.00
"""


class TestBankImport:
    def _setup(self, s):
        loan, p1, p2, _ = example_a(s)        # Ravi: Rs 1,500 on 5 Feb and Rs 32,500 on 15 Mar recorded
        acc = svc.create_cash_account(s, name="HDFC", kind="BANK", book="PERSONAL", balance_paise=0,
                                      as_of=D(2026, 1, 1))
        return loan, p1, acc

    def test_import_skips_duplicates_and_sets_balance(self, s):
        loan, _, acc = self._setup(s)
        imp, bal = svc.import_statement(s, acc.id, BANK_CSV, file_name="feb.csv", actor="o", as_of=D(2026, 4, 1))
        assert (imp.row_count, imp.new_count, imp.period_start, imp.period_end) == (
            3, 3, D(2026, 2, 5), D(2026, 2, 12))
        assert (bal.balance_paise, bal.as_of) == (2_800_000, D(2026, 2, 12))
        again, _ = svc.import_statement(s, acc.id, BANK_CSV, file_name="feb.csv", actor="o", as_of=D(2026, 4, 1))
        assert again.new_count == 0 and len(svc.bank_txns(s)) == 3

    def test_newest_first_statement_takes_the_latest_balance(self, s):
        _, _, acc = self._setup(s)
        lines = BANK_CSV.splitlines()
        newest_first = b"\n".join([lines[0], *reversed(lines[1:])]) + b"\n"
        _, bal = svc.import_statement(s, acc.id, newest_first, file_name="x.csv", actor="o", as_of=D(2026, 4, 1))
        assert (bal.balance_paise, bal.as_of) == (2_800_000, D(2026, 2, 12))

    def test_older_statement_does_not_replace_a_newer_balance(self, s):
        _, _, acc = self._setup(s)
        svc.set_cash_balance(s, acc.id, balance_paise=1, on=D(2026, 3, 1), as_of=D(2026, 4, 1))
        _, bal = svc.import_statement(s, acc.id, BANK_CSV, file_name="x.csv", actor="o", as_of=D(2026, 4, 1))
        assert bal is None

    def test_suggestions_link_record_ignore_undo(self, s):
        loan, p1, acc = self._setup(s)
        imp, _ = svc.import_statement(s, acc.id, BANK_CSV, file_name="feb.csv", actor="o", as_of=D(2026, 4, 1))
        t1, t2, t3 = svc.bank_txns(s, imp.id)
        sug = svc.txn_suggestions(s, D(2026, 4, 1), [t1, t2, t3])
        assert (sug[t1.id][0].kind, sug[t1.id][0].entry_id) == ("EXISTING", p1.id)     # already recorded
        assert sug[t2.id][0].kind == "NEW_PAYMENT" and sug[t2.id][0].loan_id == loan.id  # name in narration
        assert sug[t3.id] == []
        # link-all-exact links only the Rs 1,500 line
        assert [t.id for t in svc.link_exact_matches(s, imp.id, D(2026, 4, 1))] == [t1.id]
        # the same Rs 1,500 seen again on another account's statement cannot claim the same entry
        acc2 = svc.create_cash_account(s, name="SBI", kind="BANK", book="PERSONAL", balance_paise=0,
                                       as_of=D(2026, 1, 1))
        imp2, _ = svc.import_statement(s, acc2.id, BANK_CSV, file_name="dup.csv", actor="o", as_of=D(2026, 4, 1))
        with pytest.raises(svc.ServiceError, match="already linked"):
            svc.link_bank_txn(s, svc.bank_txns(s, imp2.id)[0].id, p1.id)
        # record the Rs 2,000 as a payment: normal waterfall, receipt issued
        t2, e = svc.record_bank_txn(s, t2.id, loan.id, D(2026, 4, 1))
        assert (t2.status, e.payment_mode, e.entry_date, e.amount_paise) == ("RECORDED", "BANK", D(2026, 2, 10), 200_000)
        assert s.scalar(select(m.Receipt.number).where(m.Receipt.ledger_entry_id == e.id))
        with pytest.raises(svc.ServiceError, match="reverse payment"):
            svc.undo_bank_txn(s, t2.id)
        svc.reverse_entry(s, e.id, entry_date=D(2026, 4, 1), as_of=D(2026, 4, 1))
        assert svc.undo_bank_txn(s, t2.id).status == "NEW"
        assert svc.ignore_bank_txn(s, t3.id, "cash for home").status == "IGNORED"

    def test_wrong_direction_and_amount_are_refused(self, s):
        loan, p1, acc = self._setup(s)
        taken = make_loan(s, direction="TAKEN", name="Lender")
        imp, _ = svc.import_statement(s, acc.id, BANK_CSV, file_name="feb.csv", actor="o", as_of=D(2026, 4, 1))
        t1, t2, t3 = svc.bank_txns(s, imp.id)
        with pytest.raises(svc.ServiceError, match="money in"):
            svc.record_bank_txn(s, t1.id, taken.id, D(2026, 4, 1))       # money in is not a payment you made
        with pytest.raises(svc.ServiceError, match="money out"):
            svc.record_bank_txn(s, t3.id, loan.id, D(2026, 4, 1))        # ATM withdrawal is not Ravi paying
        with pytest.raises(svc.ServiceError, match="amounts differ"):
            svc.link_bank_txn(s, t2.id, p1.id)

    def test_bad_files_and_future_lines(self, s):
        _, _, acc = self._setup(s)
        with pytest.raises(svc.ServiceError, match="could not read the statement: could not find the column"):
            svc.import_statement(s, acc.id, b"a,b\n1,2\n", file_name="x.csv", actor="o", as_of=D(2026, 4, 1))
        with pytest.raises(svc.ServiceError, match="in the future"):
            svc.import_statement(s, acc.id, BANK_CSV, file_name="x.csv", actor="o", as_of=D(2026, 2, 6))

    def test_bank_figures_are_protected(self, s):
        _, _, acc = self._setup(s)
        imp, _ = svc.import_statement(s, acc.id, BANK_CSV, file_name="feb.csv", actor="o", as_of=D(2026, 4, 1))
        tid = svc.bank_txns(s, imp.id)[0].id
        for sql, msg in (("UPDATE bank_txn SET amount_paise = 1 WHERE id = :i", "only the decision"),
                         ("DELETE FROM bank_txn WHERE id = :i", "cannot be deleted"),
                         ("UPDATE bank_import SET row_count = 0 WHERE id = :m", "append-only")):
            with pytest.raises(DBAPIError, match=msg):
                with s.begin_nested():
                    s.execute(text(sql), {"i": tid, "m": imp.id})
