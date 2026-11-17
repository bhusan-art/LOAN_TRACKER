# 04 — Calculation logic

Engine = pure function: `compute(loan_terms, ledger_entries, as_of_date) -> {dues, allocations, balances}`.
Same inputs must always give the same output.

## 4.1 Interest accrual (simple interest)
Split the loan's life into segments. A new segment starts when outstanding principal or rate changes
(DISBURSE, TOPUP, a PAYMENT's PRINCIPAL bucket, RATE_CHANGE).

    interest_for_period = SUM over segments ( principal_in_segment * daily_rate * days_in_segment )

Day-count convention (per loan):
- Monthly flat ("Rs 2 per Rs 100 per month", DECISIONS #15): a complete cycle (due date to
  next due date) earns exactly P * monthly_rate (x3 for quarterly), whatever its length.
  A partial cycle (short first period, mid-cycle payoff, principal or rate change inside a
  cycle) is pro-rated: P * monthly_rate * days / days_in_that_cycle.
- Annual: daily_rate = annual_rate / 365.
- Day-belongs-to rule: a calendar day `d` accrues interest at the rate and
  principal in force FROM `d` (inclusive). An event dated `d` (payment, rate
  change, top-up) therefore affects interest from `d` — the repayment day
  belongs to the new (reduced) segment.
- Cycle boundaries: `due_day` clamped to month-end (31 → 28/29/30). First
  boundary = first clamped occurrence STRICTLY AFTER disbursement; a period
  accrues on `[prev_boundary, boundary - 1]`, and its due date is `boundary`.

Rounding is paise, half-up, only at the two defined points: when a due is
generated, and for the open in-progress fraction at an as-of date (DECISIONS #8).

Compound (optional): at each cycle end unpaid interest added to principal via CAPITALISE entry.
EMI (optional): standard reducing-balance amortisation schedule.

## 4.2 Payment waterfall
For each PAYMENT in date order:
1. Outstanding penalty, oldest first
2. Interest dues, oldest due first
3. If the remainder would clear the principal, the open period up to the day before the
   payment is first realized as a closing due and paid (DECISIONS #11)
4. Remainder reduces principal (starts a new accrual segment); any surplus = credit
Written as allocation rows. Overpayment beyond full payoff = credit, flagged.

## 4.3 Due generation (daily job)
Due rows are immutable skeletons (review 2.1): the job creates one per cycle
when it ends, and per mid-cycle payoff (closing due); the unique
(loan_id, period_start) index keeps re-runs idempotent. paid/waived/penalty
paise and status are NEVER stored — the engine derives them on read for any
as-of date (pinned in docs/design/engine.md):

UPCOMING (due_date > as_of) → WAIVED (waived >= interest + penalty) →
PAID (paid + waived >= interest + penalty) → OVERDUE (as_of > due_date +
grace_days) → else PARTIAL (includes 0 paid inside grace). WAIVED is checked
before PAID so it is reachable; overdue_days count starts after grace.
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
