-- Phase 15: private statement links for borrowers. Only a SHA-256 hash of the
-- token is stored, so the database (or a backup of it) cannot be turned back
-- into a working link. Never deleted; revoking is a one-time mark.
CREATE TABLE share_link (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  token_hash      text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  loan_id         bigint NOT NULL REFERENCES loan (id),
  created_by      text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  expires_at      timestamptz NOT NULL,
  revoked_at      timestamptz,
  revoked_by      text,
  view_count      int NOT NULL DEFAULT 0 CHECK (view_count >= 0),
  last_viewed_at  timestamptz,
  CHECK (expires_at > created_at)
);
CREATE INDEX share_link_loan ON share_link (loan_id);

-- what may change: a one-time revoke, and the view counter going up
CREATE OR REPLACE FUNCTION share_link_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'statement links are kept; revoke them instead';
  END IF;
  IF (to_jsonb(NEW) - 'revoked_at' - 'revoked_by' - 'view_count' - 'last_viewed_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'revoked_at' - 'revoked_by' - 'view_count' - 'last_viewed_at')
     OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
     OR NEW.view_count < OLD.view_count THEN
    RAISE EXCEPTION 'a statement link can only be revoked once or have its views counted';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER share_link_limited_update
  BEFORE UPDATE OR DELETE ON share_link
  FOR EACH ROW EXECUTE FUNCTION share_link_guard();
