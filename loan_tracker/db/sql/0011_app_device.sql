-- Phase 18: phones signed in to the Android app. Each sign-in gets a random token; only a
-- SHA-256 hash is stored, so the database (or a backup) cannot be used to sign in.
-- session_epoch copies the user's epoch at sign-in: a password reset or disabling the user
-- ends every phone. Never deleted; signing a phone out is a one-time mark.
CREATE TABLE app_device (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id        bigint NOT NULL REFERENCES app_user (id),
  name           text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  token_hash     text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  session_epoch  int NOT NULL CHECK (session_epoch >= 1),
  created_at     timestamptz NOT NULL DEFAULT now(),
  last_used_at   timestamptz,
  revoked_at     timestamptz,
  revoked_by     text
);
CREATE INDEX app_device_user ON app_device (user_id);

-- what may change: last use, and a one-time sign-out
CREATE OR REPLACE FUNCTION app_device_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'signed-in phones are kept; sign them out instead';
  END IF;
  IF (to_jsonb(NEW) - 'last_used_at' - 'revoked_at' - 'revoked_by')
       IS DISTINCT FROM (to_jsonb(OLD) - 'last_used_at' - 'revoked_at' - 'revoked_by')
     OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
    RAISE EXCEPTION 'a phone can only be signed out once or have its last use updated';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER app_device_limited_update
  BEFORE UPDATE OR DELETE ON app_device
  FOR EACH ROW EXECUTE FUNCTION app_device_guard();
