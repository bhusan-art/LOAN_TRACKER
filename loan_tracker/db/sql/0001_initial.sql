-- Loan Tracker — PostgreSQL 16 schema (Phase 1 design; docs only, no app code yet)
--
-- Conventions encoded here:
--   * Money = integer paise (bigint, CHECK > 0). Never float. Rates = NUMERIC.
--   * Rounding is an engine concern only (DECISIONS #8: paise, half-up) — nothing
--     in the schema depends on a rounding unit.
--   * P1/P2: ledger_entry is append-only, enforced at DB level (triggers, end of
--     file). Corrections are REVERSAL pairs: a REVERSAL row carries reverses_id
--     pointing at the original (set at insert; UNIQUE so an entry is reversed at
--     most once). party rows block DELETE only (they allow UPDATE; see triggers).
--   * due rows are IMMUTABLE SKELETONS (review 2.1): created once by the daily
--     job when a cycle ends; paid/waived/penalty/status are engine outputs
--     computed per as-of date and are NOT stored. due_status below is the
--     derived status vocabulary only.
--   * allocation rows are a regenerable derived cache (review 2.2): the job
--     atomically rebuilds a loan's allocations when its ledger changes. P2's
--     append-only rule applies to ledger_entry ONLY.
--   * RATE_CHANGE carries new_rate (review 2.4); loan.rate = initial rate,
--     overridden by the latest unreversed RATE_CHANGE.

-- =====================================================================
-- Enums
-- =====================================================================
CREATE TYPE loan_direction    AS ENUM ('GIVEN', 'TAKEN');
CREATE TYPE loan_book         AS ENUM ('BUSINESS', 'PERSONAL');
CREATE TYPE loan_status       AS ENUM ('ACTIVE', 'OVERDUE', 'MATURED', 'CLOSED', 'SETTLED', 'WRITTEN_OFF');
CREATE TYPE rate_unit         AS ENUM ('PER_MONTH', 'PER_YEAR');
CREATE TYPE interest_type     AS ENUM ('SIMPLE', 'COMPOUND', 'EMI');      -- v1 engine supports SIMPLE only (DECISIONS #1)
CREATE TYPE interest_cycle    AS ENUM ('MONTHLY', 'QUARTERLY', 'AT_MATURITY');
CREATE TYPE entry_type        AS ENUM ('DISBURSE', 'TOPUP', 'PAYMENT',
                                       'WAIVER', 'PENALTY', 'RATE_CHANGE', 'CAPITALISE', 'REVERSAL');
CREATE TYPE payment_mode      AS ENUM ('CASH', 'UPI', 'BANK');
CREATE TYPE allocation_bucket AS ENUM ('PENALTY', 'INTEREST', 'PRINCIPAL');
CREATE TYPE due_status        AS ENUM ('UPCOMING', 'PARTIAL', 'PAID', 'OVERDUE', 'WAIVED'); -- derived; never stored
CREATE TYPE reminder_kind    AS ENUM ('REMINDER', 'OVERDUE', 'PAY_ALERT');
CREATE TYPE channel_type      AS ENUM ('TELEGRAM', 'EMAIL');

-- =====================================================================
-- Tables
-- =====================================================================

-- party: anyone the owner lends to or borrows from; a person can be on both
-- sides. DELETE is blocked (trigger below) but UPDATE is allowed so the owner
-- can correct contact fields; the identity row is never deleted.
CREATE TABLE party (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name          text NOT NULL,
  phone         text,
  address       text,
  id_proof_ref  text,
  guarantor     text,
  notes         text,
  risk_tag      text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- loan: the AGREEMENT (P4) — terms only, no events. A RATE_CHANGE entry
-- overrides rate without editing this row; status is maintained by the daily
-- job. Every loan has direction and book (non-negotiable rule 5).
CREATE TABLE loan (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  party_id       bigint NOT NULL REFERENCES party (id),
  direction      loan_direction  NOT NULL,
  book           loan_book       NOT NULL,
  principal_paise bigint NOT NULL CHECK (principal_paise > 0),
  start_date     date NOT NULL,
  rate           numeric(7,4) NOT NULL CHECK (rate >= 0),  -- initial rate; overridden by latest RATE_CHANGE
  rate_unit      rate_unit       NOT NULL,
  interest_type  interest_type   NOT NULL,
  interest_cycle interest_cycle  NOT NULL DEFAULT 'MONTHLY',
  due_day        smallint NOT NULL CHECK (due_day BETWEEN 1 AND 31),  -- clamped to month-end at cycle boundaries (review 2.5)
  grace_days     smallint NOT NULL DEFAULT 3 CHECK (grace_days >= 0), -- DECISIONS #9
  penalty_rule   text,             -- JSON text, e.g. {"kind":"flat","paise":500,"per":"cycle"}; DECISIONS #3: off in v1
  tenure_months  integer,
  maturity_date  date,
  collateral     text,
  purpose        text,
  status         loan_status NOT NULL DEFAULT 'ACTIVE',
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (maturity_date IS NULL OR maturity_date > start_date)
);

-- ledger_entry: append-only EVENTS (P1). Money types carry a positive paise
-- amount; RATE_CHANGE carries new_rate; a REVERSAL row carries reverses_id
-- pointing at the original (set at insert; UNIQUE, so an entry is reversed at
-- most once). There is no PRINCIPAL_PREPAY type (DECISIONS #13) — principal
-- reduction is the PRINCIPAL bucket of a PAYMENT. Penalty dedupe ("at most one
-- per (loan, cycle, bucket)") is enforced by the daily job, not the DB.
CREATE TABLE ledger_entry (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loan_id        bigint NOT NULL REFERENCES loan (id),
  entry_date     date NOT NULL,
  type           entry_type NOT NULL,
  amount_paise   bigint,
  payment_mode   payment_mode NOT NULL DEFAULT 'CASH',
  reference      text,
  note           text,
  new_rate       numeric(7,4),
  reverses_id    bigint REFERENCES ledger_entry (id),   -- set on a REVERSAL row: the entry it cancels
  created_at     timestamptz NOT NULL DEFAULT now(),

  -- money-bearing types must have a positive paise amount
  CHECK (type NOT IN ('DISBURSE', 'TOPUP', 'PAYMENT', 'PENALTY', 'CAPITALISE')
         OR (amount_paise IS NOT NULL AND amount_paise > 0)),
  -- non-monetary types must not carry an amount
  CHECK (type NOT IN ('RATE_CHANGE', 'REVERSAL') OR amount_paise IS NULL),
  -- WAIVER may waive an explicit amount or everything pending
  CHECK (type <> 'WAIVER' OR amount_paise IS NULL OR amount_paise > 0),
  -- only RATE_CHANGE carries a rate
  CHECK (new_rate IS NULL OR (type = 'RATE_CHANGE' AND new_rate >= 0)),
  -- reverses_id is required on a REVERSAL and null otherwise; UNIQUE so one
  -- original is reversed at most once (NULLs are distinct, so non-reversals pass)
  CHECK (type = 'REVERSAL' OR reverses_id IS NULL),
  CHECK (type <> 'REVERSAL' OR reverses_id IS NOT NULL),
  UNIQUE (reverses_id)
);

-- due: one IMMUTABLE skeleton row per interest cycle (review 2.1). Created
-- once by the daily job when the cycle ends (unique (loan_id, period_start)
-- makes re-runs idempotent). Anything mutable — paid/waived/penalty paise,
-- status — is an engine output for a chosen as-of date, never stored here.
CREATE TABLE due (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loan_id        bigint NOT NULL REFERENCES loan (id),
  period_start   date NOT NULL,
  period_end     date NOT NULL,
  due_date       date NOT NULL,
  interest_paise bigint NOT NULL CHECK (interest_paise >= 0),
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (period_start < period_end),
  CHECK (due_date >= period_end),
  UNIQUE (loan_id, period_start)
);

-- allocation: how each money-bearing entry was split by the fixed waterfall
-- (P5). DUE_PERIOD_START (nullable) references a due by (loan, period_start);
-- it is null for the PRINCIPAL bucket and for the PENALTY bucket. This table
-- is a regenerable cache: the job replaces a loan's rows atomically when its
-- ledger changes (P2 does not apply here).
CREATE TABLE allocation (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ledger_entry_id bigint NOT NULL REFERENCES ledger_entry (id),
  due_period_start date,                                -- which interest due the INTEREST bucket paid
  bucket          allocation_bucket NOT NULL,
  amount_paise    bigint NOT NULL CHECK (amount_paise > 0)
);

-- funding_link: which borrowed money (TAKEN) funded which GIVEN loan;
-- many-to-many. The unlinked part of a GIVEN loan is own capital. App-level
-- rule: taken_loan_id must be a direction=TAKEN loan, given_loan_id a
-- direction=GIVEN loan. Cross-book links are allowed but flagged in reports.
CREATE TABLE funding_link (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  taken_loan_id bigint NOT NULL REFERENCES loan (id),
  given_loan_id bigint NOT NULL REFERENCES loan (id),
  amount_paise  bigint NOT NULL CHECK (amount_paise > 0),
  link_date     date NOT NULL,
  UNIQUE (taken_loan_id, given_loan_id, link_date)
);

-- reminder_log: what was sent about which loan + due date, so reminders never
-- duplicate. Keyed by (loan_id, due_date) rather than a due row id: projected
-- dues (review 2.3) have no `due` row yet when their "3 days before" reminder
-- fires. sent_on is the dedupe calendar day; sent_at is the actual send time.
CREATE TABLE reminder_log (
  id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loan_id  bigint NOT NULL REFERENCES loan (id),
  due_date date NOT NULL,
  kind     reminder_kind NOT NULL,
  channel  channel_type  NOT NULL,
  sent_on  date NOT NULL,
  sent_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (loan_id, due_date, kind, channel, sent_on)
);

-- =====================================================================
-- Indexes (from docs/03-data-model.md, adjusted for the skeleton due)
-- =====================================================================
CREATE INDEX ix_ledger_entry_loan_date ON ledger_entry (loan_id, entry_date);
CREATE INDEX ix_due_due_date ON due (due_date);          -- 03 lists (due_date, status); status is derived, not stored
CREATE INDEX ix_loan_book_direction_status ON loan (book, direction, status);
CREATE INDEX ix_allocation_entry ON allocation (ledger_entry_id);
CREATE INDEX ix_funding_given ON funding_link (given_loan_id);
CREATE INDEX ix_funding_taken ON funding_link (taken_loan_id);
-- reminder dedupe (same loan + due_date + kind + channel, once per day) is the
-- UNIQUE constraint inline on reminder_log; a plain index helps date scans
CREATE INDEX ix_reminder_sent ON reminder_log (loan_id, due_date, sent_on);

-- =====================================================================
-- Append-only enforcement (P1/P2, non-negotiable rule 2)
-- =====================================================================

-- ledger_entry: fully append-only — no UPDATE or DELETE. Corrections are
-- posted as a REVERSAL row carrying reverses_id, never an edit in place.
CREATE OR REPLACE FUNCTION append_only_guard() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: post a REVERSAL entry instead of UPDATE/DELETE', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_entry_append_only
  BEFORE UPDATE OR DELETE ON ledger_entry
  FOR EACH ROW EXECUTE FUNCTION append_only_guard();

-- party: DELETE is blocked (the identity row is permanent) but UPDATE is
-- allowed so the owner can fix contact fields (phone, notes, risk_tag, ...).
CREATE OR REPLACE FUNCTION party_delete_guard() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'party rows cannot be deleted; add a note instead';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER party_delete_only
  BEFORE DELETE ON party
  FOR EACH ROW EXECUTE FUNCTION party_delete_guard();

-- ---------------------------------------------------------------------------
-- Added in Phase 3 (not in the Phase 1 design)
-- ---------------------------------------------------------------------------

-- `due` and `allocation` are CACHES rebuilt from ledger_entry by
-- services.recompute_loan() whenever a loan's ledger changes, and by the
-- daily job. A reversal can legitimately change later dues (Example A:
-- reversing the 5 Feb payment moves the Apr due 167_097 -> 168_742), so
-- these rows are replaced per loan, never edited by hand.

-- The owner's login (DECISIONS #6: owner only). Password = scrypt hash;
-- totp_secret = base32 secret for the authenticator app (2FA).
CREATE TABLE app_user (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  username      text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  totp_secret   text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- One row per daily-job run, for the "last run" health check (NFR observability).
CREATE TABLE job_run (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_date    date NOT NULL,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  ok          boolean,
  summary     text
);
CREATE INDEX ix_job_run_started ON job_run (started_at DESC);
