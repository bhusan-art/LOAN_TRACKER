# Progress log

## Phase plan
| Phase | Goal | Status |
|---|---|---|
| 0 | Setup: repo, docs, CLAUDE.md, docker-compose (Postgres), venv | done |
| 1 | Design: schema, engine interfaces, API + screen list (docs only, no app code) | done |
| 2 | Calculation engine + tests (Examples A-C, edge cases) | done |
| 3 | DB layer: SQLAlchemy models, Alembic migrations, repositories | done |
| 4 | API: FastAPI endpoints, auth + 2FA, book filter | done |
| 5 | Daily job + Telegram/email notifications | done |
| 6 | Frontend PWA: 6 screens | done |
| 7 | Deploy: Dockerfile, reverse proxy, backups | done |

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

- Phase 3: migrations (Alembic running db/sql/0001_initial.sql; + app_user, job_run),
  ORM models, services.py (append-only writes, cache rebuild in the same transaction),
  reports.py. Design change: due/allocation are caches (a reversal can change later dues).
- Phase 4-6: FastAPI app with HTML pages + /api JSON; scrypt passwords + TOTP 2FA
  (stdlib); daily job with grouped digest, never-muted payables, idempotent re-runs;
  phone layout checked with screenshots. tzdata added (Windows has no tz database).
- Phase 7: Dockerfile, compose (db, app, scheduler), backup.sh/.ps1, Caddyfile, README.
  100 tests (74 engine + 26 database/web/job).

- Phase 8 (V2 foundation): funding links count only what is still outstanding on
  both loans (engine.link_exposure; spread, idle cost, own capital deployed).
  Typed JSON API bodies (pydantic, via FastAPI): integer paise, enums, readable 400.
  Migration 0002 audit_log (append-only trigger): logins, loans, entries, reversals,
  status, parties, funding, CLI users; /activity page + per-loan history. 121 tests.

- Phase 9 (collections): migration 0003 collection_activity (append-only) and
  payment_promise (cancel-only). collections.py (pure): promise status from live
  payments, wa.me links, reminder text. Collections page, loan-page contact form +
  WhatsApp button, /api contacts/promises, FOLLOW UP digest section. 147 tests.

- Review (after 9): API docs behind login + test that every route needs login;
  loan row lock (FOR UPDATE) on post/reverse/status; lint clean.
- Phase 10 (receipts): migration 0004 receipt (snapshot, cancel-only) + atomic
  per-year counter RCPT-YYYY-NNNN. Auto-issued per PAYMENT on given loans,
  cancelled on reversal, backfill for older payments. Printable receipt (amount in
  words, WhatsApp share), receipts list, no-dues certificate (CLOSED/SETTLED).
  Issuer from RECEIPT_NAME / RECEIPT_BUSINESS_NAME / RECEIPT_DETAILS. 172 tests.

- Phase 11 (cash): migration 0005 cash_account, cash_balance (append-only),
  planned_cash_item (cancel-only). cashflow.forecast (pure); reports.cash_forecast
  (expected + cautious without late borrowers), shortfall/stale warnings on the
  dashboard and digest, CASH_MIN_BALANCE floor. /cash page with chart, accounts,
  plans; calendar starts from the real balance. rupees_to_paise rejects Infinity/NaN.
  Sidebar scrolls. 193 tests.

- Phase 12 (what-if): simulator.py (pure) runs the engine on copies (negative-id
  entries, nothing saved); interest assumed paid on each due date in both paths;
  extra payment, payoff, rate change, missed months, top-up; new-loan cash flows.
  /simulate (GET only) with actual vs what-if, dues, funding-link spread; new-loan
  mode shows cash forecast before/after. JSON /api/simulate/*. render() no longer
  overwrites a page's own error. 211 tests.

- Phase 13 (bank import): bankimport.py (pure) parses common Indian bank CSVs
  (skipinitialspace; refuses rows wider than the headings rather than guess),
  fingerprints for duplicate skip, suggestions with reasons (an already-recorded
  match hides new-payment offers). Migration 0006 bank_import (append-only),
  bank_txn (bank figures locked). Link/record/ignore/undo, link-all-exact, balance
  from statement. /bank pages, /api/bank/*. All /api POSTs must be JSON (CSRF). 241 tests.

- Evaluation after 13: 150-loan/2,565-entry book, every page < 0.5 s, daily job
  1.2 s. Fixed one-day interest periods (migration 0007: due start <= end; loan
  starting the day before its due day, payoff the day after a due date).
  tests/db/test_fuzz.py: random loans and steps through services + real DB. 244 tests.

- Phase 14 (documents): cryptography added (owner approved). docvault.py AES-256-GCM,
  nonce per file, bound to document uuid, key id header, DOCUMENT_KEY_OLD for
  rotation, type by content (PDF/JPEG/PNG/WebP, 10 MB). Migration 0008 document
  (record kept, one-time delete mark). Files 0600 in DOCUMENT_DIR (Docker volume);
  removed only after the delete commits; failed uploads leave no file. Cards on
  people/loans, /documents, view logged, no-store + sandbox headers. gen-doc-key;
  backups include documents; key kept out of backups on purpose. 265 tests.

- Evaluation after 14: clean; pip-audit found no known vulnerabilities.
- Phase 15 (borrower statement links): migration 0009 share_link (SHA-256 of the
  token only; 7/30/90 days; one-time revoke; view count; guarded). /s/{token} and
  its receipts: no login, no session/cookie, no-store, noindex, strict CSP, 30
  failures/15 min per IP. BorrowerStatement exposes only the borrower's own loan.
  Owner: create (link shown once, WhatsApp send), list, switch off; views logged.
  PUBLIC_URL setting; receipt card shared via _receipt_doc.html. 272 tests.

- Review of 15: bounded failure counters (login + links); business name on statements.
- Phase 16 (read-only login): migration 0010 app_user role OWNER/VIEWER, disabled_at,
  session_epoch. user() now checks the DB every request (disabled user or password
  reset ends sessions at once) and refuses any non-GET from a viewer, pages and API;
  documents owner-only. CLI create-user --viewer, list-users, disable/enable-user
  (last owner protected). Viewer UI hides change controls. Route-walk test. 277 tests.

## Next step
Deferred ideas: application/approval workflow, official WhatsApp API.


## UI review fixes (branch ui-fixes)
- Clicked through all 64 pages and 51 forms/buttons (desktop, phone, dark). Fixed:
- Documents page now has its own upload form (person, optional loan, kind, title, file).
- Bank import: a same-amount entry of another person is no longer "already recorded" when the narration names someone else; "Link all" skips it.
- Settled/closed loans no longer offer WhatsApp reminders or "Log a contact".
- Flash messages in plain words (Top-up / Rate change / which payment was reversed); funding refusal says how much is still unlinked.
- Phone layout: no sideways scrolling on person, reports, funding pages; styled file pickers; calendar tabs on one line.

## Phase 17 - React frontend (branch react-ui) - done
- Whole UI rebuilt in React + TypeScript + Tailwind (frontend/), design "Ledgerly option 1"; Jinja pages removed.
- Backend: views.py = one JSON payload per screen from the same services (numbers unchanged); CSRF via X-CSRF-Token header.
- New: Payments page, Ctrl K search, "vs last month" trends; public borrower pages as React + /api/public JSON.
- Tests: test_web.py ported to the API with the same expected numbers + new tests (288 pass); Vitest for formatting;
  browser click-through 70/70 (desktop, phone, viewer, borrower link). Dockerfile builds the app in a Node stage.
- Review fixes: service worker registered again (installable); viewers can use What-if (READ_ONLY_POSTS); book switch query keys.

## Phase 18 - Android app (branch android-app) - done
- Capacitor wraps the React app (frontend/android); APK built and signed by GitHub Actions on tags v*.
- Phone sign-in = device token (app_device, hash only, DB-guarded); bearer auth, no CSRF needed; CORS only for the app;
  ends on sign-out, password reset, disable, 90 days idle. CLI list-devices / revoke-device; Settings page.
- App: server address (https only), fingerprint lock, due reminders, share sheet for files, back button; no backups.
- Tests: 299 pytest (10 new), 10 Vitest, web click-through 73/73, app mode from another origin 19/19.
- Not run here: the Gradle/APK build itself (Android servers blocked in this workspace) - first run is on GitHub.

## App fixes (branch app-fixes)
- App: white strips at the status/gesture bars (Capacitor padding showed the theme's white window) -> deep-teal bars, light icons; safe areas now add to padding instead of replacing it (web pages had lost their bottom padding).
- Sign-out after 30 min without use (server + screen timer, keep-alive while reading), 12 h max browser session; reason shown on sign-in.
- One sign-in per user (SINGLE_SESSION): a new sign-in ends the user's other browsers and phones.
- Review fixes: double sign-out requests, sign-in screen bouncing back after sign-out, app sign-in race, "Change server" had no cancel, reminders switch only applied after restart, listener leaks, CLI idle state.
- Tests: 302 pytest, 10 Vitest, web click-through 80/80, app mode 24/24.

## Phase 19 - Windows app + Android fixes (branch windows-app)
- Tauri 2 shell (frontend/src-tauri) wraps the same React build; MSI built by GitHub Actions (windows.yml) on tags v*, unsigned.
- Desktop mode: device name "Windows PC", in-app file preview + save to Downloads, external links open in the system browser, print kept.
- CORS default APP_ORIGINS adds https://tauri.localhost (test added).
- Android: doubled teal band under the status bar fixed (SystemBars insetsHandling native, no viewport-fit=cover).
- App start (Android and Windows) always ends the previous sign-in and asks for the password.
- Tests: 303 pytest, 10 Vitest, web 80/80, phone mode 26/26, Windows mode 15/15. MSI/Rust build not run here (first run on GitHub).
