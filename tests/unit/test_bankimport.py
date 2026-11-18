"""Bank statement parsing and matching (Phase 13). Pure: no database."""
from datetime import date as D

import pytest

from loan_tracker.bankimport import (EntryInfo, LoanInfo, StatementError, fingerprints, parse_statement,
                                     suggest)

SBI = b"""Account Name :,MR A OWNER
Account Number :,00000012345
Statement from 01 Feb 2026 to 28 Feb 2026

Txn Date,Value Date,Description,Ref No./Cheque No.,Debit,Credit,Balance
5 Feb 2026,5 Feb 2026,BY TRANSFER-UPI/CR/603612345/RAVI KUMAR/SBIN/ravi@ok,603612345, ,"1,500.00","26,500.00"
05 Feb 2026,05 Feb 2026,TO TRANSFER-UPI/DR/603699999/SURESH/HDFC/rent,603699999,"1,935.55", ,"24,564.45"
06 Feb 2026,06 Feb 2026,ATM WDL,,500.00,,"24,064.45"
**This is a computer generated statement**
"""

HDFC = b"""Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
05/02/26,UPI-RAVI KUMAR-RAVI@OKAXIS-603612345,0000603612345,05/02/26,,1500.00,26500.00
15/03/26,NEFT CR-MAHESH TRADERS,N12345,15/03/26,,32500.00,59000.00
"""

ICICI = (b"S No.,Value Date,Transaction Date,Cheque Number,Transaction Remarks,"
         b"Withdrawal Amount (INR ),Deposit Amount (INR ),Balance (INR )\n"
         b"1,05/02/2026,05/02/2026 10:22:11,-,UPI/RAVI KUMAR/603612345,0.00,\"1,500.00\",\"26,500.00\"\n")

GENERIC = b"""Date;Description;Amount;Dr/Cr;Balance
2026-02-05;Ravi interest;1500.00;CR;26500.00
2026-02-06;Shop rent;15000;DR;11500.00
"""

SIGNED = b"""Date\tDetails\tAmount
05-02-2026\tRavi\t1500
06-02-2026\tRent\t-15000.50
"""


def test_sbi_layout_with_preamble_and_footer():
    rows = parse_statement(SBI)
    assert [(r.txn_date, r.amount_paise, r.balance_paise, r.reference) for r in rows] == [
        (D(2026, 2, 5), 150_000, 2_650_000, "603612345"),
        (D(2026, 2, 5), -193_555, 2_456_445, "603699999"),
        (D(2026, 2, 6), -50_000, 2_406_445, None)]
    assert "RAVI KUMAR" in rows[0].description


def test_hdfc_two_digit_years():
    rows = parse_statement(HDFC)
    assert [(r.txn_date, r.amount_paise) for r in rows] == [(D(2026, 2, 5), 150_000), (D(2026, 3, 15), 3_250_000)]


def test_icici_prefers_transaction_date_and_drops_time():
    [r] = parse_statement(ICICI)
    assert (r.txn_date, r.amount_paise, r.balance_paise, r.reference) == (D(2026, 2, 5), 150_000, 2_650_000, None)


def test_single_amount_with_dr_cr_and_semicolons():
    rows = parse_statement(GENERIC)
    assert [(r.txn_date, r.amount_paise) for r in rows] == [(D(2026, 2, 5), 150_000), (D(2026, 2, 6), -1_500_000)]


def test_signed_amount_tab_separated():
    rows = parse_statement(SIGNED)
    assert [(r.txn_date, r.amount_paise, r.balance_paise) for r in rows] == [
        (D(2026, 2, 5), 150_000, None), (D(2026, 2, 6), -1_500_050, None)]


def test_day_first_never_month_first():
    rows = parse_statement(b"Date,Narration,Debit,Credit\n03/04/2026,x,,1\n")
    assert rows[0].txn_date == D(2026, 4, 3)


@pytest.mark.parametrize("data, msg", [
    (b"", "empty"),
    (b"hello,world\n1,2\n", "column headings"),
    (b"Date,Narration,Debit,Credit\nnot a date,x,1,\n", "no transactions"),
    (b"Date,Narration,Debit,Credit\n05/02/2026,x,1.005,\n", "line 2"),
])
def test_friendly_errors(data, msg):
    with pytest.raises(StatementError, match=msg):
        parse_statement(data)


def test_fingerprints_stable_and_count_identical_rows():
    data = b"Date,Narration,Debit,Credit\n05/02/2026,TEA,10,\n05/02/2026,TEA,10,\n"
    a, b = fingerprints(parse_statement(data), account_id=1)
    assert a != b and fingerprints(parse_statement(data), account_id=1) == [a, b]
    assert fingerprints(parse_statement(data), account_id=2)[0] != a


LOANS = [LoanInfo(5, "Ravi Kumar", "98480 12345", "GIVEN", due_paise=150_000, next_paise=200_000,
                  payoff_paise=10_150_000),
         LoanInfo(6, "Suresh", None, "TAKEN", due_paise=193_555, next_paise=150_000, payoff_paise=10_193_555),
         LoanInfo(9, "Mahesh Traders", None, "GIVEN", due_paise=0, next_paise=100_000, payoff_paise=3_250_000)]


def test_existing_entry_is_the_best_match():
    entries = [EntryInfo(41, 5, "GIVEN", "PAYMENT", D(2026, 2, 4), 150_000)]
    [best, *rest] = suggest(D(2026, 2, 5), 150_000, "UPI-RAVI KUMAR", LOANS, entries)
    assert (best.kind, best.entry_id, best.loan_id) == ("EXISTING", 41, 5)
    assert "already recorded" in best.reason
    assert rest == []          # no "record payment" next to it: that would count the money twice


def test_name_and_amount_suggest_a_payment():
    [best, *_] = suggest(D(2026, 2, 5), 150_000, "UPI/RAVI KUMAR/603612345", LOANS, [])
    assert (best.kind, best.loan_id, best.strong) == ("NEW_PAYMENT", 5, True)
    assert "ravi" in best.reason.lower() and "interest due" in best.reason


def test_money_out_only_matches_loans_you_took():
    out = suggest(D(2026, 2, 5), -193_555, "TO SURESH", LOANS, [])
    assert [s.loan_id for s in out] == [6]


def test_payoff_amount_and_phone():
    [best, *_] = suggest(D(2026, 3, 15), 3_250_000, "NEFT CR MAHESH TRADERS", LOANS, [])
    assert best.loan_id == 9 and "closes the loan" in best.reason
    [byphone, *_] = suggest(D(2026, 2, 5), 1, "IMPS 9848012345", LOANS, [])
    assert byphone.loan_id == 5 and "phone" in byphone.reason


def test_unrelated_transaction_has_no_suggestion():
    assert suggest(D(2026, 2, 5), -50_000, "ATM WDL", LOANS, []) == []
    assert suggest(D(2026, 2, 5), 123_456, "SALARY ACME", LOANS, []) == []


def test_already_linked_and_far_entries_are_skipped():
    entries = [EntryInfo(41, 5, "GIVEN", "PAYMENT", D(2026, 2, 4), 150_000, linked=True),
               EntryInfo(42, 5, "GIVEN", "PAYMENT", D(2026, 1, 20), 150_000)]
    assert all(s.kind != "EXISTING" for s in suggest(D(2026, 2, 5), 150_000, "RAVI", LOANS, entries))


def test_space_before_quoted_amount():
    rows = parse_statement(b'Date,Narration,Debit,Credit,Balance\n12/11/2026,ATM, "2,000.00", ,"56,000.00"\n')
    assert (rows[0].amount_paise, rows[0].balance_paise) == (-200_000, 5_600_000)


def test_extra_columns_are_refused_not_guessed():
    # an unquoted amount with a comma splits into two cells: refuse instead of reading Rs 2
    with pytest.raises(StatementError, match="line 2 has 5 columns but the headings have 4"):
        parse_statement(b"Date,Narration,Debit,Credit\n12/11/2026,ATM,2,000.00,9\n")


def test_trailing_empty_columns_are_fine():
    rows = parse_statement(b"Date,Narration,Debit,Credit,,\n12/11/2026,ATM,500,,,,\n")
    assert rows[0].amount_paise == -50_000


def test_existing_entry_of_another_person_is_not_a_match():
    """UI review: 'NEFT CR-MAHESH TRADERS Rs 1,000' was offered as Ravi's recorded Rs 1,000 (same amount, date)."""
    entries = [EntryInfo(41, 5, "GIVEN", "PAYMENT", D(2026, 2, 4), 100_000)]       # Ravi's payment, LN-5
    out = suggest(D(2026, 2, 5), 100_000, "NEFT CR-MAHESH TRADERS", LOANS, entries)
    assert all(s.kind != "EXISTING" or not s.strong for s in out)
    assert out[0].kind == "NEW_PAYMENT" and out[0].loan_id == 9                     # Mahesh's own loan comes first
    weak = [s for s in out if s.kind == "EXISTING"]
    assert weak and "names Mahesh Traders" in weak[0].reason


def test_existing_entry_with_no_name_in_narration_is_still_offered():
    entries = [EntryInfo(41, 5, "GIVEN", "PAYMENT", D(2026, 2, 4), 150_000)]
    [best] = suggest(D(2026, 2, 5), 150_000, "UPI 603612345", LOANS, entries)
    assert (best.kind, best.entry_id, best.strong) == ("EXISTING", 41, True)
