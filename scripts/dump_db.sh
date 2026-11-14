#!/usr/bin/env bash
# Save the database contents to a compressed file (data only: the tables are created by db/init).
# Usage: scripts/dump_db.sh [output-file]
set -euo pipefail

# Commands run inside the database container. Set DB_EXEC to an empty string to use a local Postgres instead.
DB_EXEC="${DB_EXEC-docker compose exec -T db}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_NAME="${POSTGRES_DB:-sec_copilot}"
OUT="${1:-data/snapshots/sec-copilot-$(date +%Y-%m-%d).sql.gz}"

mkdir -p "$(dirname "$OUT")"
$DB_EXEC pg_dump -U "$DB_USER" -d "$DB_NAME" --data-only --no-owner --no-privileges | gzip > "$OUT"
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"

$DB_EXEC psql -U "$DB_USER" -d "$DB_NAME" -At -F ' ' -c "
  SELECT 'companies:', COUNT(*)::text FROM companies
  UNION ALL SELECT 'filings:', COUNT(*)::text FROM filings
  UNION ALL SELECT 'financial_facts:', COUNT(*)::text FROM financial_facts
  UNION ALL SELECT 'doc_chunks:', COUNT(*)::text FROM doc_chunks
  UNION ALL SELECT 'chunks with embeddings:', COUNT(*)::text FROM doc_chunks WHERE embedding IS NOT NULL
  UNION ALL SELECT 'latest quarter:', MAX(period_end)::text FROM financial_facts WHERE fiscal_period <> 'FY'"