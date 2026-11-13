# 05 — Worked examples (acceptance tests)

## Example A: partial payments and a principal repayment
GIVEN, PERSONAL. Rs 1,00,000 to Ravi on 1 Jan, 2% per month, simple, due on the 1st,
monthly flat with daily pro-rating.

| Date | Entry | Allocation / effect | Principal | Interest pending |
|---|---|---|---|---|
| 1 Jan | DISBURSE 1,00,000 | - | 1,00,000 | 0 |
| 1 Feb | Due generated 2,000 | - | 1,00,000 | 2,000 |
| 5 Feb | PAYMENT 1,500 | 1,500 -> Feb interest | 1,00,000 | 500 |
| 1 Mar | Due generated 2,000 | Feb due OVERDUE (unless within grace) | 1,00,000 | 2,500 |
| 15 Mar | PAYMENT 32,500 | 500 -> Feb due, 2,000 -> Mar due, 30,000 -> principal | 70,000 | 0 |
| 1 Apr | Due generated 1,671 | 1,00,000*2%*14/31 + 70,000*2%*17/31 = 903.23 + 767.74 | 70,000 | 1,671 |

Expected on 1 Apr: accrued = 5,671; received = 4,000; outstanding = 1,671.
(Rounding: 1,670.97 -> 1,671 when rounding the due to whole rupees; to paise it is 1,670.97.
DECISIONS.md fixes which.)

## Example B: borrowed money lent onward
- TAKEN, BUSINESS: Rs 1,00,000 from Suresh, 1.5%/month, due on 5th -> pay Rs 1,500/month
- GIVEN, BUSINESS: same Rs 1,00,000 to Ravi, 2.5%/month, due on 10th -> receive Rs 2,500/month
- funding_link Suresh -> Ravi, Rs 1,00,000
Expected: spread Rs 1,000/month. Mismatch WARNING monthly (pay 5th, receive 10th).
If Ravi overdue: RISK "Rs 1,500 to Suresh due from own funds".

## Example C: cross-book funding
TAKEN, PERSONAL Rs 50,000 from a relative at 1%/month, linked to a GIVEN, BUSINESS loan.
Expected: link allowed, cross-book badge shown, appears in cross-book report.
Personal book shows interest cost; business book shows interest income.

## Edge cases to test
- Leap year (29 Feb)
- due_day = 31 in a 30-day month (and February)
- Payment on the disbursement day
- REVERSAL of an old payment (all later allocations recompute)
- Payment larger than full payoff (credit)
- Rate change mid-cycle
