#!/usr/bin/env bash
# Zero-to-API proof: build everything on a NEW Lakebase project, run every test, then delete it all.
#   scripts/ci_proof.sh                 # uses ./.env for profile, catalog, warehouse, caller SP
#   ENV_FILE=/path/.env KEEP=1 scripts/ci_proof.sh   # KEEP=1: leave the project up to debug
# Works on a copy of the repo in a temp dir, so your .env and data/ are not touched.
# Creates: project lps-ci-<rand>, UC schemas <catalog>.lps_ci_<rand> and <catalog>.payer_serving.
# The payer_serving UC schema must not already exist in that catalog (the script stops if it does).
set -uo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE=${ENV_FILE:-$SRC/.env}
R=$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 6)
PROJ=lps-ci-$R
UCS=lps_ci_$R
W=$(mktemp -d)/repo
PROFILE=$(grep '^DATABRICKS_PROFILE=' "$ENV_FILE" | cut -d= -f2)
CAT=$(grep '^UC_CATALOG=' "$ENV_FILE" | cut -d= -f2)
step() { echo; echo "######## $(date +%H:%M:%S) $*"; }

teardown() {
  [ -n "${KEEP:-}" ] && { echo "KEEP=1: left $PROJ, $CAT.$UCS, $CAT.payer_serving in place"; return; }
  step "teardown"
  cd "$W" 2>/dev/null || return
  # Project first: deleting the synced table first fails while views depend on it.
  databricks bundle destroy --auto-approve -p "$PROFILE" --var "lakebase_project=$PROJ" >/dev/null 2>&1
  databricks postgres delete-project "projects/$PROJ" -p "$PROFILE" >/dev/null 2>&1
  # The UC synced table and its pipeline outlive the project; this removes both.
  databricks postgres delete-synced-table "synced_tables/$CAT.payer_serving.insurer_alias" -p "$PROFILE" >/dev/null 2>&1
  uv run python scripts/uc_sql.py "DROP SCHEMA IF EXISTS $CAT.$UCS CASCADE" >/dev/null 2>&1
  uv run python scripts/uc_sql.py "DROP SCHEMA IF EXISTS $CAT.payer_serving CASCADE" >/dev/null 2>&1
  step "teardown check (expect: none)"
  databricks postgres list-projects -p "$PROFILE" -o json \
    | python3 -c "import json,sys; print('projects:', [p['name'] for p in json.load(sys.stdin) if p['name'].endswith('/$PROJ')] or 'none')"
  databricks pipelines list-pipelines -p "$PROFILE" -o json \
    | python3 -c "import json,sys; print('pipelines:', [p['pipeline_id'] for p in json.load(sys.stdin) if '$CAT.payer_serving.' in p['name']] or 'none')"
  echo "schemas: $(uv run python scripts/uc_sql.py "SHOW SCHEMAS IN $CAT LIKE 'payer_serving|$UCS'" 2>/dev/null | grep -cE 'payer_serving|lps_ci') left"
}
trap teardown EXIT

step "copy repo to $W"
mkdir -p "$W"
rsync -a --exclude .git --exclude .venv --exclude .env --exclude .databricks --exclude graphify-out --exclude data/out "$SRC/" "$W/"
cd "$W"
{ grep -vE '^(LAKEBASE_PROJECT|LAKEBASE_BRANCH|LAKEBASE_ENDPOINT|PG_DATABASE|UC_SCHEMA|DATA_API_BASE)=' "$ENV_FILE"
  echo "LAKEBASE_PROJECT=$PROJ"; echo "LAKEBASE_BRANCH=production"; echo "LAKEBASE_ENDPOINT=primary"
  echo "PG_DATABASE=databricks_postgres"; echo "UC_SCHEMA=$UCS"; } > .env
uv sync -q
if uv run python scripts/uc_sql.py "SHOW SCHEMAS IN $CAT LIKE 'payer_serving'" 2>/dev/null | grep -q payer_serving; then
  echo "STOP: $CAT.payer_serving already exists; use another catalog"; trap - EXIT; exit 1
fi

step "project $PROJ via the bundle (CU 2-16)"
databricks bundle deploy -p "$PROFILE" --var "lakebase_project=$PROJ" 2>&1 | tail -1 || exit 1

step "setup.sh"
ROWS=${ROWS:-200000} scripts/setup.sh || { echo "SETUP FAILED"; exit 1; }

step "Data API url"
BASE=$(uv run python - <<'PY'
import os, sys
sys.path[:0] = ["scripts"]
from uc_sql import load_env; load_env()
from databricks.sdk import WorkspaceClient
from enable_data_api import database_path
e = os.environ
w = WorkspaceClient(profile=e["DATABRICKS_PROFILE"])
db = database_path(w, f"projects/{e['LAKEBASE_PROJECT']}/branches/production", e["PG_DATABASE"])
print(w.postgres.get_data_api(name=f"{db}/data-api").status.url)
PY
)
[ -n "$BASE" ] || exit 1
echo "DATA_API_BASE=$BASE" >> .env
sleep 20   # Data API schema cache

step "tests"
uv run pytest -q -m 'not live' 2>&1 | tail -1
uv run pytest -q -s -m live 2>&1 | grep -E 'Wider-word|^FAILED|passed|failed' | tail -8
