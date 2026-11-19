# Phase 17 - React frontend (all at once)

Owner's choices (2026-11-19): build the whole React app on branch `react-ui` and switch over in
one go; Tailwind CSS; the public borrower pages (/s/...) move to React too; dependencies approved:
React, React Router, TypeScript, Vite, TanStack Query, Vitest, Tailwind (+ Node build stage).
Look and feel: `docs/design/ledgerly-option1/` (deep teal sidebar grouped Workspace / Analyse,
teal / emerald / amber, metric cards, interest chart, portfolio health, To collect / To pay).

## Rules that do not change
- The engine, services, ledger, money rules and the database are untouched.
- The browser never calculates money. It shows paise the server computed and sends amounts
  the user typed as text ("1,00,000"); the server parses them (`rupees_to_paise`).
- Viewers stay read-only; documents stay owner-only; public links stay token-only + rate limited.

## Backend
- `loan_tracker/web/views.py`: one JSON payload per screen (`/api/ui/...`), built from the same
  services/reports the Jinja pages used, so every number is identical.
- New endpoints for actions that were HTML-only: login/logout/session, party update, loan
  status, share links, documents (multipart upload, file, delete), bank upload + link-exact,
  cash account open/close, receipts backfill, public statement/receipt JSON.
- New for the design: `/api/ui/payments` (all payments in and out), `/api/search` (Ctrl K),
  month-over-month trends and a 12-month interest series on the overview.
- CSRF: every non-GET `/api/` request must send `X-CSRF-Token` equal to the session's token
  (from `GET /api/session`). Works for JSON and file uploads; a cross-site page can't set it.
- FastAPI serves the built app (`loan_tracker/web/spa/`) with an SPA fallback and a strict CSP;
  `/s/...` responses keep no-store / noindex / no-referrer.
- Jinja templates and HTML routes are deleted at the switch. `/export.csv` stays.

## Frontend (`frontend/`)
- Vite + React + TypeScript + Tailwind; React Router; TanStack Query for fetching/caching.
- Shell: sidebar (drawer on phones), top bar with search, book switch (All/Business/Personal),
  toasts, dark mode.
- Screens: Login, Overview, Loans, Loan (payments, entries, status, collections, share links,
  documents), New loan / borrow, Record payment, Collections, Payments, Calendar, People, Person,
  Documents, Cash & forecast, What-if, Reports, Statement, Funding links, Bank import (+ one
  import), Receipts (+ printable receipt), Closure certificate, Activity, public statement and
  receipt.
- Charts are plain SVG (no chart library).

## Tests
- Backend: the HTML-page tests in tests/db/test_web.py are rewritten against the JSON API with
  the same expected numbers; new tests for every new endpoint, CSRF header and public pages.
- Frontend: Vitest for formatting helpers; full browser click-through (Playwright) at the end.

## Order
1. API completion + CSRF + SPA serving (backend tests green)
2. Frontend scaffold, design system, shell, login
3. Screens, area by area
4. Switch-over: delete Jinja, Dockerfile Node stage, README, click-through on desktop + phone
