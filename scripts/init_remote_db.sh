#!/usr/bin/env bash
# Prepare an EMPTY remote database (for example on Neon): create the tables and views, then the read-only role
# with a real password. Run it once, then load the data with scripts/restore_db.sh.
# Usage: DB_URL='postgresql://...' RO_PASSWORD='...' scripts/init_remote_db.sh [--role-only]
#   --role-only   skip the tables and only create the read-only role (to finish an init that stopped at that step)
set -euo pipefail

: "${DB_URL:?Set DB_URL to the database connection string (the pooled one that Neon shows by default is fine)}"
: "${RO_PASSWORD:?Set RO_PASSWORD to the password the read-only role should get}"
DB_EXEC="${DB_EXEC-docker compose exec -T db}"

if [ $# -gt 1 ] || { [ $# -eq 1 ] && [ "$1" != "--role-only" ]; }; then
  echo "Usage: DB_URL=... RO_PASSWORD=... scripts/init_remote_db.sh [--role-only]" >&2
  exit 2
fi
# The password is written into SQL text, so only characters that are safe there are allowed.
if ! printf '%s' "$RO_PASSWORD" | grep -Eq '^[A-Za-z0-9_-]{16,}$'; then
  echo "RO_PASSWORD must be at least 16 characters, using only letters, digits, - and _ (python's secrets.token_urlsafe does)." >&2
  exit 2
fi

if [ "${1:-}" != "--role-only" ]; then
  for file in db/init/00[1-4]*.sql; do
    echo "applying $file"
    $DB_EXEC psql -d "$DB_URL" -v ON_ERROR_STOP=1 -q -o /dev/null -f - < "$file"
  done
fi

# The role script contains a development password, which a cloud database rejects as too weak.
# So the real password is put into the script's text before it is sent.
role_sql=$(sed "s/'readonly'/'$RO_PASSWORD'/g" db/init/005_readonly_role.sql)
case "$role_sql" in
  *"PASSWORD '$RO_PASSWORD'"*) ;;
  *) echo "Could not place the password into db/init/005_readonly_role.sql (has the file changed?)." >&2; exit 1 ;;
esac
echo "applying db/init/005_readonly_role.sql (with your password)"
printf '%s\n' "$role_sql" | $DB_EXEC psql -d "$DB_URL" -v ON_ERROR_STOP=1 -q -o /dev/null -f -

echo "Done: tables, views and the read-only role are in place."
echo "Next: DB_URL='...' scripts/restore_db.sh <snapshot.sql.gz>"