"""Receipt helpers (Phase 10). Pure: no database."""

import pytest

from loan_tracker.receipts import amount_in_words, receipt_number


@pytest.mark.parametrize("paise, words", [
    (0, "Rupees Zero Only"),
    (100, "Rupees One Only"),
    (5, "Rupees Zero and Five Paise Only"),
    (1_150, "Rupees Eleven and Fifty Paise Only"),
    (2_100, "Rupees Twenty-One Only"),
    (10_000, "Rupees One Hundred Only"),
    (322_581, "Rupees Three Thousand Two Hundred Twenty-Five and Eighty-One Paise Only"),
    (10_000_000, "Rupees One Lakh Only"),
    (12_345_678_950, "Rupees Twelve Crore Thirty-Four Lakh Fifty-Six Thousand Seven Hundred Eighty-Nine "
                     "and Fifty Paise Only"),
    (10**13, "Rupees Ten Thousand Crore Only"),
    (10**14, "Rupees One Lakh Crore Only"),
])
def test_amount_in_words(paise, words):
    assert amount_in_words(paise) == words


def test_negative_rejected():
    with pytest.raises(ValueError):
        amount_in_words(-1)


def test_receipt_number():
    assert receipt_number(2026, 42) == "RCPT-2026-0042"
    assert receipt_number(2027, 12345) == "RCPT-2027-12345"
