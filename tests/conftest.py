import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.split("#")[0].strip())


_load_env()


@pytest.fixture(scope="session")
def client():
    if not os.environ.get("DATA_API_BASE"):
        pytest.skip("live: DATA_API_BASE not set")
    from insurer_search.client import InsurerSearchClient
    return InsurerSearchClient.from_env()


@pytest.fixture(scope="session")
def admin_sql():
    """Run SQL as the project owner (for checks the caller must NOT be able to do)."""
    def run(sql: str) -> list[list[str]]:
        out = subprocess.run([str(ROOT / "scripts/psql.sh"), "-At", "-F", "\t", "-c", sql],
                             capture_output=True, text=True, check=True).stdout
        return [line.split("\t") for line in out.splitlines() if line]
    return run
