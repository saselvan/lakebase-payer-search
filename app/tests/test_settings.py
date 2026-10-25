"""I9 (session 4, item 2): the tenant label and the Data API base come only from the app's environment.

The server has no default for either. Without one, or with the placeholder the tracked app.yaml holds, it refuses
to start, so a deploy that skipped scripts/stage_deploy.py fails at start-up and never searches with a stale value.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server.dataapi import DataApiClient, TokenProvider
from server.main import create_app
from tests.test_server_unit import API, CLIENT_ID, HOST, SECRET, Upstream

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "<set by scripts/stage_deploy.py>"
BASE_ENV = {"DATABRICKS_HOST": HOST, "DATABRICKS_CLIENT_ID": CLIENT_ID, "DATABRICKS_CLIENT_SECRET": SECRET,
            "DATA_API_BASE": API, "FINDER_TENANT": "env-tenant"}


def _client(up):
    http = httpx.AsyncClient(transport=httpx.MockTransport(up))
    return DataApiClient(API, TokenProvider(HOST, CLIENT_ID, SECRET, http), http)


@pytest.mark.parametrize("value", [None, "", "  ", PLACEHOLDER])
def test_server_refuses_to_start_without_a_tenant(value):
    env = {k: v for k, v in BASE_ENV.items() if k != "FINDER_TENANT"}
    if value is not None:
        env["FINDER_TENANT"] = value
    with pytest.raises(RuntimeError, match="FINDER_TENANT"):
        create_app(client=_client(Upstream()), env=env)


@pytest.mark.parametrize("value", [None, "", PLACEHOLDER, "http://ep-x.example/api"])
def test_server_refuses_to_start_without_a_data_api_base(value):
    env = {k: v for k, v in BASE_ENV.items() if k != "DATA_API_BASE"}
    if value is not None:
        env["DATA_API_BASE"] = value
    with pytest.raises(RuntimeError, match="DATA_API_BASE"):
        create_app(env=env)


def test_server_takes_the_tenant_from_its_environment():
    up = Upstream()
    with TestClient(create_app(client=_client(up), env=BASE_ENV)) as tc:
        assert tc.post("/api/search", json={"q": "aetna"}).status_code == 200
    assert [json.loads(r.content)["customer_id"] for r in up.rpc_requests()] == ["env-tenant"]


def test_server_holds_no_default_tenant():
    src = (ROOT / "server/main.py").read_text()
    assert not re.search(r'environ\.get\(\s*"FINDER_TENANT"\s*,', src), "server/main.py has a default tenant"


def test_app_yaml_names_both_settings_with_the_placeholder_only():
    text = (ROOT / "app.yaml").read_text()
    for name in ("DATA_API_BASE", "FINDER_TENANT"):
        m = re.search(rf"- name: {name}\n\s+value: \"([^\"]*)\"", text)
        assert m, f"app.yaml does not set {name}"
        assert m.group(1) == PLACEHOLDER, f"app.yaml holds a value for {name}, not the placeholder"
