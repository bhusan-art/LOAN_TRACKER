# Phase 1 review — spec issues (pre-design)

Review of docs 01–06 + DECISIONS.md, before any design work. No code written.

## 1. System in 5 lines

1. A single Indian lender tracks loans he GIVES (receivables) and TAKES (payables),
   each tagged BUSINESS or PERSONAL, needing exact interest accounting and a
   line-by-line rebuildable audit trail for any balance on any past date.
2. Data is an append-only ledger (`ledger_entry`); every balance, due status and
   allocation is DERIVED by a pure engine `compute(terms, entries, as_of_date)`
   — never stored as editable truth.
3. An idempotent daily cron job generates per-cycle interest dues, marks overdue,
   posts optional penalties, and flags cash-flow mismatches where borrowed money's
   interest is due before the linked loan's interest arrives.
4. One engine serves both directions and both books; fixed waterfall:
   penalty → oldest interest due → principal; money is integer paise, rounded
   only at defined points.
5. A phone-first PWA records payments in a few taps; dashboard shows net interest
   income, overdue aging and funding risk; a daily digest + urgent alerts arrive
   via Telegram/email; deploys as one container + PostgreSQL behind TLS.

## 2. Contradictions, gaps, ambiguities (most important first)

### 2.1 Stored `due` state vs P1 "every balance is derived"  [contradiction]
`due` rows store `paid_paise` and `status` (03), but a payment arriving after the
due was generated changes both — and 04.3 only says "create the due row if
missing". Nobody is defined as updating it. Storing mutable state contradicts
P1/P2 and gives two sources of truth (stored vs engine-derived).
**Resolution:** make `due` an immutable skeleton: `period_start, period_end,
due_date, interest_paise` only, created once by the daily job. `paid_paise`,
`waived_paise`, `penalty_paise` and `status` are engine outputs, computed on
read for any as-of date; keep them in the table only as a regenerable cache if
performance needs it, documented as disposable. Fix 03's schema + status
derivation rules in 04.

### 2.2 REVERSAL of an old payment "all later allocations recompute" vs append-only  [contradiction]
05's edge case requires recomputing all later allocations after reversing an old
payment, but P2 + CLAUDE.md say never UPDATE/DELETE entries, and `allocation`
rows are stored.
**Resolution:** P2's append-only rule applies to `ledger_entry` only.
`allocation` rows are a derived cache: the engine recomputes the full
allocation set from the entry list (with reversal pairs netted out) and the
job/transaction replaces the loan's allocation cache atomically. State this
explicitly in 02 (amend P2's scope) and 03.

### 2.3 Future due projection missing  [gap]
04.3 generates dues only for cycles that have *ended* ("on/before today"). But
the "3 days before due" reminder (06) and the cash calendar (screen 2) need the
due 3 days *before* it exists.
**Resolution:** distinguish two concepts: *projected due* (pure: next cycle
end + interest for the upcoming period from current segments — engine output,
no table) and *realized due* (skeleton row, 2.1). Job logic: if any projected
due date is within N days of today → schedule the reminder. Document in 04.

### 2.4 `RATE_CHANGE` entry has no rate payload  [inconsistency]
`ledger_entry` only carries `amount_paise` and `reference`; a rate change is not
a paise amount, and 03 says `loan` is "changed only via adjustment entries" —
so the sync rule between `loan.rate` (current) and history-in-entries is
undefined.
**Resolution:** `RATE_CHANGE` stores the new rate in a structured `reference`
(e.g. `rate=0.02;unit=PER_MONTH`) or add optional `new_rate` column to
`ledger_entry`; `loan.rate` = latest RATE_CHANGE value, engine reads history
from entries (mid-cycle rate change already handled by 4.1 segmentation).

### 2.5 Cycle-boundary and day-count rules under-specified  [ambiguity]
Open questions that 05's edge cases expose: (a) `due_day = 31` in a 30-day
month or February — clamp to month end? (b) first cycle when disbursement day ≠
`due_day` (shorter first period)? (c) "monthly flat" — is the period
calendar-month aligned or a rolling anniversary of the last due? (d) last cycle
at maturity.
**Resolution:** pin one convention in 04: cycle boundaries = `due_day` clamped
to month-end (31 → last day of Feb etc.); period = (prev boundary, boundary],
exclusive-inclusive; first boundary = next occurrence of `due_day` on/after
disbursement; final boundary = `maturity_date`. Partial-period interest =
`P * monthly_rate * days_in_period / days_in_calendar_month` (per DECISIONS #10),
rate changes mid-cycle split the period into segments. Add these exact rules +
the 05 edge cases as acceptance tests before engine code.

### 2.6 Overpayment / credit undefined  [gap]
04.2 says "overpayment beyond full payoff = credit, flagged" — but where the
credit lives, whether it offsets the next cycle's due, and whether the owner
can withdraw it, is undefined (same for the "payment on the disbursement day"
edge).
**Resolution:** per-loan credit balance (derived): later PAYMENTs cover it as
part of the normal waterfall after all dues+principal are zero (or equivalently,
next due is satisfied from credit first, oldest credit first). Document that
credit is retrievable by owner as a withdrawal entry (type `REFUND` or reverse
the PAYMENT); no auto-forfeiture.

### 2.7 Penalty posting breaks job idempotency  [contradiction]
04.4: PENALTY is appended to the ledger by the daily job, but 01 demands the job
"can re-run for any date without duplicating".
**Resolution:** uniqueness rule — at most one PENALTY entry per (loan, cycle,
late-day-bucket as defined in `penalty_rule`); the job checks before insert.
Also DECISIONS #3 default is "none in v1": decide whether `penalty_rule`
schema + engine support ships in v1 (recommend: schema + engine, flag off).

### 2.8 Rounding unit is an open DECISIONS item blocking example A  [blocker]
02 P3 says "per due, half-up to paisa"; DECISIONS #8 default is paise, but
example A's acceptance line ("1,670.97 → 1,671 when rounding to whole rupees")
assumes whole rupees. Engine tests cannot be finalised until answered.
**Resolution:** owner must answer DECISIONS #8 before Phase 2. Interim:
parameterise the engine on rounding unit (paise default, whole-rupee option),
and make example A's expected values versioned per choice.

### 2.9 Dues/reminders for the TAKEN (pay) side not explicit  [gap]
04.3 says "for every ACTIVE loan" but the pay side (interest the owner owes)
is where urgent alerts come from, and 06 says overdue payables reminders
cannot be muted. Dues for TAKEN loans should be stated explicitly so no one
assumes collect-only.
**Resolution:** state in 04: "dues and reminders run for BOTH directions;
TAKEN dues drive pay alerts, which are never muted and are flagged
immediately on due date, not just after grace."

### 2.10 Maturity-time behaviour undefined  [gap]
Nothing defines what happens at `maturity_date`: does the loan auto-CLOSE, does
interest keep accruing (DECISIONS #2 says unpaid stays pending, but what
drives it post-maturity?), can it be rolled over?
**Resolution:** define: at maturity the loan moves to a `MATURED`/due-in-full
state; interest stops accruing on new cycles (no new dues); accrued-but-unpaid
interest remains outstanding (no compounding, per #2); a `ROLLOVER` adjustment
extends maturity (new agreement, logged). CLOSED only when payoff-today = 0.

## 3. Before Phase 2 can start
- Owner answers DECISIONS.md, at minimum #1, #8, #9, #10 (and #3/#5 confirm).
- Docs 02/03/04 patched with resolutions 2.1, 2.2, 2.4, 2.5.
- 05 extended with the pinned cycle-boundary edge cases from 2.5 as acceptance tests.
