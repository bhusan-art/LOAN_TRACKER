# 1.3 — Pure calculation engine (design only, no code)

Source docs: 02-principles (P1–P9), 03-data-model, 04-logic, 05-examples,
DECISIONS.md (all answers still blank → this design runs on the DEFAULTS and
marks them pending), review resolutions 2.1–2.10 in docs/plans/phase-1-review.md.

Engine = pure function: terms + entries + as_of in, results out. No DB, no I/O,
no clock, no randomness (P7). Every balance is derived, never stored as truth
(P1). Money = integer paise (`int`); rates = `Decimal`; dates = `datetime.date`.
Rounding happens only at the points defined in §4 (P3).

DECISIONS state this doc assumes (each **pending owner confirmation**):

| # | Default used here |
|---|---|
| 1 | SIMPLE interest only in v1; COMPOUND/EMI dataclasses present, functions raise `NotImplementedError` |
| 2 | Unpaid interest stays pending (no compounding) |
| 3 | Penalty: off in v1 (default); engine support per review 2.7 recommendation, pending #3 |
| 4 | Partial principal repayment any day; interest recalculated from that day |
| 5 | Exact per-loan funding_link; pool view derived later |
| 6 | Owner-only users |
| 7 | New loan: book must be chosen; cross-book link needs explicit confirmation |
| 8 | **OPEN** — default paise, half-up. Example A's expected values are versioned both ways (§5.2) |
| 9 | grace_days = 3 |
| 10 | PER_MONTH loans: monthly flat, partial period pro-rated by actual days in that calendar month |

---

## 1. Dataclasses

All dataclasses are `frozen=True` (immutable, hashable inputs/outputs).

### LoanTerms (per loan; mirrors `loan` table + `loan.direction`/`loan.book`)

```python
@dataclass(frozen=True)
class LoanTerms:
    loan_id: int
    direction: Literal["GIVEN", "TAKEN"]
    book: Literal["BUSINESS", "PERSONAL"]
    principal_paise: int          # initial disbursed principal, paise
    start_date: date              # disbursement day; interest counts from this day (04.1)
    rate: Decimal                 # initial rate, percent (e.g. Decimal("2") = 2%/month)
    rate_unit: Literal["PER_MONTH", "PER_YEAR"]
    interest_type: Literal["SIMPLE", "COMPOUND", "EMI"]   # v1: SIMPLE only (DECISIONS #1)
    interest_cycle: Literal["MONTHLY", "QUARTERLY", "AT_MATURITY"]
    due_day: int                  # 1..31; clamped to month-end at cycle boundaries (§4.2)
    grace_days: int               # default 3 (DECISIONS #9)
    penalty_rule: dict | None     # JSON: {"kind": "flat", "paise": int, "per": "cycle"}; off in v1 (DECISIONS #3)
    maturity_date: date | None    # None = open-ended; at maturity -> MATURED (review 2.10)
```

Note: `loan.rate` is the *initial* rate only; history comes from valid
`RATE_CHANGE` entries (review 2.4, schema: `new_rate` column). v1 `RATE_CHANGE`
changes the value only, never `rate_unit`.

### LedgerEntry (per valid entry; mirrors `ledger_entry`)

```python
@dataclass(frozen=True)
class LedgerEntry:
    entry_id: int
    entry_date: date
    type: Literal["DISBURSE", "TOPUP", "PAYMENT", "PRINCIPAL_PREPAY", "WAIVER",
                  "PENALTY", "RATE_CHANGE", "CAPITALISE", "REVERSAL"]
    amount_paise: int | None      # money types: > 0; RATE_CHANGE/REVERSAL: None
    new_rate: Decimal | None      # RATE_CHANGE only (review 2.4)
    reference: str | None
    reversed_by: int | None       # entry_id of the REVERSAL that cancels this one; None = live
```

Reversal netting (review 2.2): an entry with `reversed_by != None` and its
`REVERSAL` pair are both excluded from the live event list; the `REVERSAL`
itself never carries an effect. P2's append-only rule applies to
`ledger_entry` only — `allocation` rows are a regenerable cache, recomputed
whole from the live event list (§3.2).

### FundingLink (mirrors `funding_link`)

```python
@dataclass(frozen=True)
class FundingLink:
    taken_loan_id: int
    given_loan_id: int
    amount_paise: int
    link_date: date
```

### Due (skeleton; mirrors `due` table — immutable once generated, review 2.1)

```python
@dataclass(frozen=True)
class Due:
    id: int                     # DB-assigned PK; the engine treats it opaquely
    loan_id: int
    period_start: date          # first day the period accrues
    period_end: date           # last day the period accrues (== due_date - 1)
    due_date: date
    interest_paise: int        # rounded at generation (§4.1-a); immutable afterwards
```

`id` is assigned when the job INSERTs the skeleton row (unique `(loan_id,
period_start)` guard, schema); the engine only needs it to build
`Allocation.due_id`. A due that is realized (`due_date <= as_of`) but missing
from `realized` raises `DueInconsistency` — the job must run its insert path
(§3.2 pipeline step) before the allocation rebuild, never the other way
round.

### DueStatus (engine output; NOTHING here is stored)

```python
@dataclass(frozen=True)
class DueStatus:
    due: Due
    paid_paise: int              # sum of INTEREST allocations to this due, live entries
    waived_paise: int            # sum of WAIVER effects on this due
    penalty_paise: int          # PENALTY entries posted for this cycle (0 if off)
    status: Literal["UPCOMING", "PARTIAL", "PAID", "OVERDUE", "WAIVED"]
    outstanding_paise: int       # interest + penalty - paid - waived (>= 0)
    overdue_days: int            # 0 unless status == OVERDUE; days past due_date + grace
```

Status derivation (pins 04.3, adds WAIVED from schema enum):

```
if due_date > as_of:                                status = UPCOMING
elif paid + waived >= interest + penalty:           status = PAID
elif as_of > due_date + grace_days:                 status = OVERDUE
elif waived >= interest + penalty:                   status = WAIVED   (fully waived)
else:                                               status = PARTIAL    (includes 0 paid inside grace)
```

### ProjectedDue (review 2.3 — pure output, no table row)

```python
@dataclass(frozen=True)
class ProjectedDue:
    loan_id: int
    period_start: date
    period_end: date
    due_date: date
    interest_paise: int          # current rate/principal held flat to due_date; informational
```

### Allocation (mirrors `allocation` table; regenerable cache, P2 out of scope)

```python
@dataclass(frozen=True)
class Allocation:
    entry_id: int               # the live PAYMENT / PRINCIPAL_PREPAY entry
    due_id: int | None          # None for PRINCIPAL bucket and credit overflow
    bucket: Literal["PENALTY", "INTEREST", "PRINCIPAL"]
    amount_paise: int           # > 0
```

Credit overflow (review 2.6): the part of a PAYMENT that exceeds full payoff is
**not** an allocation row. It lives in `Balances.credit_paise` (derived,
retrievable by owner as a withdrawal/REVERSAL, no auto-forfeiture). Credit can
only exist when principal = interest = penalty = 0, so it never offsets a
future due — it is always the terminal state of a loan.

### Balances (engine output; mirrors 04.6, any as-of date)

```python
@dataclass(frozen=True)
class Balances:
    loan_id: int
    as_of: date
    principal_outstanding_paise: int   # DISBURSE + TOPUP - principal allocations (4.6)
    interest_accrued_paise: int        # REALIZED basis: sum of rounded due interests with due_date <= as_of (§3.1); flat between due dates
    interest_paid_paise: int           # sum of INTEREST allocations (live entries)
    interest_waived_paise: int
    interest_outstanding_paise: int    # accrued - paid - waived (4.6)
    penalty_outstanding_paise: int
    credit_paise: int
    payoff_today_paise: int            # principal + interest_outstanding + penalty_outstanding - credit, clamped >= 0
```

### SpreadReport / MismatchFlag (§2.6/§2.7)

```python
@dataclass(frozen=True)
class LinkSpread:
    link: FundingLink
    spread_paise_per_cycle: int   # linked_amount * (rate_given - rate_taken), both normalised to PER_MONTH
    negative: bool                # flagged when < 0
    cross_book: bool              # books of the two loans differ (DECISIONS #7 confirmation already given at link time)

@dataclass(frozen=True)
class SpreadReport:
    links: tuple[LinkSpread, ...]
    negative_spread: tuple[LinkSpread, ...]
    idle_cost_paise_per_cycle: int   # unlinked TAKEN principal * its PER_MONTH rate (4.7)

@dataclass(frozen=True)
class MismatchFlag:
    taken_loan_id: int
    given_loan_id: int | None
    kind: Literal["WARNING", "RISK", "EXPOSURE"]
    message: str
```

### EngineResult (output of top-level `compute`)

```python
@dataclass(frozen=True)
class EngineResult:
    loan_id: int
    as_of: date
    dues: tuple[DueStatus, ...]          # realized dues (period_end <= as_of)
    projected_due: ProjectedDue | None   # next cycle, or None when maturity reached
    allocations: tuple[Allocation, ...]  # full live set, rebuilt
    balances: Balances
```

---

## 2. Function signatures

All functions are module-level, pure, and total (no I/O). Public API first,
internal helpers after.

```python
def accrue(terms: LoanTerms, entries: Sequence[LedgerEntry], as_of: date,
           rounding_unit: Literal["PAISE", "RUPEE"] = "PAISE") -> AccrualResult
    """Interest accrual for one loan up to and including as_of (§3.1, §4).

    entries: full ledger for this loan; reversed entries are filtered here.
    Returns every REALIZED period (due_date <= as_of, §3.1), due interests
    rounded once at each generation point (§4.1-a), the projected next due
    (review 2.3, None once maturity is reached), the rounded running interest
    accrued to as_of, and the exact-Decimal segment table (audit aid).
    rounding_unit: DECISIONS #8 (pending); applied at the two §4.1 points.
    COMPOUND/EMI interest_type: NotImplementedError (DECISIONS #1).
    """

@dataclass(frozen=True)
class AccrualResult:
    periods: tuple[Due, ...]              # realized dues; interest_paise rounded at generation (§4.1-a)
    projected_due: ProjectedDue | None   # open tail, informational; None once maturity reached (review 2.10)
    interest_accrued_paise: int          # REALIZED basis: sum of rounded due interests, due_date <= as_of (§3.1)
    segments: tuple[Segment, ...]        # audit aid; see Segment

@dataclass(frozen=True)
class Segment:
    from_day: date
    to_day: date              # inclusive; one segment never spans two calendar months (§3.1)
    principal_paise: int
    rate: Decimal             # effective rate over this segment's days
    days: int
    interest_exact: Decimal  # unrounded paise (exact Decimal); rounded only at a generation point (§4.1)

def generate_dues(terms: LoanTerms, entries: Sequence[LedgerEntry],
                  realized: Sequence[Due], as_of: date,
                  rounding_unit: Literal["PAISE", "RUPEE"] = "PAISE") -> tuple[list[Due], ProjectedDue | None]
    """Daily-job half of due generation (04.3, P9).

    realized: skeleton rows already in the DB for this loan.
    Returns the skeleton dues that are missing (due_date <= as_of, idempotent —
    the unique (loan_id, period_start) guard makes re-runs no-ops) plus the
    projected next due used for the "3 days before" reminder (review 2.3).
    Dues are generated for BOTH directions; TAKEN dues drive pay alerts,
    never muted, flagged on the due_date itself, not only after grace
    (review 2.9). Cross-checks each returned Due's interest_paise against the
    engine value; mismatch with an existing realized row raises
    DueInconsistency (never silent).
    """

def allocate_payment(terms: LoanTerms, entry: LedgerEntry,
                     state: WaterfallState) -> list[Allocation]
    """Single waterfall step (§3.2) for ONE live PAYMENT or PRINCIPAL_PREPAY.

    PAYMENT: penalty -> oldest due -> principal -> credit overflow.
    PRINCIPAL_PREPAY: straight to PRINCIPAL bucket (no waterfall).
    Mutates state (principal, due paid/waived counters, penalties) in place;
    returns the allocation rows to record. One PAYMENT may yield several rows.
    """

@dataclass
class WaterfallState:
    """Mutable running state for a loan while replaying its live entries in date order."""
    principal_paise: int
    credit_paise: int
    due_paid: dict[int, int]            # due_id -> paid paise so far
    due_waived: dict[int, int]
    penalty_outstanding: dict[date, int] # period_start -> posted PENALTY paise not yet covered

def allocate_all(terms: LoanTerms, entries: Sequence[LedgerEntry],
                 realized: Sequence[Due]) -> tuple[tuple[Allocation, ...], WaterfallState]
    """Full rebuild of the allocation cache (review 2.2).

    Replays ALL live money entries in (entry_date, entry_id) order, applying
    due skeletons as they become realized on each day. REVERSAL of an old
    payment is handled by recompute-from-start: the original and its REVERSAL
    are both dropped from the live list, every LATER allocation is regenerated,
    and interest segments from that day forward use the restored principal.
    This is the function the job uses to atomically replace a loan's
    allocation rows when its ledger changes.
    """

def compute_balances(terms: LoanTerms, entries: Sequence[LedgerEntry],
                     realized: Sequence[Due], as_of: date,
                     rounding_unit: Literal["PAISE", "RUPEE"] = "PAISE") -> Balances
    """Derived balances at as_of (04.6). Pure; no state shared with the caller.

    interest_accrued = sum of rounded realized due interests with due_date <=
    as_of (§3.1 — the open tail of the next period is NOT included; it is the
    projected_due in AccrualResult, informational only).
    payoff_today clamps at 0 and nets credit_paise.
    """

def spread(links: Sequence[FundingLink],
          given_terms: Mapping[int, LoanTerms],
          taken_terms: Mapping[int, LoanTerms]) -> SpreadReport
    """Portfolio spread per funding_link (04.7).

    spread_paise_per_cycle = link.amount * (rate_given - rate_taken), both
    rates normalised to PER_MONTH (PER_YEAR / 12). Negative -> flagged.
    Idle cost = unlinked TAKEN principal * its PER_MONTH rate.
    """

def mismatch_check(taken_terms: Sequence[LoanTerms], taken_dues: Sequence[DueStatus],
                   given_terms: Mapping[int, LoanTerms],
                   given_dues: Mapping[int, Sequence[DueStatus]],
                   links: Sequence[FundingLink], as_of: date) -> list[MismatchFlag]
    """Cash-flow mismatch check (04.8). For each TAKEN loan T with next due D:

    - linked GIVEN G with next due date > D  -> WARNING "payable before receivable"
    - linked G with an OVERDUE due           -> RISK "T's interest must come from own funds"
    - linked G WRITTEN_OFF                   -> EXPOSURE showing T's remaining principal
    Static advice text accompanies the flags: set borrowers' due days a few days
    BEFORE own payable due days. TAKEN dues are computed by the same engine
    (P6) — only cash direction flips.
    """

def compute(terms: LoanTerms, entries: Sequence[LedgerEntry], as_of: date,
            realized: Sequence[Due] = (),
            rounding_unit: Literal["PAISE", "RUPEE"] = "PAISE") -> EngineResult
    """Top-level per-loan entry point (P7).

    rounding_unit: DECISIONS #8 (pending; PAISE default). The engine is a
    pure function of it — changing the flag changes stored due values, so it
    must be fixed before the first due is generated and any later change
    regenerates skeleton dues (documented, Phase 3).

    Same inputs always give the same output. Pipeline:
      1. filter live entries (drop reversed originals + their REVERSALs)
      2. accrue()                      -> segments, realized dues, projected due
      3. cross-check realized skeleton vs engine values (DueInconsistency on drift)
      4. allocate_all()                -> allocation rows + terminal WaterfallState
      5. compute_balances()            -> Balances
    Raises EngineError subclasses (not exceptions for money conditions —
    overpayment is credit, not an error; §3.2).
    """
```

Internal helpers (not public API, but pinned here so Phase 2 can't drift):

```python
def cycle_boundaries(terms, up_to: date) -> list[date]
    """Clamped due_day occurrences per §3.1/§4.2."""

def round_half_up(x: Decimal, unit: Literal["PAISE", "RUPEE"]) -> int
    """The ONLY rounding primitive (§4.1): PAISE quantizes to 1 paise,
    RUPEE quantizes to 100 paise, both ROUND_HALF_UP."""

def net_rate(entries) -> list[tuple[date, Decimal]]
    """Valid RATE_CHANGE history: (effective_date, new_rate), date order."""

def net_principal(events_up_to: date) -> int
    """DISBURSE + TOPUP - PRINCIPAL_PREPAY - live principal allocations + reversed-restored amounts."""
```

---

## 3. Pseudocode

### 3.1 Accrual segments

Day-belongs-to rule (pinned by Example A): **a calendar day `d` accrues interest
at the rate and principal in force from `d` onward.** An event dated `d`
(payment, prepay, rate change, top-up) therefore affects interest **from `d`
(inclusive)**. "The repayment day belongs to the new (reduced) segment" (04.1)
means exactly this. The due date `b_k` is the FIRST day of period k+1, so
the period that charges on `b_k` accrues on the days before it:

```
periods = []
boundaries b_1, b_2, ... = cycle_boundaries(terms, as_of)      # §4.2
period_1 = [start_date, b_1 - 1]                                # accrual days, inclusive
for k in 2..n:
    period_k = [b_{k-1}, b_k - 1]
    due_date_k = b_k
    period_k realized iff b_k <= as_of         # 05: "1 Mar: Due generated" — a due is
                                               # realized on its due date itself

segments:                # one segment per (rate, principal, calendar-month) cell
    rate_steps = net_rate(entries)                                # (date, rate)
    principal_steps = principal at each live money event
    for each pair of consecutive events at d_i, d_{i+1} (and start_date / as_of as anchors):
        for each calendar month m overlapping [d_i, min(d_{i+1}-1, as_of)]:
            days = overlap of [d_i, d_{i+1}-1] with month m, inclusive
            interest_exact = P * r_m/100 * days / days_in_month(m)      # PER_MONTH, §4.2
                            = P * r/100 * days / 365                     # PER_YEAR
            append Segment(d_i, d_{i+1}-1, P, r_m, interest_exact)     # EXACT Decimal, no rounding

due interests (generation point):
    for each REALIZED period_k:
        interest_paise = round_half_up(sum(segment interest for segments inside period_k),
                                       rounding_unit)
        -> Due(period_start, period_end = b_k - 1, due_date = b_k, interest_paise)

interest accrued at as_of (REALIZED basis — 05 pins this: "1 Apr: accrued = 5,671",
i.e. only dues already generated; the open tail of the next period is NOT accrued):
    interest_accrued_paise = sum(rounded due interests for periods with due_date <= as_of)
    (jumps only when a due is realized; flat in between due dates)

projected due (review 2.3, the open tail; INFORMATIONAL, not in accrued/payoff):
    next period P* = [b_n, b_{n+1} - 1] with b_{n+1} the next boundary after as_of
    projected interest = current P and rate held flat over P*, rounded the same way
    (if maturity_date is set and <= b_{n+1}: use [b_n, maturity-1] as last period;
     projected_due = None once maturity reached; review 2.10)
    NOTE: closing a loan mid-period (payoff/SETTLED) does NOT charge P*'s open
    tail in v1 (05 convention); an "economic payoff today" that adds it is a
    v2 option (DECISIONS #2 keeps unpaid interest pending, not compounded).
```

`P` is principal outstanding just after the previous event, in paise. A
rate change mid-cycle splits the period into segments at the new rate — no
proration of the rate itself, just a new segment (04.1).

### 3.2 Waterfall

```
allocate_all(terms, live_entries, realized_dues):
    state = WaterfallState(principal = terms.principal_paise ... actually from DISBURSE entry)
    due_index = realized dues ordered by (due_date, period_start)

    replay live money events in (entry_date, entry_id) order:

    DISBURSE / TOPUP:  state.principal += amount          (starts a new accrual segment from entry_date)
    RATE_CHANGE:       no money effect (handled by accrue via net_rate)
    WAIVER:
        if amount is None:            target = oldest unsettled due, waive its full outstanding
        else:                         target = oldest dues, waive up to amount, oldest first
        state.due_waived[target] += waived
    PENALTY:           state.penalty_outstanding[period_start_of(due_date)] += amount
                       (dedup rule, review 2.7: at most one PENALTY per (loan, cycle, rule bucket) —
                        job enforces before insert; engine just sums)
    PRINCIPAL_PREPAY:  apply = min(amount, state.principal)
                       rows += Allocation(entry, None, PRINCIPAL, apply)
                       state.principal -= apply
                       if amount > state.principal before apply: overflow -> credit (§3.2 note)
    PAYMENT:           pool = amount
        1. penalty, oldest cycle first:      take = min(pool, penalty_outstanding[c]); rows += (entry, None, PENALTY, take)
        2. interest dues, oldest due_date first:
           for due in due_index (only those with due_date <= entry_date):
               take = min(pool, due.interest - paid - waived + penalty_of(due))
               rows += (entry, due.id, INTEREST, take); state.due_paid[due] += take_interest_part
        3. principal:  take = min(pool, state.principal)
           rows += (entry, None, PRINCIPAL, take); state.principal -= take
        4. credit:     if pool > 0: state.credit += pool; NO allocation row (review 2.6)
    CAPITALISE:        state.principal += interest_accrued_unpaid (v2 hook; v1 raises)

REVERSAL handling: reversed entries are simply ABSENT from the live list, so a
reversed payment neither pays nor reduces principal from its date forward;
later dues and allocations recompute automatically because the whole replay
starts from scratch. No stored allocation row is ever edited or deleted
(review 2.2): the job replaces the loan's allocation table atomically.

Credit note: payoff complete + surplus -> credit_paise, flagged in UI,
retrievable by owner (REFUND / reverse the PAYMENT). Never forfeited, never
applied to a different loan.
```

**Worked check — Example A, 15 Mar payment of ₹32,500 (3,250,000 p):**
penalty 0 → Feb due outstanding 50,000 p: INTEREST 50,000 p (Feb now PAID);
Mar due 200,000 p: INTEREST 200,000 p (Mar now PAID); pool left 3,000,000 p
→ PRINCIPAL 3,000,000 p; principal 10,000,000 → 7,000,000 p. ✓ matches 05.

**Example A, 5 Feb payment of ₹1,500:** the Feb due (period [1 Jan, 31 Jan],
due 1 Feb) is realized at 5 Feb (period end ≤ as_of). No penalty → INTEREST
150,000 p to the Feb due; remaining pool 0. Feb due now PARTIAL, 50,000 p
outstanding. ✓

---

## 4. Rounding and day-count rules (exact)

### 4.1 Rounding

1. **Exact arithmetic.** All accrual math is `Decimal`, unrounded, in paise
   (rates percent, 4 decimal places). No float anywhere (CLAUDE rule 1).
2. **Only two rounding points per loan:**
   a. **Due generation** — a realized period's interest is rounded ONCE to
      `Due.interest_paise`; the value is immutable (skeleton, review 2.1).
      Waterfall and status math uses rounded due values only.
   b. **In-progress fraction** at `as_of` — display/payoff only, recomputed
      every read, never stored (P1). When the period later generates its due
      in step (a), that rounded value becomes the one stored fact for the
      closed part; the open tail is always re-derived, so a recompute at a
      later date never rewrites the past (§5.3 property test).
3. **The primitive** (`round_half_up`), in paise:
   - `PAISE` (default, DECISIONS #8): quantize to 1 paise, ROUND_HALF_UP.
     Example A Apr due: 1,67,096.77 p → **1,67,097 p**.
   - `RUPEE` (alternative): quantize to 100 paise, ROUND_HALF_UP.
     Example A Apr due: 1,67,096.77 p → **1,67,100 p**.
4. `payoff_today` and all `Balances` fields are exact sums of integer paise
   after the two points above — no further rounding.
5. Spread report (§2.6): round each link's per-cycle spread at its reporting
   point with the TAKEN loan's `rounding_unit`.

### 4.2 Day-count

- **PER_MONTH (DECISIONS #10 default):** a segment is confined to one
  calendar month `m`; segment interest =
  `P × r/100 × days_in_segment / days_in_calendar_month(m)`. A full cycle
  (period spanning whole calendar months) earns `P × r/100` per month.
  Partial periods pro-rate by actual days in that calendar month.
- **PER_YEAR:** `daily_rate = r/365` (04.1): segment interest =
  `P × r/100 × days / 365`, actual calendar days, no month clamping.
- **Cycle boundaries** (review 2.5, pinned): `due_day` is clamped to month
  end in any short month (31 → 30/29/28, `days_in_month(m)`).
  `b_1` = first clamped occurrence STRICTLY AFTER `start_date` (Example A:
  start 1 Jan, due_day 1 → first due 1 Feb; this refines review 2.5's
  "on/after" — the example is authoritative), `b_{k+1}` = next clamped
  occurrence per `interest_cycle` (MONTHLY: each month; QUARTERLY: every
  3rd month from `b_1`; AT_MATURITY: single period ending at `maturity_date`).
- **Period `k` accrues on `[b_{k-1}, b_k - 1]`** inclusive (`b_0 = start_date - 1`);
  `due_date = b_k` is the first day of the NEXT period (events on it accrue
  there at the post-event state, §3.1).
- **First period is short** when `start_date` ≠ clamped due_day: accrual days
  = `b_1 - start_date` (Example A: 31 days = full January).
- **February / leap year:** `days_in_month(Feb) = 28` or `29`; due_day 31 in
  Feb clamps to 28/29; a period spanning 29 Feb pro-rates 29/29 = full month.
- **Maturity (review 2.10):** last boundary = `maturity_date`; no periods
  after it; loan status → `MATURED`; accrued-but-unpaid interest stays
  outstanding (no compounding, DECISIONS #2); CLOSED only when
  `payoff_today = 0`; rollover = new agreement, logged.

---

## 5. Test list (acceptance tests for Phase 2)

All tests run on the paise default; §5.2 states the DECISIONS-#8-versioned
expected values. Amounts in paise unless noted; ₹ = 100 p.

### 5.1 Examples A–C

- **test_example_a** — GIVEN/PERSONAL, ₹1,00,000 on 1 Jan, 2%/month, due_day 1.
  Entries: DISBURSE 1 Jan; PAYMENT 150,000 p on 5 Feb; PAYMENT 3,250,000 p on 15 Mar.
  - Feb due (gen 1 Feb): 200,000 p; 5 Feb payment → INTEREST 150,000 p, Feb PARTIAL.
  - Mar due (gen 1 Mar): 200,000 p; Mar 15 payment: INTEREST 50,000 (Feb) +
    200,000 (Mar), PRINCIPAL 30,000,000 p → principal 7,00,000 p, both dues PAID.
  - Apr due (gen 1 Apr): segments 10,00,000 p×2%×14/31 + 7,00,000 p×2%×17/31
    = 90,322.58 + 76,774.19 = 1,67,096.77 p exact → §5.2.
  - Balances at 1 Apr: accrued 5,67,096.77 p exact (200,000 + 200,000 + 1,67,096.77);
    paid 4,00,000 p; payoff_today = 70,00,000 p principal + outstanding per §5.2.
- **test_example_b** — TAKEN/BUSINESS ₹1,00,000 from Suresh 1.5%/month due 5th;
  GIVEN/BUSINESS same amount to Ravi 2.5%/month due 10th; funding_link ₹1,00,000.
  - spread_paise_per_cycle = 10,00,000 × (2.5−1.5)/100 = **10,000 p** (₹1,000/month).
  - mismatch_check: Ravi's next due (10th) > Suresh's (5th) → **WARNING** every cycle.
  - Ravi due OVERDUE → **RISK** "₹1,500 to Suresh due from own funds".
- **test_example_c** — cross-book: TAKEN/PERSONAL ₹50,000 @1%/month linked to a
  GIVEN/BUSINESS loan. Link allowed (explicit confirmation per DECISIONS #7),
  `LinkSpread.cross_book = True`, appears in cross-book report; PERSONAL book
  shows interest cost, BUSINESS book shows interest income (reporting only —
  engine is one, P6/P8).

### 5.2 DECISIONS #8 versioned expectations (Example A, 1 Apr)

| Field | PAISE (default) | RUPEE |
|---|---|---|
| Apr due `interest_paise` | 1,67,097 | 1,67,100 |
| accrued to 1 Apr | 5,67,097 | 5,67,100 |
| interest outstanding | 1,67,097 | 1,67,100 |
| payoff_today (7,00,000 + outstd) | 71,67,097 | 71,67,100 |

Both columns are the same engine, one flag apart. When DECISIONS #8 is
answered, the chosen column becomes canonical and the other column is dropped.

### 5.3 Edge cases (from 05 + review-pinned)

- **test_leap_year** — period spanning Feb 2024: 29 days, 29/29 = full
  month; due_day 31 clamps to 29 Feb; PER_YEAR variant uses 365-day days.
- **test_due_day_31_clamp** — due_day 31: Jun 30, Aug 31, Feb 28/29; period
  lengths 30/31/28(29) days, pro-rated.
- **test_due_day_31_feb** — first boundary after 31 Jan is Feb 28/29 (clamp),
  not "next valid day of month 2" or skip.
- **test_payment_on_disbursement_day** — PAYMENT dated `start_date`: no dues,
  no penalties → PRINCIPAL bucket; interest accrues on reduced principal from
  day one; if it exceeds full payoff → credit.
- **test_reversal_recomputes_later_allocations** — reverse the 5 Feb Example A
  payment: live list drops original + REVERSAL; the 15 Mar payment now
  allocates INTEREST 2,00,000 p (full Feb due, now unpaid) + 2,00,000 p
  (full Mar due) + PRINCIPAL 10,00,000 p (full principal) → loan paid off
  15 Mar, CREDIT 18,50,000 p (₹18,500) flagged (review 2.6), no further
  dues or projected due (§5.3 zero-principal test), status CLOSED.
  Contrast with the non-reversal run (principal 7,00,000 p, no credit):
  proves "all later allocations recompute" (05) end-to-end; original
  entries untouched (P2), allocation cache regenerated atomically (2.2).
- **test_overpayment_credit** — payment > payoff-today: payoff cleared,
  surplus in `Balances.credit_paise`, no allocation row for it, flagged;
  withdrawable, never forfeited, never cross-loan.
- **test_rate_change_mid_cycle** — GIVEN ₹1,00,000 (10,00,000 p), start 1
  Jan, 2%/month, due_day 1, NO payments; RATE_CHANGE to 3% on 15 Feb.
  Feb period [1 Feb, 28 Feb] splits at 15 Feb (change day accrues at NEW
  rate, §3.1):
    [1 Feb, 14 Feb] = 14 days @2%: 10,00,000 × 2/100 × 14/28 = 10,000 p
    [15 Feb, 28 Feb] = 14 days @3%: 10,00,000 × 3/100 × 14/28 = 15,000 p
    Mar due = **25,000 p** (₹250). Two segments, one per rate cell.
- **test_short_first_period** — start 20 Jan, due_day 5: first boundary
  5 Feb; period [20 Jan, 4 Feb] = 12 days in January + 4 in February,
  interest = P×r/100×(12/31 + 4/28) (two segments, one per calendar month).
- **test_maturity_stops_cycles** (review 2.10) — loan with `maturity_date`:
  no period after it, `projected_due = None`, loan `MATURED`, unpaid
  interest still outstanding, CLOSED only at payoff_today = 0.
- **test_taken_dues_symmetry** (review 2.9) — identical terms/dates flipped to
  TAKEN: same dues/allocation numbers; TAKEN OVERDUE flag surfaces to pay
  alerts, unmuted.
- **test_quarterly_cycle** — boundaries every 3rd month; due = sum of three
  monthly pro-rations (or full `3 × P × r/100` when whole months).
- **test_at_maturity_single_due** — one due at `maturity_date`.
- **test_property_recompute_invariance** (hypothesis, P1) — for random entry
  lists and `d1 < d2`: `compute(..., d1).dues` restricted to due_date <= d1
  equals `compute(..., d2).dues` restricted likewise, and due `interest_paise`
  values are identical; i.e. rebuilding at a later date never changes the
  past.
- **test_zero_principal_no_interest** — fully paid loan: no segments, no
  projected due, `payoff_today = credit_paise` (0 if no credit).

---

## 6. Out of scope here (v2 hooks, deliberately inert)

- COMPOUND (`CAPITALISE` adds accrued interest to principal) and EMI
  (reducing-balance schedule): dataclasses accept, engine functions raise
  `NotImplementedError` (DECISIONS #1).
- Penalty posting stays engine-supported, job flag OFF (DECISIONS #3, review
  2.7 dedup rule in §3.2).
- `REFUND` entry type not yet in schema (review 2.6): credit withdrawal in v1
  is a REVERSAL of the overpaying PAYMENT; add `REFUND` in Phase 3 if the
  owner prefers an explicit type.
- DB/job concerns (idempotent inserts, atomic allocation rebuild, reminders)
  are Phase 3–5; this doc defines only the pure contract they call.

## Open items
- DECISIONS #8 blocks finalising §5.2's canonical column (and Example A's
  headline number). All other decisions this doc assumes are defaults.
- Review 2.5 "on/after" vs Example A: pinned here as "strictly after" (§4.2);
  patch docs/04-logic.md 4.1/4.3 wording with this doc when docs 02/03/04
  are updated.
