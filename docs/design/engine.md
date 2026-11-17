# 1.3 — Pure calculation engine (design only, no code)

Source docs: 02-principles (P1–P9), 03-data-model, 04-logic, 05-examples,
DECISIONS.md (all 13 answered → this design runs on the decided values),
review resolutions 2.1–2.10 in docs/plans/phase-1-review.md.

Engine = pure function: terms + entries + as_of in, results out. No DB, no I/O,
no clock, no randomness (P7). Every balance is derived, never stored as truth
(P1). Money = integer paise (`int`, underscore literals in this doc); rates =
`Decimal`; dates = `datetime.date`. Rounding is paise, half-up, at the two
defined points (§4.1) — a single fixed unit (DECISIONS #8, no RUPEE option).

Decisions this doc runs on (all answered in DECISIONS.md):

| # | Value |
|---|---|
| 1 | SIMPLE only; COMPOUND/EMI dataclasses present, functions raise `NotImplementedError` |
| 2 | Unpaid interest stays pending (no compounding) |
| 3 | Penalty off in v1 (schema + engine present, job flag off) |
| 4 | Partial principal repayment any day; interest recalculated from that day |
| 5 | Exact per-loan `funding_link`; pool view derived later |
| 6 | Owner-only users |
| 7 | New loan: book chosen at create; cross-book link needs explicit confirmation |
| 8 | Paise, half-up. Single unit — no whole-rupee option |
| 9 | grace_days = 3 |
| 10 | PER_MONTH: monthly flat, partial period pro-rated by actual days in that calendar month |
| 11 | Mid-cycle payoff: charge interest up to (not including) the repayment day; payoff_today includes the open partial period |
| 12 | After maturity: interest keeps accruing monthly on unpaid principal until repaid; status MATURED + overdue flags |
| 13 | No `PRINCIPAL_PREPAY` entry type; every payment goes through the waterfall (penalty → interest → principal) |

| 14 | QUARTERLY: first due on/after start + 3 months, then every 3 months |
| 15 | A complete cycle earns exactly P × r × flat months; only partial cycles pro-rate by days / days in that cycle |

All tests below pin the year **2026**.

> **Implementation notes (Phase 2, supersede the text below where it differs).**
> The code lives in `loan_tracker/engine/` and the tests in `tests/engine/` are the
> authoritative acceptance values.
> - **Day count (#15):** a period's interest is Σ P·r/100·months·days/cycle_days, where
>   the cycle is the regular cycle ending at the period's due date
>   (`calendar.cycle_frame`). Segments split only at rate/principal events and
>   boundaries, never at calendar-month edges. A short first period uses the notional
>   previous due day (start 20 Jan, due day 5 → 16 of 31 days = **103_226 p**).
> - **One replay (`replay.py`)** realizes dues, runs the waterfall and tracks principal
>   in a single date-ordered pass; `accrue()` has the §2 signature again.
>   Same-day entries: DISBURSE/TOPUP first, then by entry_id.
> - **Payoff rule:** when a payment, after penalties and realized dues, would clear the
>   principal, the open period [last boundary, payment day − 1] is first realized as a
>   closing due and paid; any principal left keeps accruing from the payment day.
> - **Mid-cycle payoff on 15 Apr:** three dues are realized (1 Feb, 1 Mar, 1 Apr = 600_000),
>   so payoff = **10_693_333 p** (the 10_493_333 / 400_000 figures below are superseded).
> - Zero-interest periods create no due. Entries dated after as_of are ignored.
> - `compute()` also returns `loan_status` (ACTIVE / OVERDUE / MATURED / CLOSED).
> - `spread(links, terms, taken_principal=None)` and
>   `mismatch_check(links, results, written_off=frozenset())` take engine results,
>   a simpler form than the §2 signatures.

---

## 1. Dataclasses

All dataclasses are `frozen=True` (immutable, hashable inputs/outputs).

### LoanTerms (per loan; mirrors `loan` + `loan.direction`/`loan.book`)

```python
@dataclass(frozen=True)
class LoanTerms:
    loan_id: int
    direction: Literal["GIVEN", "TAKEN"]
    book: Literal["BUSINESS", "PERSONAL"]
    principal_paise: int          # initial disbursed principal, paise
    start_date: date             # disbursement day; interest counts from this day (04.1)
    rate: Decimal                # initial rate, percent (Decimal("2") = 2%/month)
    rate_unit: Literal["PER_MONTH", "PER_YEAR"]
    interest_type: Literal["SIMPLE", "COMPOUND", "EMI"]   # v1: SIMPLE only (#1)
    interest_cycle: Literal["MONTHLY", "QUARTERLY", "AT_MATURITY"]
    due_day: int                 # 1..31; clamped to month-end at cycle boundaries (§4.2)
    grace_days: int              # default 3 (#9)
    penalty_rule: dict | None    # JSON {"kind","paise","per"}; off in v1 (#3)
    maturity_date: date | None   # None = open-ended; at maturity -> MATURED (#12), accrual continues
```

Note: `loan.rate` is the *initial* rate only; history comes from valid
`RATE_CHANGE` entries (review 2.4, schema `new_rate` column). v1 `RATE_CHANGE`
changes the value only, never `rate_unit`.

### LedgerEntry (per live entry; mirrors `ledger_entry`)

```python
@dataclass(frozen=True)
class LedgerEntry:
    entry_id: int
    entry_date: date
    type: Literal["DISBURSE", "TOPUP", "PAYMENT", "WAIVER",
                  "PENALTY", "RATE_CHANGE", "CAPITALISE", "REVERSAL"]
    amount_paise: int | None      # money types: > 0; RATE_CHANGE/REVERSAL: None
    new_rate: Decimal | None      # RATE_CHANGE only (review 2.4)
    reference: str | None
    reverses: int | None          # on a REVERSAL row: the entry_id it reverses; None otherwise (#13)
```

There is no `PRINCIPAL_PREPAY` (#13): principal reduction only happens as the
PRINCIPAL bucket of a `PAYMENT` through the waterfall. Reversal netting
(review 2.2): an entry `e` with a live REVERSAL row `r` where `r.reverses ==
e.entry_id` — both `e` and `r` are dropped from the live event list; the
REVERSAL itself carries no money effect. P2's append-only rule applies to
`ledger_entry` only — `allocation` rows are a regenerable cache, recomputed
whole from the live list (§3.2).

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

A due is identified by **`(loan_id, period_start)`**, not a DB id. The engine
computes the full realized set from terms + entries + as_of (no DB input); the
daily job INSERTs the skeleton rows with `ON CONFLICT (loan_id, period_start)
DO NOTHING`, so the DB id is assigned at insert time and is opaque to the engine.

```python
@dataclass(frozen=True)
class Due:
    loan_id: int
    period_start: date          # identity key with loan_id (== due_date - period_days, see §4.2)
    period_end: date           # last day the period accrues (== due_date - 1)
    due_date: date
    interest_paise: int        # rounded at generation (§4.1-a); immutable afterwards
```

No `DueInconsistency` in the engine: realized dues are derived, so there is no
stored skeleton to drift against (review 2.1).

### DueStatus (engine output; NOTHING here is stored)

```python
@dataclass(frozen=True)
class DueStatus:
    due: Due
    paid_paise: int            # sum of INTEREST allocations to this due, live entries
    waived_paise: int         # sum of WAIVER effects on this due
    penalty_paise: int        # PENALTY entries posted for this cycle (0 if off)
    status: Literal["UPCOMING", "PARTIAL", "PAID", "OVERDUE", "WAIVED"]
    outstanding_paise: int     # interest + penalty - paid - waived (>= 0)
    overdue_days: int         # 0 unless status == OVERDUE; days past due_date + grace
```

Status derivation (pins 04.3 + schema enum; **WAIVED checked before PAID so it
is reachable**):

```
if due_date > as_of:                       status = UPCOMING
elif waived >= interest + penalty:         status = WAIVED   (fully waived)
elif paid + waived >= interest + penalty:  status = PAID
elif as_of > due_date + grace_days:       status = OVERDUE
else:                                      status = PARTIAL   (includes 0 paid inside grace)
```

### ProjectedDue (review 2.3 — pure output, no table row)

```python
@dataclass(frozen=True)
class ProjectedDue:
    loan_id: int
    period_start: date
    period_end: date
    due_date: date
    interest_paise: int        # current rate/principal held flat to due_date; informational
```

### Allocation (mirrors `allocation` table; regenerable cache)

```python
@dataclass(frozen=True)
class Allocation:
    entry_id: int             # the live PAYMENT entry
    due_period_start: date | None   # references a due by period_start (None for PRINCIPAL bucket & credit)
    bucket: Literal["PENALTY", "INTEREST", "PRINCIPAL"]
    amount_paise: int         # > 0
```

Credit overflow (review 2.6): the part of a PAYMENT that exceeds full payoff is
**not** an allocation row. It lives in `Balances.credit_paise` (derived,
retrievable by the owner via a REVERSAL of the overpaying PAYMENT, no
auto-forfeiture). Credit only exists when principal = interest = penalty = 0,
so it never offsets a future due — it is the terminal state of a loan.

### Balances (engine output; mirrors 04.6, any as-of date)

```python
@dataclass(frozen=True)
class Balances:
    loan_id: int
    as_of: date
    principal_outstanding_paise: int   # DISBURSE + TOPUP - PRINCIPAL allocations (4.6)
    interest_accrued_paise: int        # REALIZED basis: sum of rounded due interests, due_date <= as_of (§3.1)
    interest_paid_paise: int           # sum of INTEREST allocations (live entries)
    interest_waived_paise: int
    interest_outstanding_paise: int    # accrued - paid - waived (4.6)
    open_period_paise: int             # in-progress interest for [last boundary, as_of - 1] (#11); 0 when on a boundary or principal = 0
    penalty_outstanding_paise: int
    credit_paise: int
    payoff_today_paise: int            # principal + interest_outstanding + open_period + penalty - credit, clamped >= 0 (#11)
```

`payoff_today_paise` includes the open partial period (#11): charging interest
up to (not including) the repayment day. `open_period_paise` is 0 when `as_of`
is a due boundary or when principal is fully repaid.

### SpreadReport / MismatchFlag (§2.6/§2.7)

```python
@dataclass(frozen=True)
class LinkSpread:
    link: FundingLink
    spread_paise_per_cycle: int   # linked_amount * (rate_given - rate_taken), both normalised to PER_MONTH
    negative: bool                # flagged when < 0
    cross_book: bool              # books of the two loans differ (#7 confirmation given at link time)

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
    dues: tuple[DueStatus, ...]          # realized dues (due_date <= as_of), keyed by (loan_id, period_start)
    projected_due: ProjectedDue | None   # next cycle; None only when principal fully repaid (#12: NOT at maturity)
    allocations: tuple[Allocation, ...]  # full live set, rebuilt
    balances: Balances
```

---

## 2. Function signatures

All functions are module-level, pure, and total (no I/O). No `rounding_unit`
parameter (single paise unit, #8) and no `realized` input (the engine derives
realized dues, no DB input, fix #4).

```python
def accrue(terms: LoanTerms, entries: Sequence[LedgerEntry], as_of: date) -> AccrualResult
    """Interest accrual for one loan up to and including as_of (§3.1, §4).

    Reversed entries are filtered here. Returns every REALIZED period
    (due_date <= as_of), due interests rounded once at each generation point
    (§4.1-a), the projected next due (review 2.3; None only when principal is
    fully repaid — NOT at maturity, #12), and the exact-Decimal segment table.
    COMPOUND/EMI interest_type: NotImplementedError (#1).
    """

@dataclass(frozen=True)
class AccrualResult:
    periods: tuple[Due, ...]              # realized dues; interest_paise rounded at generation (§4.1-a)
    projected_due: ProjectedDue | None
    interest_accrued_paise: int           # REALIZED basis (due_date <= as_of)
    open_period_paise: int                # in-progress tail for payoff (#11)
    segments: tuple[Segment, ...]        # audit aid; see Segment

@dataclass(frozen=True)
class Segment:
    from_day: date
    to_day: date              # inclusive; one segment never spans two calendar months (§3.1)
    principal_paise: int
    rate: Decimal             # effective rate over this segment's days
    days: int
    interest_exact: Decimal   # unrounded paise (exact Decimal); rounded only at a generation point (§4.1)

def generate_dues(terms: LoanTerms, entries: Sequence[LedgerEntry],
                  as_of: date) -> tuple[list[Due], ProjectedDue | None]
    """Daily-job half of due generation (04.3, P9).

    Computes the FULL realized set (due_date <= as_of) keyed by
    (loan_id, period_start); no `realized` input — the job's idempotency is the
    unique index, not a comparison against stored rows. Also returns the
    projected next due for the "3 days before" reminder (review 2.3). Dues are
    generated for BOTH directions; TAKEN dues drive pay alerts, never muted,
    flagged on the due_date itself, not only after grace (review 2.9).
    """

def allocate_payment(terms: LoanTerms, entry: LedgerEntry,
                     state: WaterfallState) -> list[Allocation]
    """Single waterfall step (§3.2) for ONE live PAYMENT.

    PAYMENT: penalty -> oldest due -> principal -> credit overflow. No
    PRINCIPAL_PREPAY exists (#13) — principal reduction is always the
    PRINCIPAL bucket of a PAYMENT. Mutates state in place; returns the
    allocation rows to record (due rows referenced by period_start).
    One PAYMENT may yield several rows.
    """

@dataclass
class WaterfallState:
    """Mutable running state for a loan while replaying its live entries in date order."""
    principal_paise: int
    credit_paise: int
    due_paid: dict[datetime.date, int]    # period_start -> paid paise so far
    due_waived: dict[datetime.date, int]
    penalty_outstanding: dict[datetime.date, int]  # period_start -> posted PENALTY paise not yet covered

def allocate_all(terms: LoanTerms, entries: Sequence[LedgerEntry],
                 as_of: date) -> tuple[tuple[Allocation, ...], WaterfallState]
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
                     as_of: date) -> Balances
    """Derived balances at as_of (04.6). Pure; no shared state.

    interest_accrued = sum of rounded realized due interests with due_date <=
    as_of (REALIZED basis). open_period = the in-progress tail for payoff
    (#11). payoff_today = principal + interest_outstanding + open_period +
    penalty_outstanding - credit, clamped >= 0.
    """

def spread(links: Sequence[FundingLink],
          given_terms: Mapping[int, LoanTerms],
          taken_terms: Mapping[int, LoanTerms]) -> SpreadReport
    """Portfolio spread per funding_link (04.7).

    spread_paise_per_cycle = link.amount * (rate_given - rate_taken), both
    rates normalised to PER_MONTH (PER_YEAR / 12). Negative -> flagged.
    Idle cost = unlinked TAKEN principal * its PER_MONTH rate. Rounded
    half-up to paise at the reporting point.
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

def compute(terms: LoanTerms, entries: Sequence[LedgerEntry], as_of: date) -> EngineResult
    """Top-level per-loan entry point (P7). Works with no DB input: realized
    dues are derived internally, keyed by (loan_id, period_start) (fix #4).

    Same inputs always give the same output. Pipeline:
      1. filter live entries (drop reversed originals + their REVERSALs)
      2. accrue()          -> segments, realized dues, projected due, open period
      3. allocate_all()    -> allocation rows + terminal WaterfallState
      4. compute_balances() -> Balances
    Raises EngineError subclasses (not for money conditions — overpayment is
    credit, not an error; §3.2). No DueInconsistency step.
    """
```

Internal helpers (not public API, but pinned here so Phase 2 can't drift):

```python
def cycle_boundaries(terms, up_to: date) -> list[date]
    """Clamped due_day occurrences per §3.1/§4.2."""

def round_half_up(x: Decimal) -> int
    """The ONLY rounding primitive (§4.1): quantize to 1 paise, ROUND_HALF_UP.
    Single unit (DECISIONS #8) — there is no whole-rupee option."""

def net_rate(entries) -> list[tuple[date, Decimal]]
    """Valid RATE_CHANGE history: (effective_date, new_rate), date order."""

def net_principal(events_up_to: date) -> int
    """DISBURSE + TOPUP - live PRINCIPAL allocations (no PRINCIPAL_PREPAY, #13)
    + reversed-restored amounts."""
```

---

## 3. Pseudocode

### 3.1 Accrual segments

Day-belongs-to rule (pinned by Example A): **a calendar day `d` accrues interest
at the rate and principal in force from `d` onward.** An event dated `d`
(payment, rate change, top-up) therefore affects interest **from `d`
(inclusive)**. "The repayment day belongs to the new (reduced) segment" (04.1)
means exactly this. The due date `b_k` is the FIRST day of period k+1, so the
period that charges on `b_k` accrues on the days before it:

```
periods = []
boundaries b_1, b_2, ... = cycle_boundaries(terms, as_of)      # §4.2
period_1 = [start_date, b_1 - 1]                                # accrual days, inclusive
for k = 2..n:
    period_k = [b_{k-1}, b_k - 1]
    due_date_k = b_k
    period_k realized iff b_k <= as_of         # 05: a due is realized on its due date itself

segments:                # one segment per (rate, principal, calendar-month) cell
    rate_steps = net_rate(entries)
    principal_steps = principal at each live money event
    for each pair of consecutive events at d_i, d_{i+1} (and start_date / as_of as anchors):
        for each calendar month m overlapping [d_i, min(d_{i+1}-1, as_of)]:
            days = overlap of [d_i, d_{i+1}-1] with month m, inclusive
            interest_exact = P * r_m/100 * days / days_in_month(m)      # PER_MONTH, §4.2
                            = P * r/100 * days / 365                     # PER_YEAR
            append Segment(d_i, d_{i+1}-1, P, r_m, interest_exact)      # EXACT Decimal, no rounding

due interests (generation point):
    for each REALIZED period_k:
        interest_paise = round_half_up(sum(segment interest inside period_k))
        -> Due(period_start, period_end = b_k - 1, due_date = b_k, interest_paise)

interest accrued at as_of (REALIZED basis — 05 pins this; only dues generated):
    interest_accrued_paise = sum(rounded due interests for periods with due_date <= as_of)
    (jumps only when a due is realized; flat between due dates)

open period (#11 — the in-progress tail, for payoff/display only, never stored):
    next boundary b_{n+1}; open period = [b_n, as_of - 1]
    open_period_paise = round_half_up(sum(segment interest over [b_n, as_of - 1] at current P & rate))
    open_period_paise = 0 when as_of is a boundary OR principal is fully repaid
    if the loan is paid off mid-period (PAYMENT >= payoff_today on its entry_date),
    the open period is realized as a CLOSING DUE with due_date = entry_date
    (payoff rule, §3.2); no dues are generated after principal reaches 0

projected due (review 2.3, the open tail; INFORMATIONAL, not in accrued/payoff):
    next period P* = [b_n, b_{n+1} - 1] with b_{n+1} the next boundary after as_of
    projected interest = current P and rate held flat over P*, rounded the same way
    Maturity (#12): maturity_date does NOT cap cycles — projected_due keeps
    generating monthly on the unpaid principal; the loan is flagged MATURED and
    unpaid matured dues get OVERDUE flags. projected_due = None only when the
    principal is fully repaid (not at maturity).
```

`P` is principal outstanding just after the previous event, in paise. A rate
change mid-cycle splits the period into segments at the new rate — no proration
of the rate itself, just a new segment (04.1).

### 3.2 Waterfall

```
allocate_all(terms, live_entries, as_of):
    state = WaterfallState(principal from the DISBURSE entry)
    due_index = realized dues ordered by (due_date, period_start)

    replay live money events in (entry_date, entry_id) order:

    DISBURSE / TOPUP:  state.principal += amount          (starts a new accrual segment from entry_date)
    RATE_CHANGE:       no money effect (handled by accrue via net_rate)
    WAIVER:
        if amount is None:            target = oldest unsettled due, waive its full outstanding
        else:                         target = oldest dues, waive up to amount, oldest first
        state.due_waived[target_period_start] += waived
    PENALTY:           state.penalty_outstanding[period_start_of(due_date)] += amount
                       (dedup rule, review 2.7: at most one PENALTY per (loan, cycle, rule bucket) —
                        job enforces before insert; engine just sums)
    PAYMENT:           pool = amount    (no PRINCIPAL_PREPAY type, #13)
        0. payoff rule: if pool >= payoff_today(entry_date), first realize a
           CLOSING DUE for [b_n, entry_date - 1] (due_date = entry_date) at the
           open-period amount on that date; it joins due_index as the newest
           due. No dues are generated after principal reaches 0.
        1. penalty, oldest cycle first:      take = min(pool, penalty_outstanding[c]); rows += (entry, None, PENALTY, take)
        2. interest dues, oldest due_date first (includes the closing due when realized):
           for due in due_index (only those with due_date <= entry_date):
               take = min(pool, due.interest - paid - waived + penalty_of(due))
               rows += (entry, due.period_start, INTEREST, take); state.due_paid[due.period_start] += take_interest_part
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
retrievable by the owner (REVERSAL of the overpaying PAYMENT). Never
forfeited, never applied to a different loan.
```

**Worked check — Example A, 15 Mar 2026 payment of 3_250_000 p (₹32,500):**
penalty 0 → Feb due outstanding 50_000 p: INTEREST 50_000 p (Feb now PAID);
Mar due 200_000 p: INTEREST 200_000 p (Mar now PAID); pool left
3_250_000 − 250_000 = 3_000_000 p → PRINCIPAL 3_000_000 p; principal
10_000_000 → 7_000_000 p. ✓ matches 05.

**Example A, 5 Feb 2026 payment of 150_000 p (₹1,500):** the Feb due
(period [1 Jan, 31 Jan], due 1 Feb) is realized at 5 Feb. No penalty →
INTEREST 150_000 p to the Feb due; pool 0. Feb due now PARTIAL, 50_000 p
outstanding. ✓

**Payoff rule worked check — mid-cycle payoff on 15 Apr 2026 (§5.3):**
pool 10_693_333 p >= payoff_today → a closing due [1 Apr, 14 Apr]
(due_date 15 Apr) at 93_333 p is realized first; waterfall: INTEREST
200_000 × 3 (Feb, Mar, Apr) + 93_333 (closing due), PRINCIPAL 10_000_000,
pool 0, credit 0. No dues after principal = 0; loan CLOSED.

---

## 4. Rounding and day-count rules (exact)

### 4.1 Rounding

1. **Exact arithmetic.** All accrual math is `Decimal`, unrounded, in paise
   (rates percent, 4 decimal places). No float anywhere (CLAUDE rule 1).
2. **Only two rounding points per loan:**
   a. **Due generation** — a realized period's interest is rounded ONCE to
      `Due.interest_paise`; the value is immutable (skeleton, review 2.1).
      Waterfall and status math use rounded due values only.
   b. **Open period at `as_of`** — the in-progress fraction for payoff/display
      (#11), recomputed every read, never stored (P1). When the period later
      generates its due in step (a), that rounded value becomes the one stored
      fact for the closed part; the open tail is always re-derived, so a
      recompute at a later date never rewrites the past (§5.3 property test).
3. **The primitive** (`round_half_up`), in paise — a single unit, no
   whole-rupee option (#8): quantize to 1 paise, ROUND_HALF_UP.
   Example A Apr due: 167_096.77 p → **167_097 p**.
4. `payoff_today` and all `Balances` fields are exact sums of integer paise
   after the two points above — no further rounding.
5. Spread report (§2.6): round each link's per-cycle spread half-up to paise
   at its reporting point.

### 4.2 Day-count

- **PER_MONTH (#10 default):** a segment is confined to one calendar month `m`;
  segment interest = `P × r/100 × days_in_segment / days_in_calendar_month(m)`.
  A full cycle (period spanning whole calendar months) earns `P × r/100` per
  month. Partial periods pro-rate by actual days in that calendar month.
- **PER_YEAR:** `daily_rate = r/365` (04.1): segment interest =
  `P × r/100 × days / 365`, actual calendar days, no month clamping.
- **Cycle boundaries (review 2.5, pinned):** `due_day` is clamped to month end
  in any short month (31 → 30/29/28, `days_in_month(m)`). `b_1` = first clamped
  occurrence STRICTLY AFTER `start_date` (Example A: start 1 Jan, due_day 1 →
  first due 1 Feb; this refines review 2.5's "on/after" — the example is
  authoritative), `b_{k+1}` = next clamped occurrence per `interest_cycle`
  (MONTHLY: each month; QUARTERLY: `b_1` = first clamped occurrence on/after start_date + 3 months, then every 3rd month (DECISIONS #14); AT_MATURITY: a
  single lump due at `maturity_date`, then open-ended monthly accrual on the
  unpaid balance per #12).
- **Period `k` accrues on `[b_{k-1}, b_k - 1]`** inclusive (`b_0 = start_date - 1`);
  `due_date = b_k` is the first day of the NEXT period (events on it accrue
  there at the post-event state, §3.1).
- **First period is short** when `start_date` ≠ clamped due_day: accrual days
  = `b_1 - start_date` (Example A: 31 days = full January).
- **February / leap year:** `days_in_month(Feb) = 28` or `29`; due_day 31 in
  Feb clamps to 28/29; a period spanning 29 Feb pro-rates 29/29 = full month.
- **Maturity (#12, overrides review 2.10 "no periods after it"):** at
  `maturity_date` the loan status → `MATURED`, but **interest keeps accruing
  monthly on the unpaid principal until it is repaid** (no new-cycle cap).
  Unpaid matured dues get `OVERDUE` flags after grace. CLOSED only when
  `payoff_today = 0`; rollover = a new agreement, logged. Accrued-but-unpaid
  interest does not compound (#2).

---

## 5. Test list (acceptance tests for Phase 2)

All tests pin the year **2026** and use paise (underscore literals); ₹ = 100 p.
Single rounding unit = paise, half-up (#8).

### 5.1 Examples A–C

- **test_example_a** — GIVEN/PERSONAL, 10_000_000 p (₹1,00,000) on 1 Jan 2026,
  2%/month, due_day 1.
  Entries: DISBURSE 1 Jan; PAYMENT 150_000 p on 5 Feb; PAYMENT 3_250_000 p on 15 Mar.
  - Feb due (gen 1 Feb): 200_000 p; 5 Feb payment → INTEREST 150_000 p, Feb PARTIAL.
  - Mar due (gen 1 Mar): 200_000 p; 15 Mar payment: INTEREST 50_000 (Feb) +
    200_000 (Mar) = 250_000, PRINCIPAL 3_000_000 → principal 7_000_000 p, both dues PAID.
  - Apr due (gen 1 Apr): [1 Mar–14 Mar] 10_000_000×2%×14/31 = 90_322.58 p +
    [15 Mar–31 Mar] 7_000_000×2%×17/31 = 76_774.19 p → 167_096.77 → **167_097 p**.
  - Balances at 1 Apr: accrued = 200_000 + 200_000 + 167_097 = **567_097 p**;
    paid = 400_000 p; outstanding = **167_097 p**; open period 0 (on the boundary).
- **test_example_b** — TAKEN/BUSINESS 10_000_000 p from Suresh, 1.5%/month,
  due 5th; GIVEN/BUSINESS 10_000_000 p to Ravi, 2.5%/month, due 10th;
  funding_link 10_000_000 p.
  - spread_paise_per_cycle = 10_000_000 × (2.5−1.5)/100 = **100_000 p/month** (₹1,000).
  - mismatch_check: Ravi's next due (10th) > Suresh's (5th) → **WARNING** every cycle.
  - Ravi due OVERDUE → **RISK** "₹1,500 to Suresh due from own funds".
- **test_example_c** — cross-book: TAKEN/PERSONAL 5_000_000 p (₹50,000) @1%/month
  linked to a GIVEN/BUSINESS loan. Link allowed (explicit confirmation per #7),
  `LinkSpread.cross_book = True`, appears in cross-book report; PERSONAL book
  shows interest cost, BUSINESS book shows interest income (reporting only —
  engine is one, P6/P8).

### 5.2 Example A canonical values, 1 Apr 2026 (paise, half-up #8)

| Field | paise |
|---|---|
| Apr due `interest_paise` | 167_097 |
| accrued to 1 Apr | 567_097 |
| interest outstanding | 167_097 |
| payoff_today (7_000_000 principal + 167_097 outstd + 0 open) | 7_167_097 |

(Single canonical column — the whole-rupee alternative was dropped with #8.)

### 5.3 Edge cases (from 05 + review-pinned, all 2026)

- **test_leap_year** — period spanning Feb 2024: 29 days, 29/29 = full
  month; due_day 31 clamps to 29 Feb; PER_YEAR variant uses 365-day days.
- **test_due_day_31_clamp** — due_day 31: Jun 30, Aug 31, Feb 28/29; period
  lengths 30/31/28(29) days, pro-rated.
- **test_due_day_31_feb** — first boundary after 31 Jan is Feb 28/29 (clamp),
  not "next valid day of month 2" or skip.
- **test_payment_on_disbursement_day** — a PAYMENT dated `start_date`: no
  dues, no penalties → PRINCIPAL bucket; interest accrues on reduced
  principal from day one; if it exceeds full payoff → credit. (No
  PRINCIPAL_PREPAY type, #13 — it is a PAYMENT through the waterfall.)
- **test_reversal_recomputes_later_allocations** — reverse the 5 Feb 2026
  payment (Example A): the live list drops original + REVERSAL; the 15 Mar
  payment now allocates INTEREST 200_000 (full Feb due, now unpaid) +
  200_000 (full Mar due) = 400_000, PRINCIPAL 3_250_000 − 400_000 =
  **2_850_000** → principal 10_000_000 − 2_850_000 = **7_150_000 p, NO
  credit** (loan not paid off). Apr due = 90_322.58 + 78_419.35 =
  **168_742 p**. Contrast with the non-reversal run (principal 7_000_000 p,
  Apr due 167_097): proves "all later allocations recompute" (05) end-to-end;
  original entries untouched (P2), allocation cache regenerated atomically (2.2).
- **test_overpayment_credit** — PAYMENT > payoff-today: payoff cleared,
  surplus in `Balances.credit_paise`, no allocation row for it, flagged;
  withdrawable, never forfeited, never cross-loan.
- **test_rate_change_mid_cycle** — GIVEN 10_000_000 p, start 1 Jan 2026,
  2%/month, due_day 1, NO payments; RATE_CHANGE to 3% on 15 Feb.
  Feb period [1 Feb, 28 Feb] splits at 15 Feb (change day accrues at NEW
  rate, §3.1):
    [1 Feb, 14 Feb] = 14 days @2%: 10_000_000 × 2/100 × 14/28 = 100_000 p
    [15 Feb, 28 Feb] = 14 days @3%: 10_000_000 × 3/100 × 14/28 = 150_000 p
    Mar due = **250_000 p** (₹2,500). Two segments, one per rate cell.
- **test_short_first_period** — GIVEN 10_000_000 p (₹1,00,000), 2%/month,
  start **20 Jan 2026**, due_day 5: first boundary 5 Feb; period
  [20 Jan, 4 Feb] = 16 days of the 31-day cycle [5 Jan, 4 Feb] (#15):
  200_000 × 16/31 = 103_225.81 → **103_226 p** (due 5 Feb 2026).
- **test_mid_cycle_payoff** (#11) — GIVEN 10_000_000 p on 1 Jan 2026,
  2%/month, due_day 1, NO payments; owner pays off on **15 Apr 2026**.
  Realized dues: 1 Feb + 1 Mar + 1 Apr = 600_000. Open period
  [1 Apr, 14 Apr] = 14 days at 10_000_000: 200_000 × 14/30 = 93_333.33 →
  **93_333 p**. `payoff_today` = 10_000_000 principal + 600_000 realized
  outstd + 93_333 open + 0 = **10_693_333 p** (₹1,06,933.33). A PAYMENT of
  exactly that: payoff rule realizes a closing due (93_333 p) first, then
  allocations = INTEREST 200_000 × 3 + 93_333 (closing),
  PRINCIPAL 10_000_000, pool 0, credit 0. No dues after principal = 0;
  status **CLOSED**.
- **test_post_maturity_accrual** (#12) — GIVEN 10_000_000 p on 1 Jan 2026,
  2%/month, due_day 1, **maturity_date 1 May**, NO payments. Cycles keep
  generating after maturity: Feb–May dues each 200_000 p, and a post-maturity
  Jun due [1 May, 31 May] = 200_000 p. At **1 Jun**: accrued = 5 × 200_000 =
  **1_000_000 p**; loan status **MATURED**; the unpaid 1 Jun due →
  **OVERDUE** after grace. Proves interest continues past maturity until
  repaid (not capped, no compounding).
- **test_taken_dues_symmetry** (review 2.9) — identical terms/dates flipped to
  TAKEN: same dues/allocation numbers; TAKEN OVERDUE flag surfaces to pay
  alerts, unmuted.
- **test_quarterly_cycle** (#14) — GIVEN 10_000_000 p on 1 Jan 2026, 2%/month,
  due_day 1, QUARTERLY, NO payments. Boundaries: b_1 = 1 Apr, then 1 Jul, 1 Oct.
  Dues: 1 Apr = [1 Jan, 31 Mar] = **600_000 p**; 1 Jul = [1 Apr, 30 Jun] =
  **600_000 p**. accrued to 1 Jul 2026 = **1_200_000 p**.
- **test_at_maturity_single_due** — GIVEN 10_000_000 p on 1 Jan 2026,
  2%/month, due_day 1, AT_MATURITY, maturity_date **1 Apr 2026**, NO
  payments. Single lump due [1 Jan, 31 Mar] = 200_000 × 3 =
  **600_000 p** due 1 Apr; status MATURED. Afterwards (#12): monthly dues
  continue on the unpaid balance — 1 May due = **200_000 p**, etc.
- **test_property_recompute_invariance** (hypothesis, P1) — for random entry
  lists and `d1 < d2`: `compute(..., d1).dues` restricted to due_date <= d1
  equals `compute(..., d2).dues` restricted likewise, and due `interest_paise`
  values are identical; i.e. rebuilding at a later date never changes the past.
- **test_zero_principal_no_interest** — fully paid loan: no segments, no
  projected due, `open_period = 0`, `payoff_today = credit_paise` (0 if no credit).

---

## 6. Out of scope here (v2 hooks, deliberately inert)

- COMPOUND (`CAPITALISE` adds accrued interest to principal) and EMI
  (reducing-balance schedule): dataclasses accept, engine functions raise
  `NotImplementedError` (#1).
- Penalty posting stays engine-supported, job flag OFF (#3, review 2.7 dedup
  rule in §3.2).
- `REFUND` entry type not in schema (review 2.6): credit withdrawal in v1 is a
  REVERSAL of the overpaying PAYMENT; add `REFUND` in Phase 3 if the owner
  prefers an explicit type.
- DB/job concerns (idempotent inserts, atomic allocation rebuild, reminders)
  are Phase 3–5; this doc defines only the pure contract they call.

## Open items
- DECISIONS #8 is answered (paise, half-up, single unit): §5.2's canonical
  column is final; the whole-rupee column and the `rounding_unit` parameter are
  dropped. All other decisions are answered and pinned in the table above.
- Review 2.5 "on/after" vs Example A: pinned here as "strictly after" (§4.2);
  04-logic.md 4.1/4.3 wording carries this.
