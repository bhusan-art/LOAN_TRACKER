# Progress log

## Phase plan
| Phase | Goal | Status |
|---|---|---|
| 0 | Setup: repo, docs, CLAUDE.md, docker-compose (Postgres), venv | done |
| 1 | Design: schema, engine interfaces, API + screen list (docs only, no app code) | done |
| 2 | Calculation engine + tests (Examples A-C, edge cases) | done |
| 3 | DB layer: SQLAlchemy models, Alembic migrations, repositories | not started |
| 4 | API: FastAPI endpoints, auth + 2FA, book filter | not started |
| 5 | Daily job + Telegram/email notifications | not started |
| 6 | Frontend PWA: 6 screens | not started |
| 7 | Deploy: Dockerfile, reverse proxy, backups | not started |

## Log
- Phase 0: setup + docs kit (01-06), CLAUDE.md, docker-compose, venv.
- Phase 1.1: spec review -> docs/plans/phase-1-review.md (10 issues 2.1-2.10:
  stored due state vs P1, reversal semantics, cycle boundaries, rounding).
- Phase 1.2: schema design -> docs/design/schema.sql (enums, tables+CHECKs,
  indexes, append-only trigger on ledger_entry, skeleton due, RATE_CHANGE
  new_rate, allocation as regenerable cache).
- Phase 1.3: engine design -> docs/design/engine.md (dataclasses, function
  signatures, pseudocode, rounding/day-count rules, test list).
- Phase 1.4: DECISIONS.md answered (1-13: paise half-up, no penalties, no
  PRINCIPAL_PREPAY, payoff charges open period, post-maturity accrual).
- Phase 1.5: API design -> docs/design/api.md (endpoint tables with ?book=,
  daily job steps + idempotency rules, 6-screen mapping).
- Phase 1.6: corrections pass — paise fixes (10_000_000 etc.), RUPEE option
  removed, status order (WAIVED before PAID), dues keyed by (loan_id,
  period_start) with no DB input to compute(), DECISIONS 11-13 applied;
  schema: reverses_id on REVERSAL (UNIQUE, NOT NULL check), reminder_log
  keyed by (loan_id, due_date), party delete-only guard; 02/03/04/05
  re-aligned; payoff rule (closing due) added to the waterfall.
- 04.3 patched per review 2.1: dues = immutable skeletons; paid/waived/penalty
  + status are engine outputs (WAIVED-before-PAID order), never stored.

## Log (cont.)
- Phase 2.1-2.2 (Claude Code): types, money, calendar, accrual; WIP when the
  company token budget ran out.
- Phase 2 completion (Claude chat): DECISIONS #15 applied everywhere (short
  first period 103_226; due day 31 = 192_857 then 200_000). Engine rebuilt
  around one date-ordered replay (replay.py): dues, waterfall, payoff closing
  due, reversals, credit, penalties, waivers. status.py (due/loan status,
  balances), funding.py (spread, mismatch), compute()/accrue() in __init__.
  Spec fix: mid-cycle payoff on 15 Apr = 10_693_333 (3 dues realized).
  74 tests incl. hypothesis properties + purity check. Docs 04/05/engine.md updated.

## Next step
Phase 3.1: SQLAlchemy models from docs/design/schema.sql + Alembic.
