# 04 — Calculation logic

Engine = pure function: `compute(loan_terms, ledger_entries, as_of_date) -> {dues, allocations, balances}`.
Same inputs must always give the same output.

## 4.1 Interest accrual (simple interest)
Split the loan's life into segments. A new segment starts when outstanding principal or rate changes
(DISBURSE, TOPUP, principal allocation, RATE_CHANGE).

    interest_for_period = SUM over segments ( principal_in_segment * daily_rate * days_in_segment )

Day-count convention (per loan):
- Monthly flat ("Rs 2 per Rs 100 per month"): a full cycle earns P * monthly_rate.
  Partial month pro-rated: monthly_rate * days / days_in_that_calendar_month.
- Annual: daily_rate = annual_rate / 365.
- Interest counts from disbursement day; the repayment day belongs to the new (reduced) segment.
- Convention must be documented in code and covered by tests.

Compound (optional): at each cycle end unpaid interest added to principal via CAPITALISE entry.
EMI (optional): standard reducing-balance amortisation schedule.

## 4.2 Payment waterfall
For each PAYMENT in date order:
1. Outstanding penalty, oldest first
2. Interest dues, oldest due first
3. Remainder reduces principal (starts a new accrual segment)
Written as allocation rows. Overpayment beyond full payoff = credit, flagged.

## 4.3 Due generation (daily job)
For every ACTIVE loan whose cycle ended on/before today, create the due row if missing (idempotent).
Status: PAID (paid = interest) / PARTIAL (0 < paid < interest, before due_date + grace)
/ OVERDUE (unpaid after due_date + grace_days) / UPCOMING (future).
Aging buckets: 0-30, 31-60, 61-90, 90+ days.

## 4.4 Penalty (if enabled)
Per loan: flat amount per late cycle, or extra rate on overdue interest.
Posted as PENALTY entry by daily job (visible, waivable).

## 4.5 Lifecycle
ACTIVE -> OVERDUE -> ACTIVE (caught up) -> CLOSED (principal = 0 and interest = 0).
Or SETTLED (closed with WAIVER) / WRITTEN_OFF. Closed loans accept only REVERSAL.

## 4.6 Derived balances (any as-of date)
- Principal outstanding = disbursed + top-ups - principal allocations
- Interest accrued = sum of segment interest to date
- Interest received/paid = sum of interest allocations
- Interest outstanding = accrued - received - waived
- Payoff today = principal + interest outstanding + penalty

## 4.7 Spread on funded loans
For each funding_link: spread = linked_amount * (rate_given - rate_taken).
Negative spread = flagged. Borrowed money not linked to any given loan = "idle cost".

## 4.8 Cash-flow mismatch check
For each TAKEN loan T with next due date D:
- for each linked GIVEN loan G: if G.next_due_date > D -> WARNING "payable before receivable"
- if G has overdue dues -> RISK "T's interest must come from own funds"
- if G is WRITTEN_OFF -> show T's remaining exposure
Advice shown to owner: set borrowers' due days a few days BEFORE own payable due days.
