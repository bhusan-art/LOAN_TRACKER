"""Promise status and WhatsApp reminder text (Phase 9). Pure: no database."""
from datetime import date as D

from loan_tracker.collections import Promise, promise_status, wa_link, wa_phone

P = Promise(made_on=D(2026, 3, 1), promised_date=D(2026, 3, 10), amount_paise=500_000)


def pays(*items):
    return [(D(2026, 3, d), amt) for d, amt in items]


class TestPromiseStatus:
    def test_pending_before_date(self):
        st = promise_status(P, [], D(2026, 3, 5))
        assert (st.status, st.paid_paise, st.short_paise) == ("PENDING", 0, 500_000)

    def test_due_today_is_still_pending(self):
        assert promise_status(P, [], D(2026, 3, 10)).status == "PENDING"

    def test_kept_by_one_payment(self):
        assert promise_status(P, pays((8, 500_000)), D(2026, 3, 20)).status == "KEPT"

    def test_kept_by_several_payments(self):
        st = promise_status(P, pays((2, 200_000), (10, 300_000)), D(2026, 3, 20))
        assert (st.status, st.paid_paise) == ("KEPT", 500_000)

    def test_kept_early_even_before_date(self):
        assert promise_status(P, pays((3, 600_000)), D(2026, 3, 4)).status == "KEPT"

    def test_broken_after_date(self):
        st = promise_status(P, pays((9, 100_000)), D(2026, 3, 11))
        assert (st.status, st.paid_paise, st.short_paise) == ("BROKEN", 100_000, 400_000)

    def test_payment_before_promise_was_made_does_not_count(self):
        assert promise_status(P, [(D(2026, 2, 28), 500_000)], D(2026, 3, 11)).status == "BROKEN"

    def test_payment_on_the_day_promise_made_counts(self):
        assert promise_status(P, pays((1, 500_000)), D(2026, 3, 11)).status == "KEPT"

    def test_kept_late(self):
        st = promise_status(P, pays((9, 100_000), (14, 400_000)), D(2026, 3, 20))
        assert (st.status, st.paid_paise) == ("KEPT_LATE", 100_000)

    def test_late_payment_after_as_of_is_ignored(self):
        assert promise_status(P, pays((14, 500_000)), D(2026, 3, 12)).status == "BROKEN"

    def test_cancelled_wins(self):
        p = Promise(D(2026, 3, 1), D(2026, 3, 10), 500_000, cancelled=True)
        assert promise_status(p, [], D(2026, 4, 1)).status == "CANCELLED"


class TestWhatsApp:
    def test_indian_ten_digit_gets_country_code(self):
        assert wa_phone("98480 12345") == "919848012345"
        assert wa_phone("+91-98480-12345") == "919848012345"
        assert wa_phone("098480 12345") == "919848012345"

    def test_other_formats(self):
        assert wa_phone("") is None and wa_phone(None) is None and wa_phone("12345") is None
        assert wa_phone("+44 7700 900123") == "447700900123"

    def test_link_encodes_text(self):
        url = wa_link("9848012345", "Hi Ravi, ₹1,000 due on 10 Mar & thanks")
        assert url.startswith("https://wa.me/919848012345?text=Hi%20Ravi%2C%20%E2%82%B91%2C000")
        assert "%26" in url
        assert wa_link("", "x") is None


class TestReminderText:
    def test_overdue(self):
        from loan_tracker.collections import reminder_text
        t = reminder_text("Ravi Kumar", 7, overdue_paise=322_581, overdue_since=D(2026, 1, 10))
        assert t == ("Hello Ravi, a reminder that ₹3,225.81 interest on loan LN-7 is pending since 10 Jan 2026. "
                     "Kindly pay at the earliest. Thank you.")

    def test_promise_wins_over_overdue(self):
        from loan_tracker.collections import reminder_text
        t = reminder_text("Ravi", 7, overdue_paise=100, promise_paise=500_000, promise_date=D(2026, 3, 10))
        assert "as discussed, you will pay ₹5,000" in t and "10 Mar 2026" in t

    def test_upcoming(self):
        from loan_tracker.collections import reminder_text
        assert "is due on 10 Feb 2026" in reminder_text("Ravi", 7, next_paise=250_000, next_date=D(2026, 2, 10))
