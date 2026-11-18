-- Phase 10: payment receipts for money received on loans you GAVE.
-- A receipt is a document: a snapshot of the payment's split and the balance
-- left, taken when it was issued. Numbers RCPT-<year>-<seq> are never reused.
-- Reversing the payment marks the receipt cancelled; it is never deleted.

CREATE TABLE receipt_counter (
  year      int PRIMARY KEY,
  last_seq  int NOT NULL CHECK (last_seq >= 0)
);

CREATE TABLE receipt (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  number                text NOT NULL UNIQUE,
  year                  int NOT NULL,
  seq                   int NOT NULL CHECK (seq > 0),
  ledger_entry_id       bigint NOT NULL UNIQUE REFERENCES ledger_entry (id),
  loan_id               bigint NOT NULL REFERENCES loan (id),
  issued_on             date NOT NULL,
  payment_date          date NOT NULL,
  amount_paise          bigint NOT NULL CHECK (amount_paise > 0),
  penalty_paise         bigint NOT NULL DEFAULT 0 CHECK (penalty_paise >= 0),
  interest_paise        bigint NOT NULL DEFAULT 0 CHECK (interest_paise >= 0),
  principal_paise       bigint NOT NULL DEFAULT 0 CHECK (principal_paise >= 0),
  credit_paise          bigint NOT NULL DEFAULT 0 CHECK (credit_paise >= 0),
  principal_left_paise  bigint NOT NULL CHECK (principal_left_paise >= 0),
  interest_left_paise   bigint NOT NULL CHECK (interest_left_paise >= 0),
  payment_mode          text,
  reference             text,
  cancelled_at          timestamptz,
  cancel_reason         text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (year, seq),
  CHECK (penalty_paise + interest_paise + principal_paise + credit_paise = amount_paise)
);
CREATE INDEX receipt_loan ON receipt (loan_id);

CREATE OR REPLACE FUNCTION receipt_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'receipts cannot be deleted; reverse the payment to cancel its receipt';
  END IF;
  IF OLD.cancelled_at IS NOT NULL OR NEW.cancelled_at IS NULL
     OR (to_jsonb(NEW) - 'cancelled_at' - 'cancel_reason') IS DISTINCT FROM (to_jsonb(OLD) - 'cancelled_at' - 'cancel_reason') THEN
    RAISE EXCEPTION 'a receipt can only be cancelled, once';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER receipt_only_cancel
  BEFORE UPDATE OR DELETE ON receipt
  FOR EACH ROW EXECUTE FUNCTION receipt_guard();
