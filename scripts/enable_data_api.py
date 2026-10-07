"""Turn on the Lakebase Data API for our database, exposing ONLY schema `api`.

Idempotent: creates the Data API config, or updates it if it exists. Prints the REST base URL.
Usage: uv run python scripts/enable_data_api.py
"""
import os
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service import postgres as pg

sys.path.insert(0, os.path.dirname(__file__))
from uc_sql import load_env  # noqa: E402

EXPOSED = ["api"]
MAX_ROWS = 50


def database_path(w: WorkspaceClient, branch: str, pg_name: str) -> str:
    # The resource id differs from the Postgres name (databricks-postgres vs databricks_postgres).
    for d in w.postgres.list_databases(parent=branch):
        if d.status and d.status.postgres_database == pg_name:
            return d.name
    raise SystemExit(f"no database {pg_name} on {branch}")


def main() -> None:
    load_env()
    e = os.environ
    w = WorkspaceClient(profile=e["DATABRICKS_PROFILE"])
    branch = f"projects/{e['LAKEBASE_PROJECT']}/branches/{e['LAKEBASE_BRANCH']}"
    db = database_path(w, branch, e["PG_DATABASE"])
    spec = pg.DataApiDataApiSpec(db_schemas=EXPOSED, db_max_rows=MAX_ROWS)
    try:
        cur = w.postgres.get_data_api(name=f"{db}/data-api")
        if cur.spec and cur.spec.db_schemas == EXPOSED and cur.spec.db_max_rows == MAX_ROWS:
            print("data api: already configured:", cur.status.url)
            return
        # SDK update_data_api cannot take a string update_mask (it wants a protobuf FieldMask): use REST.
        res = w.api_client.do("PATCH", f"/api/2.0/postgres/{db}/data-api",
                              query={"update_mask": "spec.db_schemas,spec.db_max_rows"},
                              body={"spec": {"db_schemas": EXPOSED, "db_max_rows": MAX_ROWS}})
        print("data api: updated:", res)
        return
    except NotFound:  # "Data API is not enabled for this database"
        op = w.postgres.create_data_api(parent=db, data_api=pg.DataApi(spec=spec))
    res = op.wait() if hasattr(op, "wait") else op
    print("data api:", res)


if __name__ == "__main__":
    main()
