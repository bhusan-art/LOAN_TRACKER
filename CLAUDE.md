# Loan Tracker

Personal loan management system for one lender in India who both GIVES loans (receivables)
and TAKES loans (payables), split into BUSINESS and PERSONAL books. Tracks loans, interest,
payments, dues, overdue amounts, net interest income, and sends reminders.

## Read on demand (do NOT read all docs every session)
- docs/PROGRESS.md      -> what is done, what is next. Read this first every session.
- docs/01-overview.md   -> goals, scope, architecture, stack, non-functional requirements
- docs/02-principles.md -> 9 core design rules (must never be broken)
- docs/03-data-model.md -> tables, fields, business/personal books
- docs/04-logic.md      -> accrual, waterfall, dues, penalty, spread, mismatch formulas
- docs/05-examples.md   -> worked examples = acceptance tests
- docs/06-features.md   -> notifications, dashboard, screens, reports
- docs/DECISIONS.md     -> owner's answers to open questions
- docs/plans/           -> approved plan per phase

## Stack
Python 3.12, FastAPI + Jinja (server-rendered PWA), PostgreSQL 16, SQLAlchemy 2 + Alembic,
pytest + hypothesis, Telegram Bot API / SMTP, Docker Compose (db, app, scheduler).

## Layout
loan_tracker/engine/   pure calculation engine (NO db, NO I/O, NO datetime.now())
loan_tracker/db/       sql/0001_initial.sql (schema source of truth), migrations, models
loan_tracker/services.py  the ONLY writer: appends ledger rows, rebuilds due/allocation caches
loan_tracker/reports.py   dashboard, cash calendar, FY report, CSV
loan_tracker/web/      FastAPI app: HTML pages + /api JSON, templates, static
loan_tracker/jobs/     daily job, notifications
loan_tracker/cli.py    migrate, create-user, run-job, scheduler, demo-data
tests/engine/, tests/db/  (db tests need TEST_DATABASE_URL; see README)

## Non-negotiable money rules
1. Money = integer paise (int). Never float. Rates = Decimal.
2. ledger_entry is append-only. Corrections = REVERSAL entries. Never UPDATE/DELETE entries.
3. Payment waterfall: penalty -> oldest interest due -> principal.
4. Engine is pure: compute(terms, entries, as_of_date) -> results. Same input, same output.
5. Every loan has direction (GIVEN/TAKEN) and book (BUSINESS/PERSONAL). Same engine for both.
6. `due` and `allocation` tables are caches rebuilt by services.recompute_loan(); never edit them by hand.
7. Engine tests' expected numbers are authoritative: fix code, never the expected value.

## Commands
- Tests: `pytest -q -x --tb=short` (always this form, keep output small)
- Single test file: `pytest -q -x --tb=short tests/<file>`

## Working rules
- Do only the task asked. Do not refactor or touch unrelated modules.
- Write/extend tests before implementation for engine code.
- Ask before adding a new dependency.
- At the end of every task: update docs/PROGRESS.md (max 10 lines added), then stop.

# Compact instructions
When compacting, keep: current task, files changed, failing tests, decisions made. Drop file dumps.
