"""Item 2: the server's request shape, and the browser-facing surface, with the Data API mocked.

The mock records every request the server sends, so the tests check what went over the wire.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from server.dataapi import CONTRACT_FIELDS, DataApiClient, TokenProvider
from server.main import CSP, create_app

HOST = "https://ws.example.cloud.databricks.com"
API = "https://ep-x.database.us-east-1.cloud.databricks.com/api/2.0/workspace/1/rest/databricks_postgres"
CLIENT_ID = "11111111-2222-3333-4444-555555555555"
SECRET = "dose-unit-test-secret-9f8e7d6c5b4a"
def _b64(part: dict | str) -> str:
    raw = part.encode() if isinstance(part, str) else json.dumps(part).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# Built at run time: a JWT-shaped literal in source trips the repo secret scanner.
TOKEN = ".".join([_b64({"alg": "RS256", "typ": "JWT"}), _b64({"sub": "unit-test-token"}), _b64("unit-test-signature")])
TENANT = "unit-tenant"

ROW = {"rank": 1, "payer_id": "SYN000001", "payer_name": "Aetna", "matched_alias": "aetna",
       "state": "US", "plan_type": "commercial", "score": 1.5}


class Upstream:
    """A fake workspace + Data API. `rpc` decides each RPC answer; every request is recorded."""

    def __init__(self, rpc=None):
        self.requests: list[httpx.Request] = []
        self.rpc = rpc or (lambda req: httpx.Response(200, json=[ROW]))

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.url.path == "/oidc/v1/token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        return self.rpc(req)

    def rpc_requests(self):
        return [r for r in self.requests if r.url.path.endswith("/api/rpc/search_insurers")]


def make(upstream: Upstream) -> TestClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    client = DataApiClient(API, TokenProvider(HOST, CLIENT_ID, SECRET, http), http)
    return TestClient(create_app(client=client, tenant=TENANT))


@pytest.fixture
def up():
    return Upstream()


@pytest.fixture
def tc(up):
    with make(up) as c:
        yield c


# ---- What the server sends ------------------------------------------------------------------

def test_token_request_is_client_credentials_for_the_app_sp(tc, up):
    tc.post("/api/search", json={"q": "aetna"})
    tok = [r for r in up.requests if r.url.path == "/oidc/v1/token"]
    assert len(tok) == 1
    assert str(tok[0].url) == f"{HOST}/oidc/v1/token"
    assert tok[0].headers["authorization"] == "Basic " + base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    assert parse_qs(tok[0].content.decode()) == {"grant_type": ["client_credentials"], "scope": ["all-apis"]}


def test_rpc_request_shape(tc, up):
    tc.post("/api/search", json={"q": "  bcbs   ga ", "page": 3})
    (r,) = up.rpc_requests()
    assert r.method == "POST"
    assert str(r.url) == f"{API}/api/rpc/search_insurers"
    assert r.headers["authorization"] == f"Bearer {TOKEN}"
    assert r.headers["content-type"] == "application/json"
    assert json.loads(r.content) == {"q": "bcbs ga", "customer_id": TENANT, "lim": 10, "off": 20}


def test_token_is_cached_across_searches(tc, up):
    for q in ("aetna", "uhc", "medicad"):
        tc.post("/api/search", json={"q": q})
    assert len([r for r in up.requests if r.url.path == "/oidc/v1/token"]) == 1
    assert len(up.rpc_requests()) == 3


def test_401_gets_one_new_token_and_one_resend():
    calls = []

    def rpc(req):
        calls.append(req)
        return httpx.Response(401, json={"message": "expired"}) if len(calls) == 1 else httpx.Response(200, json=[ROW])

    up = Upstream(rpc)
    with make(up) as tc:
        assert tc.post("/api/search", json={"q": "aetna"}).status_code == 200
    assert len(calls) == 2
    assert len([r for r in up.requests if r.url.path == "/oidc/v1/token"]) == 2


def test_403_is_not_retried():
    up = Upstream(lambda req: httpx.Response(403, json={"code": "42501", "message": "permission denied to set role"}))
    with make(up) as tc:
        r = tc.post("/api/search", json={"q": "aetna"})
    assert r.status_code == 502
    assert r.json() == {"error": "search_failed", "upstream_status": 403, "upstream_code": "42501"}
    assert len(up.rpc_requests()) == 1


# ---- What the browser may send ------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"q": "aetna", "customer_id": "someone-else"},
    {"q": "aetna", "lim": 50},
    {"q": "aetna", "off": 100},
    {"q": "aetna", "page": 6},
    {"q": "aetna", "page": 0},
    {"q": "aetna", "page": "2"},
    {"q": 123},
    {},
])
def test_browser_cannot_set_anything_but_q_and_page(tc, up, body):
    r = tc.post("/api/search", json=body)
    assert r.status_code == 400
    assert up.rpc_requests() == []


@pytest.mark.parametrize("q", ["a", " a ", "x" * 101])
def test_query_length_is_checked_before_any_upstream_call(tc, up, q):
    assert tc.post("/api/search", json={"q": q}).status_code == 400
    assert up.rpc_requests() == []


# ---- What the browser gets back ------------------------------------------------------------------

def test_response_rows_hold_only_contract_fields():
    extra = {**ROW, "network_status": "in", "internal_note": "x"}
    up = Upstream(lambda req: httpx.Response(200, json=[extra]))
    with make(up) as tc:
        body = tc.post("/api/search", json={"q": "aetna"}).json()
    assert [set(r) for r in body["rows"]] == [set(CONTRACT_FIELDS)]


def test_upstream_error_body_never_reaches_the_browser():
    secret_text = f"upstream echoed Bearer {TOKEN} and {SECRET}"
    up = Upstream(lambda req: httpx.Response(500, text=secret_text))
    with make(up) as tc:
        r = tc.post("/api/search", json={"q": "aetna"})
    assert r.status_code == 502
    for needle in (TOKEN, SECRET, "Bearer", "upstream echoed"):
        assert needle not in r.text


def test_token_exchange_failure_body_never_reaches_the_browser():
    def handler(req):
        if req.url.path == "/oidc/v1/token":
            return httpx.Response(401, text=f"invalid_client {SECRET}")
        return httpx.Response(200, json=[ROW])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DataApiClient(API, TokenProvider(HOST, CLIENT_ID, SECRET, http), http)
    with TestClient(create_app(client=client, tenant=TENANT)) as tc:
        r = tc.post("/api/search", json={"q": "aetna"})
    assert r.status_code == 502 and SECRET not in r.text and "invalid_client" not in r.text


def test_lens_shows_request_without_any_header_value(tc):
    body = tc.post("/api/search", json={"q": "aetna"}).json()
    lens = json.dumps(body["lens"])
    assert TOKEN not in lens and "Bearer" not in lens and SECRET not in lens
    assert body["lens"]["server_request"]["body"] == {"q": "aetna", "customer_id": None, "lim": 10, "off": 0}, \
        "the lens body is not the body sent with customer_id as a key only"
    assert body["lens"]["server_request"]["url"] == "/api/rpc/search_insurers", "the URL is the path only"


def _hosts() -> list[str]:
    return [HOST.split("//", 1)[1], API.split("//", 1)[1].split("/", 1)[0]]


def _down(req):
    raise httpx.ConnectError("down", request=req)


@pytest.mark.parametrize("upstream, request_body", [
    (lambda req: httpx.Response(200, json=[ROW]), {"q": "aetna"}),
    (lambda req: httpx.Response(200, json=[ROW]), {"q": "aetna", "page": 2}),
    (lambda req: httpx.Response(200, json=[ROW]), {"q": "a"}),
    (lambda req: httpx.Response(200, json=[ROW]), {"q": "x" * 101}),
    (lambda req: httpx.Response(200, json=[ROW]), {"q": 5}),
    (lambda req: httpx.Response(403, json={"code": "42501"}), {"q": "aetna"}),
    (lambda req: httpx.Response(400, json={"code": "22023"}), {"q": "aetna"}),
    (_down, {"q": "aetna"}),
])
def test_no_response_body_holds_the_tenant_or_a_host(upstream, request_body):
    """T6-1 (owner decision 15): the tenant value and the workspace and Data API hosts never reach the browser."""
    with make(Upstream(upstream)) as c:
        r = c.post("/api/search", json=request_body)
    for needle in [TENANT, *_hosts()]:
        assert needle not in r.text, f"a tenant value or a host is in a {r.status_code} answer: {needle}"


def test_lens_status_is_the_upstream_status():
    """T6-14: the lens shows the status the Data API answered, not a typed 200."""
    with make(Upstream(lambda req: httpx.Response(203, json=[ROW]))) as c:
        assert c.post("/api/search", json={"q": "aetna"}).json()["lens"]["status"] == 203, \
            "the lens status is not the upstream status"


from server.main import SEARCH_SQL as DEPLOYED_SQL  # noqa: E402


def test_lens_definition_is_the_deployed_file_statement_for_each_function(tc):
    """H1, T03-6: each shown statement is an exact slice of the copy, never re-typed or re-formatted."""
    d = tc.post("/api/search", json={"q": "aetna"}).json()["lens"]["definition"]
    assert d["source"] == {"repo": "https://github.com/saselvan/lakebase-payer-search",
                           "path": "sql/20_search_function.sql",
                           "commit": "b60a1512fd2f43332960ed543021b9e3f20d4114",
                           "blob": "ec46a4228f567064654b12f70f249c128ab75f3c"}
    copy = DEPLOYED_SQL.read_text()
    assert [f["name"] for f in d["functions"]] == ["api.search_insurers", "search_idx.search_core"]
    for f in d["functions"]:
        s = f["statement"]
        assert copy.count(s) == 1
        assert s.startswith(f"CREATE OR REPLACE FUNCTION {f['name']}(\n") and s.endswith("\n$fn$;")
        assert s.count("$fn$") == 2


def test_lens_function_arguments_are_the_body_the_server_sent(tc, up):
    """H6, T03-7: the arguments are the JSON body that went over the wire, not defaults or the browser body."""
    lens = tc.post("/api/search", json={"q": "  bcbs   ga ", "page": 2}).json()["lens"]
    (r,) = up.rpc_requests()
    sent = json.loads(r.content)
    assert sent == {"q": "bcbs ga", "customer_id": TENANT, "lim": 10, "off": 10}
    # T6-2: every key that went over the wire, with its value, except customer_id: the key only (owner decision 15).
    assert lens["function"] == {"name": "api.search_insurers", "arguments": {**sent, "customer_id": None}}


def test_lens_holds_no_call_form(tc):
    """H4, T03-8 (C14 UNPROVEN): no SQL call text and no "SQL that ran" anywhere but the copied statements."""
    lens = tc.post("/api/search", json={"q": "aetna"}).json()["lens"]
    assert set(lens) == {"browser_request", "server_request", "status", "function", "definition"}
    rest = json.dumps({**lens, "definition": {**lens["definition"], "functions": [
        {k: v for k, v in f.items() if k != "statement"} for f in lens["definition"]["functions"]]}})
    for needle in ("select", "=>", "sql that ran", "statement that ran"):
        assert needle not in rest.lower(), needle


def test_security_headers_on_page_and_api(tc):
    for r in (tc.get("/"), tc.post("/api/search", json={"q": "aetna"})):
        assert r.headers["content-security-policy"] == CSP
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
    assert "connect-src 'self'" in CSP and "script-src 'self'" in CSP and "unsafe" not in CSP
    assert tc.post("/api/search", json={"q": "aetna"}).headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/debug", "/env", "/api/config"])
def test_no_docs_or_debug_routes(tc, path):
    assert tc.get(path).status_code in (404, 405)


@pytest.mark.parametrize("path", [
    "/.env", "/static/../.env", "/static/..%2f.env", "/static/%2e%2e/.env", "/static/..%2F..%2F.env",
    "/app.yaml", "/static/../app.yaml", "/server/main.py", "/static/../server/main.py", "/static/",
])
def test_no_file_outside_static_is_served(tc, path):
    r = tc.get(path)
    assert r.status_code in (400, 404, 405), (path, r.status_code)
    assert "DATABRICKS" not in r.text and "DATA_API_BASE" not in r.text


def test_timing_includes_retries():
    """T6-22: elapsed_ms runs from the first request to the answer, so a retried search shows its whole wait."""
    calls = []

    def rpc(req):
        calls.append(req)
        if len(calls) == 1:
            time.sleep(0.2)
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=[ROW])
    with make(Upstream(rpc)) as c:
        body = c.post("/api/search", json={"q": "aetna"}).json()
    assert len(calls) == 2, "the search was not retried"
    assert body["elapsed_ms"] >= 450, f"the timing restarts on a retry: {body['elapsed_ms']} ms"
