"""H2, H3, H6 live on a t-finder-* branch: a real search through the app server as the app SP. The lens's
arguments equal the body that went over the wire, and each shown definition equals the branch's whole
`pg_get_functiondef` text (not `prosrc`).

Run: uv run pytest -m live tests/test_definition_live.py
"""
from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from server.dataapi import DataApiClient, TokenProvider
from server.main import create_app

pytestmark = pytest.mark.live


STATEMENT = re.compile(r"CREATE OR REPLACE FUNCTION (?P<name>[a-z_]+\.[a-z_]+)\(\n(?P<args>[^)]*)\)\n"
                       r"RETURNS TABLE \((?P<cols>[^)]*)\)\n(?P<clauses>.*?)\nAS \$fn\$(?P<body>.*)\$fn\$;", re.S)
# Only the clauses and types this file uses, each in the form Postgres prints it. Anything else raises:
# the renderer must never drop a token that pg_get_functiondef would print.
CLAUSES = re.compile(r"LANGUAGE (?P<lang>plpgsql) VOLATILE SECURITY (?P<security>DEFINER|INVOKER) "
                     r"SET search_path = (?P<path>[a-z_]+(?:, [a-z_]+)*)")
PARAM = re.compile(r"[a-z_]+ (?:text|int|real)(?: DEFAULT \d+)?")
PG_TYPE = {"text": "text", "int": "integer", "real": "real"}


def expected_functiondef(statement: str) -> str:
    """The text pg_get_functiondef prints for a function created from `statement`: the header as Postgres
    renders it from the catalog, then the dollar-quoted body byte for byte."""
    m = STATEMENT.fullmatch(statement)
    if not m:
        raise ValueError("not a CREATE OR REPLACE FUNCTION … RETURNS TABLE statement of the known shape")

    def params(raw: str) -> str:
        items = [" ".join(p.split()) for p in raw.split(",")]
        bad = [p for p in items if not PARAM.fullmatch(p)]
        if bad:
            raise ValueError(f"parameter form the renderer does not know: {bad}")
        return ", ".join(re.sub(r"\b(text|int|real)\b", lambda t: PG_TYPE[t[1]], p, count=1) for p in items)

    c = CLAUSES.fullmatch(" ".join(m["clauses"].split()))
    if not c:
        raise ValueError(f"header clause the renderer does not know: {m['clauses']!r}")
    return (f"CREATE OR REPLACE FUNCTION {m['name']}({params(m['args'])})\n"
            f" RETURNS TABLE({params(m['cols'])})\n"
            f" LANGUAGE {c['lang']}\n"
            + (" SECURITY DEFINER\n" if c["security"] == "DEFINER" else "")
            + " SET search_path TO " + ", ".join(f"'{p}'" for p in c["path"].split(", ")) + "\n"
            f"AS $function${m['body']}$function$\n")


@pytest.fixture(scope="module")
def searched(live_env, branch_api_base, app_sp_token):
    sent: list[dict] = []

    async def record(req: httpx.Request):
        if req.url.path.endswith("/api/rpc/search_insurers"):
            sent.append(json.loads(req.content))

    http = httpx.AsyncClient(event_hooks={"request": [record]})
    tokens = TokenProvider(live_env["WORKSPACE_HOST"], live_env["APP_SP_CLIENT_ID"], live_env["APP_SP_CLIENT_SECRET"], http)
    tenant = f"finder-live-{uuid.uuid4().hex[:10]}"
    with TestClient(create_app(client=DataApiClient(branch_api_base, tokens, http), tenant=tenant)) as tc:
        r = tc.post("/api/search", json={"q": "bcbs georgia", "page": 2})
    assert r.status_code == 200, r.status_code
    return r.json(), sent, tenant, r.text


def test_live_lens_arguments_are_the_body_sent_to_the_branch(searched, live_env, branch_api_base):
    body, sent, tenant, text = searched
    assert len(body["rows"]) == 10
    assert sent == [{"q": "bcbs georgia", "customer_id": tenant, "lim": 10, "off": 10}]
    # Owner decision 15: customer_id by key only, the URL by path only; no tenant and no host in the answer.
    assert body["lens"]["function"] == {"name": "api.search_insurers", "arguments": {**sent[0], "customer_id": None}}
    assert body["lens"]["server_request"]["url"] == "/api/rpc/search_insurers"
    for needle in (tenant, urlparse(branch_api_base).netloc, urlparse(live_env["WORKSPACE_HOST"]).netloc):
        assert needle and needle not in text, "a deployment value is in the /api/search answer"


def test_live_definition_equals_pg_get_functiondef(searched, admin_sql):
    functions = searched[0]["lens"]["definition"]["functions"]
    assert [f["name"] for f in functions] == ["api.search_insurers", "search_idx.search_core"]
    for f in functions:
        # ::regproc fails if the name is overloaded, so the signature is never chosen by hand.
        [[hex_def]] = admin_sql("select encode(convert_to(pg_get_functiondef(:'n'::regproc), 'UTF8'), 'hex')", n=f["name"])
        assert expected_functiondef(f["statement"]) == bytes.fromhex(hex_def).decode(), f["name"]
