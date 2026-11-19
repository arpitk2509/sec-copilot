#!/usr/bin/env bash
# Load a snapshot made by dump_db.sh into an EMPTY database.
# Usage: scripts/restore_db.sh <snapshot.sql.gz>
# To load into a remote database (for example on Neon), set DB_URL to its connection string.
set -euo pipefail

if [ $# -ne 1 ] || [ ! -f "$1" ]; then
  echo "Usage: scripts/restore_db.sh <snapshot.sql.gz>" >&2
  exit 2
fi

DB_EXEC="${DB_EXEC-docker compose exec -T db}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_NAME="${POSTGRES_DB:-sec_copilot}"
if [ "${DB_URL+set}" = "set" ]; then
  # DB_URL was given. If it is empty (for example from an unset variable), stop instead of quietly using the LOCAL database.
  [ -n "$DB_URL" ] || { echo "DB_URL is set but empty, so nothing was done. Check the variable you passed." >&2; exit 2; }
  TARGET=(-d "$DB_URL")
else
  TARGET=(-U "$DB_USER" -d "$DB_NAME")
fi
psql_cmd() { $DB_EXEC psql "${TARGET[@]}" "$@"; }

existing=$(psql_cmd -At -c "SELECT (SELECT COUNT(*) FROM companies) + (SELECT COUNT(*) FROM financial_facts) + (SELECT COUNT(*) FROM doc_chunks)")
if [ "$existing" != "0" ]; then
  echo "The database already holds $existing rows. Restoring on top would create duplicates, so nothing was changed." >&2
  echo "Use a fresh database (docker compose up -d db on a new volume), or ask for help before deleting anything." >&2
  exit 1
fi

gunzip -c "$1" | psql_cmd -v ON_ERROR_STOP=1 -q -o /dev/null

# A data-only load does not move the id counters, so move them past the loaded rows.
psql_cmd -q -c "
  SELECT setval(pg_get_serial_sequence('financial_facts', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM financial_facts;
  SELECT setval(pg_get_serial_sequence('doc_chunks', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM doc_chunks;" > /dev/null

echo "Restored $1:"
psql_cmd -At -F ' ' -c "
  SELECT 'companies:', COUNT(*)::text FROM companies
  UNION ALL SELECT 'filings:', COUNT(*)::text FROM filings
  UNION ALL SELECT 'financial_facts:', COUNT(*)::text FROM financial_facts
  UNION ALL SELECT 'doc_chunks:', COUNT(*)::text FROM doc_chunks
  UNION ALL SELECT 'chunks with embeddings:', COUNT(*)::text FROM doc_chunks WHERE embedding IS NOT NULL
  UNION ALL SELECT 'latest quarter:', MAX(period_end)::text FROM financial_facts WHERE fiscal_period <> 'FY'"