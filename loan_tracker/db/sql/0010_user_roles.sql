-- Phase 16: read-only logins. OWNER can change everything; VIEWER can only look.
-- session_epoch changes on password reset or disable: sessions carrying an
-- older epoch are logged out on their next request.
ALTER TABLE app_user
  ADD COLUMN role text NOT NULL DEFAULT 'OWNER' CHECK (role IN ('OWNER', 'VIEWER')),
  ADD COLUMN disabled_at timestamptz,
  ADD COLUMN session_epoch int NOT NULL DEFAULT 1 CHECK (session_epoch >= 1);
