# 02 — Core design principles (non-negotiable)

| # | Principle | Why |
|---|---|---|
| P1 | Append-only ledger. Store only events; every balance is derived, never stored as editable truth | Rebuild any past balance; explain disputes line by line; one bad entry can't corrupt the rest |
| P2 | Corrections are reversals. Never edit/delete a ledger entry; post REVERSAL + new entry | Full audit trail |
| P3 | Exact money. Integer paise (or NUMERIC(14,2)); never floats. Round only at defined points (per due, half-up to paisa) | Floats drift and won't reconcile |
| P4 | Terms separate from events. `loan` = agreement; `ledger_entry` = what happened | Engine compares expected (dues) vs actual (payments) |
| P5 | Fixed waterfall: penalty -> oldest interest due -> principal | Same payment always splits the same way |
| P6 | One engine for both directions (GIVEN/TAKEN); only cash direction flips | Logic written and tested once |
| P7 | Pure calculation engine: terms + entries + date in, results out, no DB/I/O | Unit-testable against worked examples |
| P8 | Book (BUSINESS/PERSONAL) is a dimension, not a separate system | No duplicated code; per-book and consolidated views free |
| P9 | Daily batch, not real-time | Interest works on whole days; one idempotent job |
