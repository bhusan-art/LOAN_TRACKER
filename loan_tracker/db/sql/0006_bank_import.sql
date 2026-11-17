-- Phase 13: bank statement import. Transactions from an uploaded bank CSV,
-- and what the owner decided for each. Money only reaches the ledger when the
-- owner records a payment (through services.post_entry, like any payment).

CREATE TABLE bank_import (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  account_id      bigint NOT NULL REFERENCES cash_account (id),
  file_name       text NOT NULL,
  actor           text NOT NULL,
  row_count       int NOT NULL CHECK (row_count >= 0),
  new_count       int NOT NULL CHECK (new_count >= 0),
  period_start    date,
  period_end      date,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER bank_import_append_only
  BEFORE UPDATE OR DELETE ON bank_import
  FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE bank_txn (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  import_id        bigint NOT NULL REFERENCES bank_import (id),
  account_id       bigint NOT NULL REFERENCES cash_account (id),
  fingerprint      text NOT NULL,
  line_no          int NOT NULL,
  txn_date         date NOT NULL,
  description      text NOT NULL DEFAULT '',
  reference        text,
  amount_paise     bigint NOT NULL CHECK (amount_paise <> 0),   -- + money in, - money out
  balance_paise    bigint,
  status           text NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW', 'LINKED', 'RECORDED', 'IGNORED')),
  ledger_entry_id  bigint REFERENCES ledger_entry (id),
  note             text,
  decided_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (account_id, fingerprint),
  CHECK ((status IN ('LINKED', 'RECORDED')) = (ledger_entry_id IS NOT NULL))
);
CREATE UNIQUE INDEX bank_txn_one_per_entry ON bank_txn (ledger_entry_id) WHERE ledger_entry_id IS NOT NULL;
CREATE INDEX bank_txn_import ON bank_txn (import_id, txn_date, line_no);
CREATE INDEX bank_txn_open ON bank_txn (account_id, status);

-- the bank's figures never change; only the owner's decision does
CREATE OR REPLACE FUNCTION bank_txn_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'bank transactions cannot be deleted; ignore them instead';
  END IF;
  IF (to_jsonb(NEW) - 'status' - 'ledger_entry_id' - 'note' - 'decided_at')
     IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'ledger_entry_id' - 'note' - 'decided_at') THEN
    RAISE EXCEPTION 'only the decision on a bank transaction can change, not its date, amount or text';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER bank_txn_only_decision
  BEFORE UPDATE OR DELETE ON bank_txn
  FOR EACH ROW EXECUTE FUNCTION bank_txn_guard();
