# Progress log

## Phase plan
| Phase | Goal | Status |
|---|---|---|
| 0 | Setup: repo, docs, CLAUDE.md, docker-compose (Postgres), venv | done |
| 1 | Design: schema, engine interfaces, API + screen list (docs only, no app code) | done |
| 2 | Calculation engine + tests (Examples A-C, edge cases) | not started |
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

## Next step
Phase 2.1: implement loan_tracker/engine/ (pure, no I/O) per
docs/design/engine.md — dataclasses + round_half_up + cycle_boundaries,
write tests first (05 examples A-C + §5.3 edge cases, pinned 2026 paise
values), run `pytest -q -x --tb=short`.
