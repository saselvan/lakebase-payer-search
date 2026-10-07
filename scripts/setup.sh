#!/usr/bin/env bash
# End-to-end setup. Every step is idempotent; run it again after any change.
#   0 Lakebase Search on  1 generate data  2 load the Delta table  3 sync to Lakebase  4 search index
#   5 search function 6 Data API (expose schema api only)   7 caller grants
# Needs .env (copy .env.example). Steps can be skipped: SKIP_DATA=1 scripts/setup.sh
# Re-runs refresh the search index with no downtime; REBUILD=1 drops and rebuilds it (short outage).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
ROWS=${ROWS:-3000000}
T="$UC_CATALOG.$UC_SCHEMA.insurer_alias"
VOL="/Volumes/$UC_CATALOG/$UC_SCHEMA/landing/insurer_alias"

# 0. Lakebase Search must be on for the project before lakebase_text can load (it is a preload
#    library). Same call as the UI button "Enable Lakebase Search". The first call restarts the
#    project's computes (drops open connections), so it runs before anything connects; later
#    calls return done at once. It cannot be turned off.
echo "== 0 Lakebase Search on project $LAKEBASE_PROJECT"
OP=$(databricks api post "/api/2.0/postgres/projects/$LAKEBASE_PROJECT/search-extensions" --json '{}' \
       -p "$DATABRICKS_PROFILE" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("" if d.get("done") else d["name"])')
for _ in $(seq 1 60); do
  [ -z "$OP" ] && break
  databricks api get "/api/2.0/postgres/$OP" -p "$DATABRICKS_PROFILE" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get("done") else 1)' && break
  sleep 5
done

if [ -z "${SKIP_DATA:-}" ]; then
  echo "== 1 generate $ROWS rows"
  uv run python -m insurer_search.generate --target-rows "$ROWS" --out data/out
  echo "== 2 load $T"
  uv run python scripts/uc_sql.py "CREATE SCHEMA IF NOT EXISTS $UC_CATALOG.$UC_SCHEMA" >/dev/null
  uv run python scripts/uc_sql.py "CREATE VOLUME IF NOT EXISTS $UC_CATALOG.$UC_SCHEMA.landing" >/dev/null
  databricks fs rm -r "dbfs:$VOL" -p "$DATABRICKS_PROFILE" >/dev/null 2>&1 || true
  databricks fs cp -r data/out "dbfs:$VOL" --overwrite -p "$DATABRICKS_PROFILE" >/dev/null
  uv run python scripts/uc_sql.py "CREATE TABLE IF NOT EXISTS $T (alias_id BIGINT NOT NULL, payer_id STRING NOT NULL, payer_name STRING NOT NULL, alias STRING NOT NULL, alias_type STRING NOT NULL, state STRING NOT NULL, plan_type STRING NOT NULL, CONSTRAINT insurer_alias_pk PRIMARY KEY (alias_id)) COMMENT 'Synthetic payer aliases for the insurer search demo. Not real payer IDs.'" >/dev/null
  uv run python scripts/uc_sql.py "INSERT OVERWRITE $T SELECT alias_id, payer_id, payer_name, alias, alias_type, state, plan_type FROM read_files('$VOL/', format => 'parquet')" >/dev/null
  uv run python scripts/uc_sql.py "SELECT count(*), count(DISTINCT payer_id) FROM $T"
  echo "== 3 sync to Lakebase"
  if uv run python -c "import sys; sys.path.insert(0,'scripts'); from uc_sql import load_env; load_env(); import os; from databricks.sdk import WorkspaceClient as W; W(profile=os.environ['DATABRICKS_PROFILE']).postgres.get_synced_table(name=f\"synced_tables/{os.environ['UC_CATALOG']}.payer_serving.insurer_alias\")" 2>/dev/null; then
    uv run python scripts/refresh_sync.py
  else
    uv run python scripts/create_synced_table.py
  fi
fi
echo "== 4 search index";    scripts/psql.sh -q ${REBUILD:+-v rebuild=1} -f sql/10_search_index.sql
echo "== 5 search function"; scripts/psql.sh -q -f sql/20_search_function.sql
echo "== 6 Data API";        uv run python scripts/enable_data_api.py >/dev/null
echo "== 7 caller grants";   scripts/psql.sh -q -v sp="$SP_APPLICATION_ID" -f sql/40_caller_grants.sql
scripts/psql.sh -At -c "NOTIFY pgrst, 'reload schema'" \
  -c "SELECT 'synced rows: ' || count(*) FROM payer_serving.insurer_alias" \
  -c "SELECT 'search keys: ' || count(*) || ' for ' || count(DISTINCT payer_id) || ' payers' FROM search_idx.alias_key"
echo "== done"
