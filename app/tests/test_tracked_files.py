"""T6-7, T6-25, T6-26, I10: no tracked file holds a value that identifies this deployment, with no exceptions.

The values come from the local .env only; the test prints their kind, never the value. app.yaml holds env names and
a placeholder (scripts/stage_deploy.py fills the upload copy), and the server has no default. Without .env (a clone
of the public repo) the value checks skip, and the shape checks still run; FINDER_REQUIRE_ENV=1 makes a missing .env
fail. A deployer always has .env: scripts/stage_deploy.py reads the tenant from it.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
BRANCH_HOST = r"ep-[a-z0-9]+(-[a-z0-9]+)+\.database\.[a-z0-9.-]+"  # real endpoint hosts; the unit tests' "ep-x" is a fake
KINDS = ["sp-client-id", "sp-id", "secret-id", "workspace-host", "workspace-id", "tenant", "data-api-host"]


def _env() -> dict[str, str]:
    if not ENV.exists():
        if os.environ.get("FINDER_REQUIRE_ENV") == "1":
            pytest.fail("FINDER_REQUIRE_ENV=1: the tracked-file check needs .env (the deployment values to look for)")
        pytest.skip("no .env, so no deployment values to look for (shape checks still run)")
    out = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def _netloc(url: str) -> str:
    return urlparse(url if "//" in url else "https://" + url).netloc


def _values() -> dict[str, list[str]]:
    env = _env()
    base = env["DATA_API_BASE_PRODUCTION"]
    return {"sp-client-id": [env["APP_SP_CLIENT_ID"]], "sp-id": [env["APP_SP_ID"]], "secret-id": [env["APP_SP_SECRET_ID"]],
            "workspace-host": [_netloc(env["WORKSPACE_HOST"])],
            "workspace-id": sorted(set(re.findall(r"/workspace/(\d+)", base))),
            "tenant": [env["FINDER_TENANT"]],
            "data-api-host": [_netloc(base)]}


def _grep(*args: str) -> set[tuple[str, int]]:
    p = subprocess.run(["git", "grep", "-n", "-I", *args], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode in (0, 1), p.stderr
    out = set()
    for line in p.stdout.splitlines():
        path, lineno, _ = line.split(":", 2)
        out.add((path, int(lineno)))
    return out


@pytest.mark.parametrize("kind", KINDS)
def test_no_tracked_file_holds_an_identifying_value(kind):
    values = _values()[kind]
    assert values and all(len(v) >= 6 for v in values), f"the {kind} value to look for is missing or too short"
    found = set().union(*(_grep("-F", v) for v in values))
    assert not found, f"tracked files hold the {kind}: {sorted(found)}"


def test_no_tracked_file_holds_a_branch_host():
    found = _grep("-E", BRANCH_HOST)
    assert not found, f"tracked files hold a branch host: {sorted(found)}"


def test_a_missing_env_skips_and_never_passes(monkeypatch, tmp_path):
    """T6-26, K8: no .env must not turn the check into a pass. A public clone skips (reported as skipped);
    FINDER_REQUIRE_ENV=1 turns the skip into a failure."""
    import tests.test_tracked_files as me
    monkeypatch.setattr(me, "ENV", tmp_path / "missing.env")
    monkeypatch.delenv("FINDER_REQUIRE_ENV", raising=False)
    with pytest.raises(pytest.skip.Exception):
        me._env()
    monkeypatch.setenv("FINDER_REQUIRE_ENV", "1")
    with pytest.raises(pytest.fail.Exception, match="needs .env"):
        me._env()
