"""Full-refresh the SNAPSHOT synced table after the Delta source changed, and wait until online.
Usage: uv run python scripts/refresh_sync.py
"""
import os
import sys
import time

from databricks.sdk import WorkspaceClient

sys.path.insert(0, os.path.dirname(__file__))
from create_synced_table import SERVING_SCHEMA  # noqa: E402
from uc_sql import load_env  # noqa: E402


def main() -> None:
    load_env()
    e = os.environ
    w = WorkspaceClient(profile=e["DATABRICKS_PROFILE"])
    dest = f"{e['UC_CATALOG']}.{SERVING_SCHEMA}.insurer_alias"
    st = w.postgres.get_synced_table(name=f"synced_tables/{dest}").status
    pipeline_id = st.pipeline_id
    upd = w.pipelines.start_update(pipeline_id, full_refresh=True)
    print(f"full refresh started: pipeline {pipeline_id} update {upd.update_id}", flush=True)
    t0 = time.time()
    while True:
        u = w.pipelines.get_update(pipeline_id, upd.update_id).update
        print(f"{time.time() - t0:5.0f}s {u.state}", flush=True)
        if str(u.state).endswith(("COMPLETED",)):
            return
        if str(u.state).endswith(("FAILED", "CANCELED")):
            raise SystemExit(f"sync {u.state}")
        time.sleep(15)


if __name__ == "__main__":
    main()
