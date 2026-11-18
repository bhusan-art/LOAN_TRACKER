-- Phase 14: documents (ID proofs, agreements, cheque photos) attached to a
-- person and/or a loan. The file itself is AES-GCM encrypted on disk
-- (DOCUMENT_DIR/<uuid>.ltd); this table only describes it. Never deleted:
-- deleting a document erases the file and records who deleted it and when.
CREATE TABLE document (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  uuid          text NOT NULL UNIQUE CHECK (uuid ~ '^[0-9a-f]{32}$'),
  party_id      bigint REFERENCES party (id),
  loan_id       bigint REFERENCES loan (id),
  kind          text NOT NULL CHECK (kind IN ('ID_PROOF', 'AGREEMENT', 'CHEQUE', 'PHOTO', 'OTHER')),
  title         text NOT NULL CHECK (length(trim(title)) > 0),
  file_name     text NOT NULL,
  content_type  text NOT NULL CHECK (content_type IN ('application/pdf', 'image/jpeg', 'image/png', 'image/webp')),
  size_bytes    int NOT NULL CHECK (size_bytes > 0),
  sha256        text NOT NULL,
  key_id        text NOT NULL,
  uploaded_by   text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz,
  deleted_by    text,
  CHECK (party_id IS NOT NULL OR loan_id IS NOT NULL)
);
CREATE INDEX document_party ON document (party_id) WHERE deleted_at IS NULL;
CREATE INDEX document_loan ON document (loan_id) WHERE deleted_at IS NULL;

CREATE OR REPLACE FUNCTION document_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'document records are kept; delete the document in the app instead';
  END IF;
  IF OLD.deleted_at IS NOT NULL OR NEW.deleted_at IS NULL
     OR (to_jsonb(NEW) - 'deleted_at' - 'deleted_by') IS DISTINCT FROM (to_jsonb(OLD) - 'deleted_at' - 'deleted_by') THEN
    RAISE EXCEPTION 'a document record can only be marked deleted, once';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER document_only_delete_mark
  BEFORE UPDATE OR DELETE ON document
  FOR EACH ROW EXECUTE FUNCTION document_guard();
