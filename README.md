# Loan Tracker

Track money you **lend** and money you **borrow**, across **business** and **personal**
books: interest dues, payments, overdue amounts, payoff figures, which borrowed money
funds which loan, and a daily Telegram/email digest of what to pay and collect.

- Exact money: integer paise, never floats. Every number comes from an append-only ledger.
- Monthly flat interest ("₹2 per ₹100 per month"): a full cycle earns exactly P × rate;
  partial periods are pro-rated by days (DECISIONS #15).
- Phone-first web app "Ledgerly" (React, installable), login with password + authenticator code.

---

## 1. Run it on your PC (Docker Desktop)

1. Install **Docker Desktop**, then in PowerShell inside the project folder:
   ```powershell
   Copy-Item .env.example .env        # then edit .env: POSTGRES_PASSWORD, SECRET_KEY (and the
                                      # same password inside DATABASE_URL / TEST_DATABASE_URL)
   docker compose up -d --build       # database + app + daily scheduler
   docker compose exec app python -m loan_tracker.cli create-user owner
   ```
2. `create-user` asks for a password and prints a **setup key**. Add it in Google
   Authenticator / Microsoft Authenticator / Aegis ("enter a setup key").
3. Open **http://localhost:8000** (or the `APP_PORT` you set in `.env`) and log in with the
   password and the 6-digit code.

   Port already in use? Set `APP_PORT` (and `DB_PORT` if 5432 is taken) in `.env`, then
   `docker compose up -d`. On Windows, `netsh interface ipv4 show excludedportrange protocol=tcp`
   lists ports Windows has reserved.

**Documents (ID proofs, agreements, cheque photos)** are stored encrypted. They stay
switched off until you create a key:
```powershell
docker compose exec app python -m loan_tracker.cli gen-doc-key   # prints DOCUMENT_KEY=...
```
Put that line in `.env` **and in your password manager**, then `docker compose up -d`.
The key is not in any backup on purpose: **if you lose it, the documents cannot be opened.**
To change it later, move the old value to `DOCUMENT_KEY_OLD` and set a new `DOCUMENT_KEY`
(old files stay readable; new ones use the new key).

Want to try it with sample data first? `docker compose exec app python -m loan_tracker.cli demo-data`
(creates the worked examples A–C; names end in "(demo)").

## 2. Daily use

| Task | Where |
|---|---|
| Record a payment (3 taps) | **+ Payment** |
| Lend / borrow money | **Loans → + Lend / + Borrow** (book is required) |
| See what to pay / collect | **Home** (next 7 days and overdue) |
| Money in vs out ahead | **Calendar** (30 / 60 / 90 days) |
| Fix a wrong entry | open the loan → **reverse** next to the entry (nothing is deleted) |
| Link borrowed money to a loan | footer → **Funding links** |
| Interest per financial year, overdue list | **Reports** |
| Statement for a borrower | loan page → **Statement** → print / save as PDF |
| Everything in Excel | footer → **Export CSV** |
| Business only / personal only | the **All · Business · Personal** toggle at the top |

### Read-only login (accountant, family member)

```powershell
docker compose exec app python -m loan_tracker.cli create-user accountant --viewer
docker compose exec app python -m loan_tracker.cli list-users
docker compose exec app python -m loan_tracker.cli disable-user accountant   # ends their access at once
```
A viewer sees every page but cannot change anything and cannot open documents.
Running `create-user` again for an existing name resets the password and 2FA and logs
that user out everywhere.

## 3. Daily digest (Telegram / email)

The `scheduler` container runs the daily job at `JOB_TIME` (default 08:00 IST). Fill in
`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (and/or the `SMTP_*` settings) in `.env`, then
`docker compose up -d`. Preview without sending:
```powershell
docker compose exec app python -m loan_tracker.cli run-job --dry-run
```
Payments **you** owe that are due today or overdue are never muted. The home page shows
when the job last ran.

## 4. Use it from your phone

- **Easiest and private:** install [Tailscale](https://tailscale.com) on the PC/server and
  your phone, then open `http://<machine-name>:8000`. Nothing is exposed to the internet.
- **On a server with a domain:** run the same `docker compose` there, put
  `deploy/Caddyfile` in front for HTTPS, and set `SECURE_COOKIES=1`.

On the phone, use the browser's **Add to Home screen** to install it like an app.

**Statement links for borrowers** (loan page → *Statement link*) only open for a borrower if
the app is reachable from the internet: run it on a server behind `deploy/Caddyfile` (HTTPS)
and set `PUBLIC_URL=https://your-domain` in `.env`. With Tailscale only you and your devices
can open them. A link shows that borrower's own loan, read-only, and stops working when it
expires or when you switch it off.

## 5. Backups (do this)

- Windows: schedule `deploy\backup.ps1` daily in Task Scheduler (7-Zip + `BACKUP_PASSPHRASE` encrypts it).
- Linux server: cron `deploy/backup.sh` (gpg-encrypted, keeps 60 days).
- Both scripts back up the database **and** the documents folder.
- Copy the `backups` folder **offsite**, and test a restore once a month (commands are inside the scripts).
- Keep `DOCUMENT_KEY` (and `BACKUP_PASSPHRASE`) in your password manager, not only in `.env`:
  a backup of the documents is useless without the key.

## 6. Development

The app is two parts: the Python server (FastAPI: login, JSON API, the money engine) and the
React screens in `frontend/` (Vite + TypeScript + Tailwind). Docker builds both for you; to
work on them directly you need Python 3.12 and Node.js 22.

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
docker compose up -d db
python -m loan_tracker.cli migrate
uvicorn loan_tracker.web.app:app --reload      # API on http://127.0.0.1:8000

cd frontend
npm ci
npm run dev                                    # screens on http://127.0.0.1:5173 (calls the API above)
npm run build                                  # writes loan_tracker/web/spa; then port 8000 serves everything
```

Tests (engine tests need nothing; database tests need an empty test database):
```powershell
docker compose exec db createdb -U loan_tracker loan_tracker_test
$env:TEST_DATABASE_URL = "postgresql+psycopg://loan_tracker:<password>@127.0.0.1:5432/loan_tracker_test"
pytest -q -x --tb=short
cd frontend ; npm test ; npm run typecheck
```

Using the API from a script: log in with `POST /api/login`, then send the `csrf` value from the
answer in an `X-CSRF-Token` header on every request that changes something. API docs are at
`/api/docs` after logging in.

## 7. Layout

```
loan_tracker/engine/   pure calculation engine (no DB, no clock) — compute(terms, entries, as_of)
loan_tracker/db/       SQL schema + Alembic migration, ORM models
loan_tracker/services.py   the only code that writes; appends ledger rows, rebuilds caches
loan_tracker/reports.py    dashboard, cash calendar, FY report, CSV export
loan_tracker/web/      FastAPI app: JSON API at /api/* (views.py = one payload per screen), serves the built React app
frontend/              React + TypeScript + Tailwind screens (design: docs/design/ledgerly-option1)
loan_tracker/jobs/     daily job and Telegram/email senders
loan_tracker/cli.py    migrate, create-user, run-job, scheduler, demo-data
tests/engine/          engine tests incl. worked examples and property tests
tests/db/              services, web and job tests against PostgreSQL
docs/                  design docs, decisions, progress log
```

Rules that must never be broken are in `CLAUDE.md` and `docs/02-principles.md`.
Not legal or tax advice: state Money Lenders Acts may require a licence; confirm the
tax treatment of interest with a CA.
