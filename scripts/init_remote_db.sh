#!/usr/bin/env bash
# Prepare an EMPTY remote database (for example on Neon): create the tables and views, then the read-only role
# with a real password. Run it once, then load the data with scripts/restore_db.sh.
# Usage: DB_URL='postgresql://...' RO_PASSWORD='...' scripts/init_remote_db.sh
set -euo pipefail

: "${DB_URL:?Set DB_URL to the database connection string (the direct one, not the pooled one)}"
: "${RO_PASSWORD:?Set RO_PASSWORD to the password the read-only role should get}"
DB_EXEC="${DB_EXEC-docker compose exec -T db}"

for file in db/init/00[1-4]*.sql db/init/005_readonly_role.sql; do
  echo "applying $file"
  $DB_EXEC psql -d "$DB_URL" -v ON_ERROR_STOP=1 -q -o /dev/null -f - < "$file"
done

# The role script uses a development password. Replace it now, before the database is used.
echo "ALTER ROLE copilot_readonly WITH PASSWORD :'ro_password';" \
  | $DB_EXEC psql -d "$DB_URL" -v ON_ERROR_STOP=1 -q -v "ro_password=$RO_PASSWORD"

echo "Done: tables, views and the read-only role are in place."
echo "Next: DB_URL='...' scripts/restore_db.sh <snapshot.sql.gz>"