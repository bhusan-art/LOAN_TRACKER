# API design — REST endpoints, daily job, screen mapping

Sources: schema.sql (design), engine.md (pure contract), 03-data-model,
04-logic, 06-features, 01-overview. Money = integer paise in JSON; rates =
strings (Decimal); dates ISO-8601. All read endpoints accept `?book=`
(`all` | `business` | `personal`, default `all`) — book filter per P8.
Auth: session cookie (password + TOTP, 01-overview). Owner-only in v1.

## 1. Endpoints

Every route lists request fields (R) / response fields (Resp). "Derived" means
computed by the engine on read (P1) — never stored.

### Dashboard
| Method | Path | Purpose | R | Resp |
|---|---|---|---|---|
| GET | /api/dashboard | Home metrics + today lists | `book` | totals: `lent_out_paise` (GIVEN principal outstanding), `borrowed_paise` (TAKEN), `own_capital_deployed_paise` (lent − funding-linked), `interest_earned_paise` {month, fy, lifetime} GIVEN, `interest_cost_paise` same periods TAKEN, `net_interest_income_paise`, `collected_vs_pending_paise` GIVEN, `overdue` {count, amount_paise, aging{0_30,31_60,61_90,90_plus}} both directions, `effective_yield_bps`, `idle_cost_paise_per_cycle`, `concentration[]` {party, share_pct, flagged>25}; today: `pay_today[]`, `collect_today[]` {loan_id, due_date, party, amount_paise}; `warnings[]` (04.8 flags) |

### Parties
| Method | Path | Purpose | R | Resp |
|---|---|---|---|---|
| GET | /api/parties | List parties (both sides merged) | `book`, `q` (search) | `[]` {id, name, phone, `net_position_paise` (lent − taken, derived), `loan_count`} |
| GET | /api/parties/{id} | Party detail | `book` | party fields, loans[] {id, direction, book, status, principal_outstanding_paise, net_position with this party, on_time_pct} |

### Loans
| Method | Path | Purpose | R | Resp |
|---|---|---|---|---|
| GET | /api/loans | List loans | `book`, `direction`, `status`, `party_id` | `[]` {id, party_name, direction, book, status, principal_outstanding_paise, payoff_today_paise, next_due_date} |
| GET | /api/loans/{id} | Loan detail | `as_of` (optional date) | terms, status, engine balances {principal_outstanding, interest_accrued, interest_paid, interest_outstanding, penalty_outstanding, credit_paise, payoff_today} all paise, dues[] {(loan_id, due_date) key, period_start, due_date, interest_paise, paid, status (derived), outstanding_paise, overdue_days}, projected_due, funding_links[] {taken_loan_id, amount, cross_book} |
| POST | /api/loans | Create loan (terms only; first DISBURSE entry auto-posted) | `party_id`, `direction`, `book` (required, DECISIONS #7), `principal_paise`, `start_date`, `rate`, `rate_unit`, `interest_type` (SIMPLE in v1), `interest_cycle`, `due_day` (1–31), `grace_days` (default 3), `tenure_months`?, `maturity_date`?, `collateral`?, `purpose`? | loan {id} |
| POST | /api/loans/{id}/fund | Link borrowed → given money | `taken_loan_id`, `given_loan_id`, `amount_paise`, `link_date`, `confirm_cross_book` (true when books differ, DECISIONS #7) | funding_link {id} |

### Ledger (append-only; corrections are REVERSALs — P2)
| Method | Path | Purpose | R | Resp |
|---|---|---|---|---|
| GET | /api/loans/{id}/entries | Ledger, newest first | `as_of`? | `[]` {id, entry_date, type, amount_paise, payment_mode, reference, note, new_rate?, reverses_id} |
| POST | /api/loans/{id}/entries | Record an event: PAYMENT, TOPUP, WAIVER, RATE_CHANGE (no PRINCIPAL_PREPAY, #13 — principal reduction is the PRINCIPAL bucket of a PAYMENT) | `type`, `entry_date` (default today), `amount_paise`?, `payment_mode`?, `reference`?, `note`?, `new_rate`? (RATE_CHANGE) | entry {id} + `allocations[]` {due_period_start (nullable), bucket, amount_paise} (waterfall, P5; written same transaction) |
| POST | /api/entries/{id}/reverse | Post a REVERSAL for entry id (never edit/delete) | `note`? | reversal {id} |

### Dues & calendar
| Method | Path | Purpose | R | Resp |
|---|---|---|---|---|
| GET | /api/dues | Dues list | `book`, `from`, `to`?, `status`? (derived filter), `direction`? | `[]` {(loan_id, due_date) key, party, direction, period_start, due_date, interest_paise, paid_paise, status, overdue_days, outstanding_paise} |
| GET | /api/calendar | Cash calendar 30/60/90 | `book`, `days` (30/60/90) | per day: `in_paise` (GIVEN dues due that day), `out_paise` (TAKEN dues due that day), `running_balance_paise`, `negative` flag |

### Reminders
| Method | Path | Purpose | R | Resp |
|---|---|---|---|---|
| GET | /api/reminders | Today's reminder queue (digest preview) | `book`? | `[]` {(loan_id, due_date), kind (REMINDER/OVERDUE/PAY_ALERT), direction, tag ([BIZ]/[PER]), message} |
| POST | /api/reminders/send | Send now (manual override of the digest) | `reminders[]` {(loan_id, due_date, kind)}?, `channel` (telegram/email) | {sent, failed[]} — logged to reminder_log; dedupe = UNIQUE (loan_id, due_date, kind, channel, sent_on) |

### Reports & export
| Method | Path | Purpose | R | Resp |
|---|---|---|---|---|
| GET | /api/reports/borrower-statement/{loan_id} | Statement PDF/HTML | `from`, `to`, `fmt` (pdf/html/share-link) | document or share link |
| GET | /api/reports/fy-interest | FY interest per book (Apr–Mar) | `fy` (e.g. 2025-26) | {per book: earned_paise, cost_paise, net_paise} |
| GET | /api/reports/overdue | Overdue list with aging | `book`, `min_days`? | `[]` {loan, party, outstanding_paise, days_overdue, aging_bucket} |
| GET | /api/reports/funding | Spread + cross-book + idle cost | `book`? | {links[] {spread_paise_per_cycle, negative, cross_book}, idle_cost_paise_per_cycle} |
| GET | /api/export | Full CSV/Excel | `fmt`, `scope` (loans/entries/dues/all) | file download |

### Job status
| Method | Path | Purpose | R | Resp |
|---|---|---|---|---|
| GET | /api/jobs/status | Daily job last run | — | {last_run_at, ok, failed_steps[]} — dashboard alerts at 26h (01-overview) |

All endpoints above accept `?book=` except party-scoped entry creation and
report exports, where book is embedded (statement of one loan, FY per-book
split returned whole).

## 2. Daily job (cron ~08:00 IST) — steps in order

Each step runs in its own transaction; every step is idempotent for a
re-run on the same date (01-overview NFR).

| # | Step | Idempotency rule |
|---|---|---|
| 1 | Due generation: for every loan (both directions) whose cycle ended on/before today, INSERT skeleton `due` row (review 2.1: interest_paise rounded once at generation, immutable) | `UNIQUE (loan_id, period_start)` — re-run is a no-op; never UPDATE a skeleton |
| 2 | Due cross-check: each skeleton's interest_paise is the engine value at generation (engine.md §3.1); re-generating a missing realized due uses the same formula, so no drift is possible. Read-only check; safe to re-run | Read-only; safe to re-run |
| 3 | Allocation rebuild: for loans whose ledger changed since last rebuild (new/reversed entries), atomically replace their `allocation` rows (P2 does not apply to allocation — regenerable cache) | Replace-in-transaction keyed on max ledger_entry id per loan; re-run produces identical rows (pure engine) |
| 4 | Penalty posting (OFF in v1, DECISIONS #3): if enabled, post at most one PENALTY per (loan, cycle, bucket) | Guard: no PENALTY row exists for that (loan, period_start) — check before insert |
| 5 | Status + lifecycle refresh: compute per-loan derived status (ACTIVE/OVERDUE/…) and update only `loan.status` (P1: never store due status; status is an engine output — loan.status is the maintained exception) | SET-based, deterministic from inputs; re-run writes same values |
| 6 | Reminder digest: build pay → collect → warnings lines, [BIZ]/[PER] tags, from: 3-days-before (REMINDER, keyed by the projected due's (loan_id, due_date) — no `due` row exists yet), due-date (PAY_ALERT, TAKEN flagged on due_date itself — review 2.9), overdue (OVERDUE), maturity-30d, monthly summary (1st) | `reminder_log` UNIQUE (loan_id, due_date, kind, channel, sent_on) — one of a kind per loan + due_date + channel per day; TAKEN payables never muted |
| 7 | Send digest + urgent alerts (Telegram primary, email backup); log every send, retry failures once | Sends gated by step-6 inserts (INSERT … ON CONFLICT DO NOTHING), so re-run sends nothing new |
| 8 | Record run: job_last_run row + observability stamp | Single upsert on run date — later run overwrites, re-run of same date is no-op |

Failed step → transaction rolls back, failure recorded (step 8 still runs
with `ok=false`), next run resumes cleanly because steps 1–4,6,7 are
insert-guarded.

## 3. The 6 screens (06-features)

| # | Screen | Purpose | Data shown | Actions | Endpoints |
|---|---|---|---|---|---|
| 1 | Home | Position at a glance | Dashboard metrics table (06): totals, net interest income, collected vs pending, overdue by aging, yield, idle cost, concentration flags; today's pay/collect lists; warnings | Open a loan/party; view warnings | `GET /api/dashboard`, `GET /api/jobs/status`, drill-down `GET /api/loans/{id}`, `GET /api/parties/{id}` |
| 2 | Cash calendar | Plan cash 30/60/90 | Per-day money in vs out, running balance, negative days highlighted | Switch 30/60/90, switch book | `GET /api/calendar?days=` |
| 3 | Parties | Counterparty view | List + detail: all loans both directions, net position, on-time % | Open party, open loan | `GET /api/parties`, `GET /api/parties/{id}` |
| 4 | Loan detail | One loan's full truth | Terms, ledger (with reversal pairs), dues + derived status, allocations per payment, payoff today, linked funding | Record entry (3-tap quick entry lands here too), link funding, reverse entry, view statement | `GET /api/loans/{id}`, `GET /api/loans/{id}/entries`, `POST /api/loans/{id}/entries`, `POST /api/loans/{id}/fund`, `POST /api/entries/{id}/reverse`, `GET /api/reports/borrower-statement/{id}` |
| 5 | Quick entry | Record a payment in 3 taps | Party → loan → amount (date defaults today; waterfall preview before save) | Save entry | `GET /api/parties?q=`, `GET /api/loans?party_id=`, `POST /api/loans/{id}/entries` |
| 6 | Reports | Documents & exports | Borrower statement (PDF/WhatsApp share), FY interest per book, overdue aging, cross-book funding report, full CSV/Excel | Generate/share/export | `GET /api/reports/*`, `GET /api/export` |

Every screen reads through the book toggle (`?book=` remembered per device,
shown as coloured badge, 03-data-model). No screen mutates state except
actions in screens 4/5 and the manual reminder send (06-features).
