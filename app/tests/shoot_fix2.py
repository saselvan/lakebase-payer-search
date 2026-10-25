"""Screenshots for fix round 2 (not a test). Usage: PYTHONPATH=. uv run python tests/shoot_fix2.py <out_dir> <prefix>

Fixture answers through the real create_app; no ms is a measure except the 41.6 ms fixture, which is labelled so.
"""
from __future__ import annotations

import json
import sys

import httpx
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

import tests.test_drawer as td
from server.dataapi import DataApiClient, TokenProvider
from server.main import create_app
from tests.test_server_unit import API, CLIENT_ID, HOST, SECRET, Upstream


def make(rpc):
    http = httpx.AsyncClient(transport=httpx.MockTransport(Upstream(rpc)))
    return TestClient(create_app(client=DataApiClient(API, TokenProvider(HOST, CLIENT_ID, SECRET, http), http), tenant=td.TENANT))


def page_for(b, app, post=None, block=None):
    page = b.new_page(viewport={"width": 1440, "height": 900})
    router = td._router(app)

    def handle(route):
        if post and route.request.method == "POST":
            return post(route)
        if block and route.request.url.endswith(block):
            return route.fulfill(status=404, body="")
        return router(route)
    page.route(f"{td.BASE}/**", handle)
    page.goto(f"{td.BASE}/")
    return page


def failed(page, out, name):
    page.fill("#q", "bcbs georgia")
    page.wait_for_selector("#error:not([hidden])")
    page.wait_for_timeout(200)
    page.screenshot(path=f"{out}/{name}")


def main(out: str, prefix: str) -> None:
    ok = make(td._rpc)
    up403 = make(lambda req: httpx.Response(403, json={"code": "42501", "message": "permission denied"}))
    up502 = make(lambda req: httpx.Response(502, text="bad gateway"))
    with ok, up403, up502, sync_playwright() as p:
        b = p.chromium.launch()
        failed(page_for(b, up403), out, f"{prefix}-front-failure-403-1440.png")
        failed(page_for(b, up502), out, f"{prefix}-front-failure-502-1440.png")
        failed(page_for(b, ok, post=lambda r: r.fulfill(status=200, content_type="text/html", body="<html>proxy</html>")),
               out, f"{prefix}-front-failure-not-json-1440.png")
        failed(page_for(b, ok, post=lambda r: r.abort()), out, f"{prefix}-front-failure-network-1440.png")

        page = page_for(b, ok, block="/static/diagram.js")
        page.fill("#q", "bcbs georgia")
        page.wait_for_selector("#results:not([hidden]) .group")
        page.get_by_role("button", name="How this search ran").click()
        page.wait_for_timeout(300)
        page.screenshot(path=f"{out}/{prefix}-diagram-blocked-drawer-1440.png")

        differ = td.payload(ok, "bcbs georgia")
        differ["lens"]["status"] = 203
        page = page_for(b, ok, post=lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(differ)))
        page.fill("#q", "bcbs georgia")
        page.wait_for_selector("#results:not([hidden]) .group")
        page.get_by_role("button", name="How this search ran").click()
        page.wait_for_selector("#lens-diagram .pfd-card")
        page.wait_for_timeout(300)
        page.screenshot(path=f"{out}/{prefix}-box1-answer-app200-dataapi203-1440.png")

        td.MODE["elapsed"] = 41.6  # a fixture timing, labelled so in the file name
        page = td.open_drawer(b, ok)
        page.locator("#lens-run .facts").scroll_into_view_if_needed()
        page.locator("#lens-run .facts").screenshot(path=f"{out}/{prefix}-measured-note-fixture-42ms.png")
        td.MODE["elapsed"] = td.KEEP

        page.evaluate("document.querySelector('#proof').open = true")
        page.locator("#proof").scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        page.locator("#proof").screenshot(path=f"{out}/{prefix}-evidence-answer.png")
        b.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
