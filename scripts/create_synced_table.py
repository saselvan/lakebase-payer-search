"""Create the SNAPSHOT synced table: UC Delta insurer_alias -> Lakebase schema payer_serving.

Idempotent: an existing synced table is left as is. Waits until the first sync is online,
at most SYNC_TIMEOUT_S seconds (default 3600).
Usage: uv run python scripts/create_synced_table.py
"""
import os
import sys
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import postgres as pg

sys.path.insert(0, os.path.dirname(__file__))
from uc_sql import load_env, run  # noqa: E402

SERVING_SCHEMA = "payer_serving"
SYNC_TIMEOUT_S = int(os.environ.get("SYNC_TIMEOUT_S", "3600"))


def main() -> None:
    load_env()
    e = os.environ
    w = WorkspaceClient(profile=e["DATABRICKS_PROFILE"])
    source = f"{e['UC_CATALOG']}.{e['UC_SCHEMA']}.insurer_alias"
    dest = f"{e['UC_CATALOG']}.{SERVING_SCHEMA}.insurer_alias"
    run(f"CREATE SCHEMA IF NOT EXISTS {e['UC_CATALOG']}.{SERVING_SCHEMA}")
    spec = pg.SyncedTableSyncedTableSpec(
        source_table_full_name=source,
        primary_key_columns=["alias_id"],
        scheduling_policy=pg.SyncedTableSyncedTableSpecSyncedTableSchedulingPolicy.SNAPSHOT,
        branch=f"projects/{e['LAKEBASE_PROJECT']}/branches/{e['LAKEBASE_BRANCH']}",
        postgres_database=e["PG_DATABASE"],
        create_database_objects_if_missing=True,
    )
    try:
        w.postgres.create_synced_table(synced_table=pg.SyncedTable(spec=spec), synced_table_id=dest)
        print(f"created {dest}")
    except Exception as ex:  # noqa: BLE001
        if "already exists" not in str(ex).lower():
            raise
        print(f"exists {dest}")
    t0 = time.time()
    while True:
        st = w.postgres.get_synced_table(name=f"synced_tables/{dest}").status
        state = str(getattr(st, "detailed_state", None) or st)
        print(f"{time.time() - t0:5.0f}s {state[:120]}", flush=True)
        if "ONLINE" in state and "FAILED" not in state:
            return
        if "FAILED" in state:
            raise SystemExit(state)
        if time.time() - t0 > SYNC_TIMEOUT_S:
            raise SystemExit(f"sync not online after {SYNC_TIMEOUT_S}s; last state: {state[:200]}")
        time.sleep(20)


if __name__ == "__main__":
    main()
