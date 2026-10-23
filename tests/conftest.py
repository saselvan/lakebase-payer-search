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


@pytest.fixture(scope="session")
def caller_sql():
    """Run SQL as the Caller service principal over a direct Postgres login (not the Data API)."""
    def run(sql: str) -> list[list[str]]:
        out = subprocess.run([str(ROOT / "scripts/psql.sh"), "-At", "-F", "\t", "-c", sql],
                             capture_output=True, text=True, check=True,
                             env={**os.environ, "AS_CALLER": "1"}).stdout
        return [line.split("\t") for line in out.splitlines() if line]
    return run


def throwaway_branch_refusal(branch: str | None) -> str | None:
    """Why a writing test must NOT run on this BRANCH, or None if it may. Pure, so it is unit-tested offline."""
    b = branch or ""
    if not b.startswith("t-") or b == "t-" or any(c.isspace() for c in b):
        return f"writes: needs BRANCH=t-<id> (a throwaway branch), got {b!r}"
    return None


@pytest.fixture(scope="session")
def throwaway_branch():
    """Guard for every test that plants objects, drops functions or grants rights. Returns the branch id."""
    why = throwaway_branch_refusal(os.environ.get("BRANCH"))
    if why:
        pytest.skip(why)
    return os.environ["BRANCH"]
