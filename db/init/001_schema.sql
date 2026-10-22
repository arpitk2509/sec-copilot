CREATE EXTENSION IF NOT EXISTS vector;

-- One row per company
CREATE TABLE companies (
    cik     INTEGER PRIMARY KEY,      -- the SEC's ID for a company
    ticker  TEXT UNIQUE NOT NULL,
    name    TEXT NOT NULL,
    sector  TEXT
);

-- One row per SEC filing (10-K, 10-Q)
CREATE TABLE filings (
    accession_no TEXT PRIMARY KEY,    -- the SEC's unique ID for a filing
    cik          INTEGER NOT NULL REFERENCES companies(cik),
    form_type    TEXT NOT NULL,       -- '10-K' or '10-Q'
    filing_date  DATE NOT NULL,
    period_end   DATE,
    source_url   TEXT
);

-- The numbers: one row per metric per fiscal period (revenue, net income, ...)
CREATE TABLE financial_facts (
    id            BIGSERIAL PRIMARY KEY,
    cik           INTEGER NOT NULL REFERENCES companies(cik),
    metric        TEXT NOT NULL,
    period_start  DATE,
    period_end    DATE NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    fiscal_period TEXT NOT NULL,      -- 'Q1', 'Q2', 'Q3', 'Q4', 'FY'
    value         NUMERIC NOT NULL,
    unit          TEXT NOT NULL DEFAULT 'USD',
    accession_no  TEXT,
    is_derived    BOOLEAN NOT NULL DEFAULT FALSE,   -- TRUE for Q4, which is computed, not reported
    UNIQUE (cik, metric, fiscal_year, fiscal_period)
);

-- The text: one row per chunk of a filing section
CREATE TABLE doc_chunks (
    id           BIGSERIAL PRIMARY KEY,
    accession_no TEXT REFERENCES filings(accession_no),
    cik          INTEGER NOT NULL REFERENCES companies(cik),
    section      TEXT NOT NULL,       -- 'Item 1A' (risks) or 'Item 7' (MD&A)
    fiscal_year  INTEGER,
    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL
);

-- Indexes make filtered lookups fast
CREATE INDEX idx_facts_lookup ON financial_facts (cik, metric, period_end);
CREATE INDEX idx_chunks_filter ON doc_chunks (cik, fiscal_year, section);