-- Vector search: one 768-number embedding per chunk (Google gemini-embedding-001, truncated to 768 dimensions).
-- Idempotent: safe to run on a new or an existing database.
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS embedding vector(768);
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT;

-- HNSW index = fast "closest vectors" lookup. vector_cosine_ops = compare by cosine distance.
CREATE INDEX IF NOT EXISTS idx_doc_chunks_embedding
    ON doc_chunks USING hnsw (embedding vector_cosine_ops);