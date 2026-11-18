-- Phase 9: collections. Contact history and promises to pay.
-- A promise is NEVER money: its status (pending/kept/broken) is derived from
-- real PAYMENT entries by loan_tracker/collections.py, never stored.

CREATE TABLE collection_activity (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loan_id         bigint NOT NULL REFERENCES loan (id),
  contact_date    date NOT NULL,
  kind            text NOT NULL CHECK (kind IN ('CALL', 'VISIT', 'WHATSAPP', 'SMS', 'OTHER')),
  outcome         text NOT NULL CHECK (outcome IN ('REACHED', 'NO_ANSWER', 'PROMISED', 'DISPUTED', 'PAID', 'OTHER')),
  note            text,
  next_follow_up  date CHECK (next_follow_up IS NULL OR next_follow_up >= contact_date),
  actor           text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX collection_activity_loan ON collection_activity (loan_id, contact_date DESC, id DESC);

-- history: append-only, like the ledger
CREATE TRIGGER collection_activity_append_only
  BEFORE UPDATE OR DELETE ON collection_activity
  FOR EACH ROW EXECUTE FUNCTION append_only_guard();

CREATE TABLE payment_promise (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  loan_id         bigint NOT NULL REFERENCES loan (id),
  activity_id     bigint REFERENCES collection_activity (id),
  made_on         date NOT NULL,
  promised_date   date NOT NULL CHECK (promised_date >= made_on),
  amount_paise    bigint NOT NULL CHECK (amount_paise > 0),
  cancelled_at    timestamptz,
  cancel_note     text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX payment_promise_loan ON payment_promise (loan_id, promised_date);

-- a promise is never deleted or rewritten; the only change allowed is
-- cancelling it once (cancelled_at + cancel_note, from NULL)
CREATE OR REPLACE FUNCTION payment_promise_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'payment_promise rows cannot be deleted; cancel the promise instead';
  END IF;
  IF OLD.cancelled_at IS NOT NULL
     OR NEW.cancelled_at IS NULL
     OR (NEW.loan_id, NEW.activity_id, NEW.made_on, NEW.promised_date, NEW.amount_paise, NEW.created_at)
        IS DISTINCT FROM
        (OLD.loan_id, OLD.activity_id, OLD.made_on, OLD.promised_date, OLD.amount_paise, OLD.created_at) THEN
    RAISE EXCEPTION 'a promise can only be cancelled, once';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER payment_promise_only_cancel
  BEFORE UPDATE OR DELETE ON payment_promise
  FOR EACH ROW EXECUTE FUNCTION payment_promise_guard();
