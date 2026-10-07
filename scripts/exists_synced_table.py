"""Does the synced table exist? Exit 0 = yes, 3 = no (NotFound). Any other error is raised.

Usage: uv run python scripts/exists_synced_table.py
"""
import os
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

sys.path.insert(0, os.path.dirname(__file__))
from uc_sql import load_env  # noqa: E402


def main() -> None:
    load_env()
    e = os.environ
    w = WorkspaceClient(profile=e["DATABRICKS_PROFILE"])
    try:
        w.postgres.get_synced_table(name=f"synced_tables/{e['UC_CATALOG']}.payer_serving.insurer_alias")
    except NotFound:
        sys.exit(3)


if __name__ == "__main__":
    main()
