-- Make doc_chunks safe to re-load, and searchable by keyword.
-- Every statement is idempotent, so this file can be run on a new or an existing database.

-- One chunk per (filing, section, position): lets the loader "upsert" instead of duplicating.
CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_chunks_position
    ON doc_chunks (accession_no, section, chunk_index);

-- Keyword search column: Postgres keeps it in sync with `content` automatically.
ALTER TABLE doc_chunks
    ADD COLUMN IF NOT EXISTS content_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;

CREATE INDEX IF NOT EXISTS idx_doc_chunks_tsv ON doc_chunks USING GIN (content_tsv);