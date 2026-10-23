#!/usr/bin/env bash
# Run psql against the Lakebase endpoint as the logged-in admin user (OAuth token, never printed).
# Usage: scripts/psql.sh [psql args...]   e.g. scripts/psql.sh -f sql/01_extensions.sql
#        BRANCH=<branch-id> scripts/psql.sh ...   runs against a test branch instead of LAKEBASE_BRANCH
#        AS_CALLER=1 scripts/psql.sh ...          logs in as the Caller service principal, not the admin
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
EP="projects/${LAKEBASE_PROJECT}/branches/${BRANCH:-$LAKEBASE_BRANCH}/endpoints/${LAKEBASE_ENDPOINT}"   # BRANCH=<id> targets a test branch
HOST=$(databricks postgres get-endpoint "$EP" -p "$DATABRICKS_PROFILE" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"]["hosts"]["host"])')
if [ "${AS_CALLER:-}" = 1 ]; then   # AS_CALLER=1: log in as the Caller service principal (M2M secret from .env)
  export PGPASSWORD=$(env -u DATABRICKS_CONFIG_PROFILE DATABRICKS_CONFIG_FILE=/dev/null DATABRICKS_HOST="https://${WORKSPACE_HOST#https://}" \
    DATABRICKS_CLIENT_ID="$SP_APPLICATION_ID" DATABRICKS_CLIENT_SECRET="$SP_CLIENT_SECRET" \
    databricks postgres generate-database-credential "$EP" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
  USER_NAME=$SP_APPLICATION_ID
else
  export PGPASSWORD=$(databricks postgres generate-database-credential "$EP" -p "$DATABRICKS_PROFILE" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
  USER_NAME=$(databricks current-user me -p "$DATABRICKS_PROFILE" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["userName"])')
fi
exec psql "host=$HOST port=5432 dbname=$PG_DATABASE user=$USER_NAME sslmode=require" -v ON_ERROR_STOP=1 "$@"
