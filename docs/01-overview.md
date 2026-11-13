# 01 — Overview, scope, architecture

## Purpose
A personal loan management system for one individual lender in India who both GIVES loans
(receivables) and TAKES loans (payables), across BUSINESS and PERSONAL books.

## Goals (priority order)
1. Correct money maths: every rupee of interest and principal reconciles exactly.
2. Full audit trail: any balance on any past date can be rebuilt and explained line by line.
3. Never miss a payment: timely reminders for interest the owner must pay and must collect.
4. Clear position: business vs personal, given vs taken, net interest income at a glance.
5. Simple to run: one person maintains it, self-hosted, near-zero cost.

## Scope
| Aspect | Requirement |
|---|---|
| Users | One owner. Later: optional second user (partner/collector) who records payments |
| Scale | Tens to a few hundred loans, a few thousand ledger entries per year |
| Direction | GIVEN (asset, they owe owner) / TAKEN (liability, owner owes them) |
| Book | BUSINESS or PERSONAL on every loan; global UI toggle All/Business/Personal |
| Interest | Monthly simple interest ("Rs X per Rs 100 per month") is the main case. EMI and compound optional |
| Locale | INR, Indian number format (1,00,000), financial year April-March, Asia/Kolkata |
| Devices | Phone first (record payments on the go), laptop for reports |
| Out of scope v1 | Borrower login, online payment collection, multi-currency |

## Architecture
One small monolith: one backend container + PostgreSQL + one daily cron job.

```
Phone/laptop (PWA)
      |
Backend service (one container)
  - API + auth (FastAPI, login + TOTP 2FA, book filter)
  - Calculation engine (pure Python, no I/O)
  - Reports + export (statements, FY report, PDF/CSV/Excel)
      |                         |
PostgreSQL (ledger)   <-   Daily job (cron): dues, penalties, statuses, reminder digest
      |                         |
Nightly encrypted backup        Telegram + email to owner
```
The daily job reuses the same engine code as the API, so numbers never disagree.

## Stack
| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | `decimal` for exact money, strong `datetime` |
| API | FastAPI | validation, auto docs, little boilerplate |
| DB | PostgreSQL 16 | transactions, NUMERIC, date functions |
| ORM/migrations | SQLAlchemy 2 + Alembic | standard, explicit |
| Frontend | PWA with HTMX + Jinja | phone-first, low complexity |
| Scheduler | cron (or K8s CronJob) | simple, reliable |
| Notifications | Telegram Bot API + SMTP | free, instant |
| Hosting | Docker Compose on VM/Proxmox behind TLS reverse proxy | cheap, self-controlled |
| Tests | pytest + hypothesis | prove the money maths |

## Non-functional requirements
- Correctness: unit tests for every example in 05-examples.md + property tests
  (allocations sum to payment; principal never negative).
- Consistency: a payment and its allocations are written in one DB transaction.
- Idempotency: daily job can re-run for any date without duplicating dues/penalties/reminders.
- Security: password + TOTP 2FA, HTTPS only, secrets in env, attachments encrypted at rest.
- Backup: nightly encrypted pg_dump offsite; monthly restore test.
- Observability: dashboard shows daily job last-run; alert if not run in 26h.
- Performance: dashboard < 1 s at 500 loans / 20k entries.
- Portability: `docker compose up` runs everything.

Legal note: state Money Lenders Acts (incl. Telangana) may require a licence / cap rates. Not legal advice.
