"""Run one SQL statement on a Databricks SQL warehouse and print the result rows.

Usage: uv run python scripts/uc_sql.py "SELECT 1"
Reads DATABRICKS_PROFILE and SQL_WAREHOUSE_ID from .env.
"""
import os
import sys
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState


def load_env() -> None:
    for line in Path(__file__).resolve().parents[1].joinpath(".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.split("#")[0].strip())


def run(sql: str) -> list:
    load_env()
    w = WorkspaceClient(profile=os.environ["DATABRICKS_PROFILE"])
    r = w.statement_execution.execute_statement(
        statement=sql, warehouse_id=os.environ["SQL_WAREHOUSE_ID"], wait_timeout="50s")
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(3)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        raise SystemExit(f"{r.status.state}: {r.status.error.message if r.status.error else ''}")
    return r.result.data_array or [] if r.result else []


if __name__ == "__main__":
    for row in run(sys.argv[1]):
        print("\t".join("" if c is None else str(c) for c in row))
