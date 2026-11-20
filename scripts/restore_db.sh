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

gunzip -t "$1" || { echo "The snapshot file is damaged (it failed gunzip's integrity check). Nothing was changed." >&2; exit 1; }

# Loading rows into a vector index one at a time is slow, and a small cloud database can drop the connection while
# it works. So: take the vector indexes away, load everything, rebuild them once, all in ONE transaction.
# If anything goes wrong, nothing at all is kept and the database is exactly as empty as before.
hnsw="schemaname = 'public' AND indexdef LIKE '%USING hnsw%'"
index_names=$(psql_cmd -At -c "SELECT indexname FROM pg_indexes WHERE $hnsw")
index_defs=$(psql_cmd -At -c "SELECT indexdef FROM pg_indexes WHERE $hnsw")
echo "Loading (all-or-nothing, so stopping with Ctrl-C is safe). This can take a few minutes..."
trap 'echo "The load failed after ${SECONDS} seconds. Nothing was kept: the database is as empty as before, so you can simply try again." >&2' ERR
{
  for name in $index_names; do echo "DROP INDEX public.\"$name\";"; done
  gunzip -c "$1"
  echo "SET search_path = public;"      # the dump empties it, and the index definitions below need to find vector_cosine_ops
  while IFS= read -r definition; do
    if [ -n "$definition" ]; then echo "$definition;"; fi
  done <<< "$index_defs"
} | psql_cmd -v ON_ERROR_STOP=1 -q -1 -o /dev/null
trap - ERR

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