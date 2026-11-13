# Progress log

## Phase plan
| Phase | Goal | Status |
|---|---|---|
| 0 | Setup: repo, docs, CLAUDE.md, docker-compose (Postgres), venv | in progress |
| 1 | Design: schema, engine interfaces, API + screen list (docs only, no app code) | in progress |
| 2 | Calculation engine + tests (Examples A-C, edge cases) | not started |
| 3 | DB layer: SQLAlchemy models, Alembic migrations, repositories | not started |
| 4 | API: FastAPI endpoints, auth + 2FA, book filter | not started |
| 5 | Daily job + Telegram/email notifications | not started |
| 6 | Frontend PWA: 6 screens | not started |
| 7 | Deploy: Dockerfile, reverse proxy, backups | not started |

## Log (newest first, max 10 lines per session)
- Phase 0: docs kit added.
- Phase 1.1: spec review done → docs/plans/phase-1-review.md (10 issues: stored due
  state vs P1, reversal semantics, cycle-boundary rules, rounding decision #8).
- Phase 1.2: schema design → docs/design/schema.sql (enums, tables+CHECKs,
  indexes, append-only triggers on ledger_entry/party, skeleton due, RATE_CHANGE
  new_rate, allocation as regenerable cache). DECISIONS.md still open.

## Next step
Owner answers DECISIONS.md (#1, #3, #8, #9, #10 minimum), then patch docs
02/03/04 with review resolutions and design engine interfaces.
