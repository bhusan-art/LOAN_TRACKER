# Loan Tracker

Track money you **lend** and money you **borrow**, across **business** and **personal**
books: interest dues, payments, overdue amounts, payoff figures, which borrowed money
funds which loan, and a daily Telegram/email digest of what to pay and collect.

- Exact money: integer paise, never floats. Every number comes from an append-only ledger.
- Monthly flat interest ("₹2 per ₹100 per month"): a full cycle earns exactly P × rate;
  partial periods are pro-rated by days (DECISIONS #15).
- Phone-first web app (installable), login with password + authenticator code.

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
3. Open **http://localhost:8000** and log in with the password and the 6-digit code.

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

## 5. Backups (do this)

- Windows: schedule `deploy\backup.ps1` daily in Task Scheduler (7-Zip + `BACKUP_PASSPHRASE` encrypts it).
- Linux server: cron `deploy/backup.sh` (gpg-encrypted, keeps 60 days).
- Copy the `backups` folder **offsite**, and test a restore once a month (commands are inside the scripts).

## 6. Development

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
docker compose up -d db
python -m loan_tracker.cli migrate
uvicorn loan_tracker.web.app:app --reload      # http://127.0.0.1:8000
```

Tests (engine tests need nothing; database tests need an empty test database):
```powershell
docker compose exec db createdb -U loan_tracker loan_tracker_test
$env:TEST_DATABASE_URL = "postgresql+psycopg://loan_tracker:<password>@127.0.0.1:5432/loan_tracker_test"
pytest -q -x --tb=short
```

## 7. Layout

```
loan_tracker/engine/   pure calculation engine (no DB, no clock) — compute(terms, entries, as_of)
loan_tracker/db/       SQL schema + Alembic migration, ORM models
loan_tracker/services.py   the only code that writes; appends ledger rows, rebuilds caches
loan_tracker/reports.py    dashboard, cash calendar, FY report, CSV export
loan_tracker/web/      FastAPI app (HTML pages + JSON API at /api/*, docs at /api/docs)
loan_tracker/jobs/     daily job and Telegram/email senders
loan_tracker/cli.py    migrate, create-user, run-job, scheduler, demo-data
tests/engine/          engine tests incl. worked examples and property tests
tests/db/              services, web and job tests against PostgreSQL
docs/                  design docs, decisions, progress log
```

Rules that must never be broken are in `CLAUDE.md` and `docs/02-principles.md`.
Not legal or tax advice: state Money Lenders Acts may require a licence; confirm the
tax treatment of interest with a CA.
