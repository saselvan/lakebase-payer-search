#!/usr/bin/env bash
# Read-only psql on production or a throwaway t-finder-* branch, as the logged-in project owner (I2-I4).
# Usage: TARGET=production scripts/psql_ro.sh [psql args...]      TARGET has no default.
# Every session starts with default_transaction_read_only=on, so a write fails with SQLSTATE 25006.
# The first output line is "host=<endpoint host>" (psql's own :HOST), so a caller can check which database it reached.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${TARGET:-}"
set -a; source .env; set +a
if [[ "$TARGET" != "production" && ! "$TARGET" =~ ^t-finder-[a-z0-9-]+$ ]]; then
  echo "psql_ro.sh: refusing target '${TARGET}': only production or t-finder-*" >&2
  exit 3
fi
EP="projects/${LAKEBASE_PROJECT}/branches/${TARGET}/endpoints/${LAKEBASE_ENDPOINT}"
HOST=$(databricks postgres get-endpoint "$EP" -p "$DATABRICKS_PROFILE" -o json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"]["hosts"]["host"])')
export PGPASSWORD=$(databricks postgres generate-database-credential "$EP" -p "$DATABRICKS_PROFILE" -o json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
USER_NAME=$(databricks current-user me -p "$DATABRICKS_PROFILE" -o json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["userName"])')
export PGOPTIONS="-c default_transaction_read_only=on"
exec psql "host=$HOST port=5432 dbname=$PG_DATABASE user=$USER_NAME sslmode=require application_name=finder-ro-$TARGET" \
  -v ON_ERROR_STOP=1 -c '\echo host=:HOST' "$@"
