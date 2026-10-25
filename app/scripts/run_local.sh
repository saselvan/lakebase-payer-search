#!/usr/bin/env bash
# Run the app server locally as the app's SP, against the t-finder branch's Data API (read live).
# Usage: scripts/run_local.sh            then open http://127.0.0.1:8000
# Refuses production: the URL is read for BRANCH and compared with production's.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
if [[ ! "$BRANCH" =~ ^t-finder-[a-z0-9-]+$ ]]; then
  echo "run_local.sh: refusing branch '$BRANCH'" >&2; exit 3
fi
DATA_API_BASE=$(uv run python - <<EOF
from databricks.sdk import WorkspaceClient
w = WorkspaceClient(profile="$DATABRICKS_PROFILE")
url = lambda b: w.api_client.do("GET", f"/api/2.0/postgres/projects/$LAKEBASE_PROJECT/branches/{b}/databases/databricks-postgres/data-api")["status"]["url"]
b, p = url("$BRANCH"), url("production")
assert b != p and b != "$DATA_API_BASE_PRODUCTION", "branch URL equals production"
print(b)
EOF
)
export DATA_API_BASE
export DATABRICKS_HOST="$WORKSPACE_HOST" DATABRICKS_CLIENT_ID="$APP_SP_CLIENT_ID" DATABRICKS_CLIENT_SECRET="$APP_SP_CLIENT_SECRET"
export DATABRICKS_APP_PORT="${PORT:-8000}"
unset DATABRICKS_CONFIG_PROFILE
exec uv run python -m server
