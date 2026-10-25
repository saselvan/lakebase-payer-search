"""I7, I11 (session 4, item 4): the upload tree is an allowlist, and the Data API base is production's.

The script writes into a destination the caller names (tests use a temp dir; a deploy uses /tmp).
It never copies .env. The Data API URL is an argument here, so a unit run does not call Databricks.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("stage_deploy", ROOT / "scripts/stage_deploy.py")
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)

PROD = "https://ep-prod.database.us-east-1.cloud.databricks.com/api/2.0/workspace/1/rest/databricks_postgres"
BRANCH = "https://ep-branch.database.us-east-1.cloud.databricks.com/api/2.0/workspace/1/rest/databricks_postgres"
TENANT = "stage-fixture-tenant"


def _stage(dest: Path, base: str = PROD, tenant: str = TENANT) -> Path:
    return sd.stage(ROOT, dest, data_api_base=base, tenant=tenant, production_base=PROD)


def test_staged_tree_is_the_allowlist_only(tmp_path):
    dest = _stage(tmp_path / "out")
    rel = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    expected = sorted(sd.allowlist(ROOT))
    assert rel == expected, f"staged extra or missing: extra={set(rel)-set(expected)} missing={set(expected)-set(rel)}"
    assert not any(p == ".env" or p.endswith("/.env") for p in rel), ".env is in the staged tree"


def test_staged_app_yaml_holds_production_and_the_tenant(tmp_path):
    dest = _stage(tmp_path / "out")
    text = (dest / "app.yaml").read_text()
    assert f'value: "{PROD}"' in text
    assert f'value: "{TENANT}"' in text
    assert sd.PLACEHOLDER not in text
    assert sd.PLACEHOLDER in (ROOT / "app.yaml").read_text()


def test_refuses_a_branch_data_api_url(tmp_path):
    with pytest.raises(RuntimeError, match="not production"):
        _stage(tmp_path / "out", base=BRANCH)


def test_refuses_a_missing_https_base(tmp_path):
    with pytest.raises(RuntimeError, match="DATA_API_BASE"):
        _stage(tmp_path / "out", base="http://ep-prod.example/api")


def test_allowlist_does_not_name_env_or_tests():
    names = sd.allowlist(ROOT)
    assert ".env" not in names
    assert not any(n.startswith("tests/") or n.startswith("runs/") for n in names)
    assert "app.yaml" in names and "requirements.txt" in names
    assert any(n.startswith("server/") for n in names)
    assert any(n.startswith("static/") for n in names)
    assert "sql/20_search_function.sql" in names


def test_staged_function_is_the_repo_file(tmp_path):
    """K6: the upload carries the repo's sql/20, byte for byte; the server finds it at sql/ in the upload copy."""
    dest = _stage(tmp_path / "out")
    assert (dest / "sql/20_search_function.sql").read_bytes() == (ROOT.parent / "sql/20_search_function.sql").read_bytes()
