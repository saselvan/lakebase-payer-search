#!/usr/bin/env bash
# psql on a throwaway t-finder-* branch, as the logged-in project owner (OAuth token, never printed).
# Usage: scripts/psql.sh [psql args...]      BRANCH comes from .env or the environment; no default.
# Refuses any branch that is not t-finder-*: this script must never reach production.
set -euo pipefail
cd "$(dirname "$0")/.."
BRANCH_OVERRIDE="${BRANCH:-}"
set -a; source .env; set +a
BRANCH="${BRANCH_OVERRIDE:-${BRANCH:-}}"
if [[ ! "$BRANCH" =~ ^t-finder-[a-z0-9-]+$ ]]; then
  echo "psql.sh: refusing branch '${BRANCH}': only t-finder-* branches" >&2
  exit 3
fi
EP="projects/${LAKEBASE_PROJECT}/branches/${BRANCH}/endpoints/${LAKEBASE_ENDPOINT}"
HOST=$(databricks postgres get-endpoint "$EP" -p "$DATABRICKS_PROFILE" -o json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"]["hosts"]["host"])')
export PGPASSWORD=$(databricks postgres generate-database-credential "$EP" -p "$DATABRICKS_PROFILE" -o json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
USER_NAME=$(databricks current-user me -p "$DATABRICKS_PROFILE" -o json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["userName"])')
exec psql "host=$HOST port=5432 dbname=$PG_DATABASE user=$USER_NAME sslmode=require application_name=finder-$BRANCH" \
  -v ON_ERROR_STOP=1 "$@"
