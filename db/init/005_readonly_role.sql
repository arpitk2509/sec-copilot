-- A database user for LLM-written queries. It can read two views and nothing else,
-- cannot write even by accident, and every query is stopped after 5 seconds.
-- The password below is for LOCAL DEVELOPMENT ONLY. In any shared deployment, create the role with a real secret.
-- Idempotent: safe to run again.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_readonly') THEN
        CREATE ROLE copilot_readonly LOGIN PASSWORD 'readonly';
    END IF;
END $$;

ALTER ROLE copilot_readonly WITH LOGIN PASSWORD 'readonly' CONNECTION LIMIT 5;
ALTER ROLE copilot_readonly SET statement_timeout = '5s';
ALTER ROLE copilot_readonly SET default_transaction_read_only = on;
ALTER ROLE copilot_readonly SET idle_in_transaction_session_timeout = '10s';

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO copilot_readonly', current_database());
END $$;

GRANT USAGE ON SCHEMA public TO copilot_readonly;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM copilot_readonly;   -- start from nothing...
GRANT SELECT ON v_quarterly_metrics, companies TO copilot_readonly; -- ...then allow only these two