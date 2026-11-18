-- Phase 11: cash and liquidity. Your bank/cash accounts, their balances over
-- time, and planned money in/out. None of this is loan money: the ledger,
-- dues and allocations are untouched.

CREATE TABLE cash_account (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name        text NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
  kind        text NOT NULL CHECK (kind IN ('BANK', 'CASH', 'WALLET')),
  book        loan_book NOT NULL,
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- balance history: each update is a new row (append-only); the latest one per
-- account is the current balance. Negative allowed (overdraft).
CREATE TABLE cash_balance (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  account_id     bigint NOT NULL REFERENCES cash_account (id),
  as_of          date NOT NULL,
  balance_paise  bigint NOT NULL,
  note           text,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cash_balance_latest ON cash_balance (account_id, as_of DESC, id DESC);
CREATE TRIGGER cash_balance_append_only
  BEFORE UPDATE OR DELETE ON cash_balance
  FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE planned_cash_item (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  book          loan_book NOT NULL,
  item_date     date NOT NULL,
  direction     text NOT NULL CHECK (direction IN ('IN', 'OUT')),
  amount_paise  bigint NOT NULL CHECK (amount_paise > 0),
  description   text NOT NULL CHECK (length(trim(description)) > 0),
  cancelled_at  timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX planned_cash_item_date ON planned_cash_item (item_date);

-- a planned item is never deleted or rewritten, only cancelled once
CREATE OR REPLACE FUNCTION planned_cash_item_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'planned items cannot be deleted; cancel them instead';
  END IF;
  IF OLD.cancelled_at IS NOT NULL OR NEW.cancelled_at IS NULL
     OR (to_jsonb(NEW) - 'cancelled_at') IS DISTINCT FROM (to_jsonb(OLD) - 'cancelled_at') THEN
    RAISE EXCEPTION 'a planned item can only be cancelled, once';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER planned_cash_item_only_cancel
  BEFORE UPDATE OR DELETE ON planned_cash_item
  FOR EACH ROW EXECUTE FUNCTION planned_cash_item_guard();
