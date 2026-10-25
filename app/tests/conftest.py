"""Live fixtures. Every value that decides *where* a test runs is read live, never trusted from .env:
the branch's Data API URL, production's URL (to refuse it), and the app's service principal id.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


def _dotenv() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def pytest_report_header(config):
    """A live log names the branch it ran on, first thing (T6-29)."""
    if "live" in (config.getoption("markexpr", "") or ""):
        branch = os.environ.get("BRANCH") or _dotenv().get("BRANCH", "<none>")
        return [f"live branch: {branch}", f"read-only target: {os.environ.get('FINDER_TARGET') or branch}"]
    return []


@pytest.fixture(scope="session")
def live_env() -> dict[str, str]:
    env = {**_dotenv(), **{k: v for k, v in os.environ.items() if k == "BRANCH"}}
    if not env.get("APP_SP_CLIENT_SECRET"):
        pytest.skip("live: .env has no app SP secret")
    if not re.fullmatch(r"t-finder-[a-z0-9-]+", env.get("BRANCH", "")):
        pytest.exit(f"refusing to run live tests on branch {env.get('BRANCH')!r}: only t-finder-*", 3)
    return env


@pytest.fixture(scope="session")
def ws(live_env):
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient(profile=live_env["DATABRICKS_PROFILE"])


def _data_api_url(ws, project: str, branch: str) -> str:
    path = f"/api/2.0/postgres/projects/{project}/branches/{branch}/databases/databricks-postgres/data-api"
    return ws.api_client.do("GET", path)["status"]["url"]


@pytest.fixture(scope="session")
def branch_api_base(ws, live_env) -> str:
    branch = _data_api_url(ws, live_env["LAKEBASE_PROJECT"], live_env["BRANCH"])
    production = _data_api_url(ws, live_env["LAKEBASE_PROJECT"], "production")
    if branch.rstrip("/") in {production.rstrip("/"), live_env.get("DATA_API_BASE_PRODUCTION", "").rstrip("/")}:
        pytest.exit("branch Data API URL equals production's: refusing", 3)
    return branch


@pytest.fixture(scope="session")
def app_sp(ws, live_env) -> str:
    """The app's SP application id, as Databricks reports it now."""
    return ws.apps.get(live_env["APP_NAME"]).service_principal_client_id


def fetch_token(live_env) -> str:
    r = httpx.post(f"{live_env['WORKSPACE_HOST']}/oidc/v1/token",
                   auth=(live_env["APP_SP_CLIENT_ID"], live_env["APP_SP_CLIENT_SECRET"]),
                   data={"grant_type": "client_credentials", "scope": "all-apis"}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def app_sp_token(live_env, app_sp) -> str:
    assert live_env["APP_SP_CLIENT_ID"] == app_sp, "the .env secret is not for the app's SP"
    return fetch_token(live_env)


@pytest.fixture(scope="session")
def target(live_env) -> str:
    """Where the read-only checks run: FINDER_TARGET=production, else the t-finder branch (I2, I3)."""
    t = os.environ.get("FINDER_TARGET") or live_env["BRANCH"]
    if t != "production" and not re.fullmatch(r"t-finder-[a-z0-9-]+", t):
        pytest.exit(f"refusing read-only target {t!r}", 3)
    return t


@pytest.fixture(scope="session")
def ro_sql(live_env, ws, target):
    """Run SQL on the target through scripts/psql_ro.sh (read-only sessions). The script's first line names the
    endpoint host it reached; it must be the target's own endpoint, read here through the API (I3)."""
    ep = f"projects/{live_env['LAKEBASE_PROJECT']}/branches/{target}/endpoints/{live_env['LAKEBASE_ENDPOINT']}"
    expected = ws.api_client.do("GET", f"/api/2.0/postgres/{ep}")["status"]["hosts"]["host"]

    def run(sql: str, **variables: str) -> list[list[str]]:
        args = [str(ROOT / "scripts/psql_ro.sh"), "-At", "-F", "\t", "-f", "-"]
        for k, v in variables.items():
            args += ["-v", f"{k}={v}"]
        p = subprocess.run(args, input=sql, capture_output=True, text=True, env={**os.environ, "TARGET": target})
        out = p.stdout.splitlines()
        assert out and out[0] == f"host={expected}", f"the read-only session did not reach the {target} endpoint"
        assert p.returncode == 0, f"read-only SQL failed on {target}: {p.stderr.strip()[-300:]}"
        return [line.split("\t") for line in out[1:] if line]
    return run


@pytest.fixture(scope="session")
def admin_sql(live_env):
    """Run SQL on the t-finder branch as the project owner. psql.sh refuses any other branch.
    SQL goes in on stdin, so psql interpolates :'var' (it does not inside -c)."""
    def run(sql: str, **variables: str) -> list[list[str]]:
        args = [str(ROOT / "scripts/psql.sh"), "-At", "-F", "\t", "-f", "-"]
        for k, v in variables.items():
            args += ["-v", f"{k}={v}"]
        out = subprocess.run(args, input=sql, capture_output=True, text=True, check=True,
                             env={**os.environ, "BRANCH": live_env["BRANCH"]}).stdout
        return [line.split("\t") for line in out.splitlines() if line]
    return run
