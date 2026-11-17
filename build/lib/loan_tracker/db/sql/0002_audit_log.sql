-- Phase 8: audit log. Who did what and when: logins, new loans and entries,
-- reversals, owner status changes, party edits, funding links, users.
-- Append-only like ledger_entry (same guard). Never holds passwords or 2FA codes.
CREATE TABLE audit_log (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  actor      text NOT NULL,                 -- username, or 'cli' / 'job'
  action     text NOT NULL,                 -- e.g. LOGIN_OK, ENTRY_REVERSED
  entity     text,                          -- loan, ledger_entry, party, funding_link, app_user
  entity_id  bigint,
  detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
  ip         text
);
CREATE INDEX audit_log_at ON audit_log (at DESC);
CREATE INDEX audit_log_entity ON audit_log (entity, entity_id);

CREATE TRIGGER audit_log_append_only
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION append_only_guard();
