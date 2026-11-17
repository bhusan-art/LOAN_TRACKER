# 05 — Worked examples (acceptance tests)

All values in paise (underscore literals, no Indian commas); ₹ = 100 p;
all dates 2026. Rounding: paise, half-up (DECISIONS #8). Engine detail and
the full edge-case test list: docs/design/engine.md §5.

## Example A: partial payments and a principal repayment
GIVEN, PERSONAL. 10_000_000 p (₹1,00,000) to Ravi on 1 Jan, 2% per month,
simple, due on the 1st, monthly flat with daily pro-rating.

| Date | Entry | Allocation / effect | Principal | Interest pending |
|---|---|---|---|---|
| 1 Jan | DISBURSE 10_000_000 | - | 10_000_000 | 0 |
| 1 Feb | Due generated 200_000 | - | 10_000_000 | 200_000 |
| 5 Feb | PAYMENT 150_000 | 150_000 -> Feb interest | 10_000_000 | 50_000 |
| 1 Mar | Due generated 200_000 | Feb due OVERDUE (unless within grace) | 10_000_000 | 250_000 |
| 15 Mar | PAYMENT 3_250_000 | INTEREST 50_000 (Feb) + 200_000 (Mar), PRINCIPAL 3_000_000 | 7_000_000 | 0 |
| 1 Apr | Due generated 167_097 | [1 Mar–14 Mar] 10_000_000*2%*14/31 + [15 Mar–31 Mar] 7_000_000*2%*17/31 = 167_096.77 -> 167_097 | 7_000_000 | 167_097 |

Expected on 1 Apr: accrued = 567_097; received = 400_000; outstanding =
167_097; payoff_today = 7_167_097 (principal 7_000_000 + 167_097, open
period 0 on the boundary).

## Example B: borrowed money lent onward
- TAKEN, BUSINESS: 10_000_000 p from Suresh, 1.5%/month, due 5th -> pay 150_000 p/month
- GIVEN, BUSINESS: 10_000_000 p to Ravi, 2.5%/month, due 10th -> receive 250_000 p/month
- funding_link Suresh -> Ravi, 10_000_000 p
Expected: spread = 10_000_000 × (2.5−1.5)/100 = 100_000 p/month (₹1,000).
Mismatch WARNING monthly (pay 5th, receive 10th).
If Ravi overdue: RISK "₹1,500 to Suresh due from own funds".

## Example C: cross-book funding
TAKEN, PERSONAL 5_000_000 p (₹50,000) from a relative at 1%/month, linked
to a GIVEN, BUSINESS loan.
Expected: link allowed, cross-book badge shown, appears in cross-book
report. Personal book shows interest cost; business book shows interest
income.

## DECISIONS #11 / #12 acceptance values (engine.md §5.3)
- Mid-cycle payoff (#11): GIVEN 10_000_000 p on 1 Jan, 2%/month, due 1st,
  no payments; payoff PAYMENT on 15 Apr of 10_693_333 p (principal
  10_000_000 + realized 600_000 (1 Feb, 1 Mar, 1 Apr) + closing due 93_333
  for [1 Apr, 14 Apr]). Allocations: INTEREST 200_000 x3 + 93_333 (closing due),
  PRINCIPAL 10_000_000, credit 0, status CLOSED; no dues after.
- Post-maturity accrual (#12): same loan with maturity_date 1 May; cycles
  keep generating after maturity (Jun due 200_000 p), status MATURED,
  unpaid matured dues OVERDUE after grace; no cap, no compounding.

## Edge cases to test (full list + exact values: engine.md §5.3)
- Leap year (29 Feb)
- due_day = 31 in a 30-day month (and February)
- Short first period (start 20 Jan, due 5th -> 103_226 p due 5 Feb: 16 of 31 days, DECISIONS #15)
- Payment on the disbursement day (a PAYMENT; no PRINCIPAL_PREPAY type, #13)
- REVERSAL of an old payment (15 Mar -> INTEREST 200_000 + 200_000,
  PRINCIPAL 2_850_000, principal 7_150_000, Apr due 168_742)
- Payment larger than full payoff (credit, withdrawable, never forfeited)
- Rate change mid-cycle (Mar due 250_000 p)
- Quarterly cycle (1 Apr and 1 Jul dues 600_000 p each) and AT_MATURITY single due (1 Apr due 600_000 p, then monthly)
