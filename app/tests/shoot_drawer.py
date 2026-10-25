"""Screenshots of the drawer states for the fix round (not a test). Fixture data: the real app with a fake Data API.

Usage: uv run python tests/shoot_drawer.py <out_dir> <prefix>
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from server.dataapi import DataApiClient, TokenProvider
from server.main import create_app
from tests import test_drawer as td
from tests.test_server_unit import API, CLIENT_ID, HOST, SECRET, Upstream

BASE = td.BASE


def shoot(out: Path, prefix: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    http = httpx.AsyncClient(transport=httpx.MockTransport(Upstream(td._rpc)))
    client = DataApiClient(API, TokenProvider(HOST, CLIENT_ID, SECRET, http), http)
    with TestClient(create_app(client=client, tenant=td.TENANT)) as app, sync_playwright() as p:
        b = p.chromium.launch()
        for w, h in [(1440, 900), (744, 1133), (390, 844)]:
            page = td.open_drawer(b, app, width=w, height=h)
            page.screenshot(path=str(out / f"{prefix}-drawer-{w}.png"))
            page.close()

        # A 200 answer that is not JSON.
        page = b.new_page(viewport={"width": 1440, "height": 900})
        router = td._router(app)
        page.route(f"{BASE}/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<html>proxy page</html>")
                   if r.request.method == "POST" else router(r))
        page.goto(f"{BASE}/")
        page.fill("#q", "bcbs georgia")
        page.wait_for_timeout(1200)
        page.get_by_role("button", name="How this search ran").click()
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / f"{prefix}-not-json-1440.png"))
        page.close()

        # diagram.js does not load.
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.route(f"{BASE}/**", lambda r: r.fulfill(status=404, body="") if r.request.url.endswith("/static/diagram.js")
                   else router(r))
        page.goto(f"{BASE}/")
        page.fill("#q", "bcbs georgia")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(out / f"{prefix}-diagram-blocked-1440.png"))
        page.get_by_role("button", name="How this search ran").click()
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / f"{prefix}-diagram-blocked-drawer-1440.png"))
        page.close()

        # A second search that has not answered yet.
        page = td.open_drawer(b, app)
        held = []
        page.route(f"{BASE}/api/search", lambda r: held.append(r))
        page.fill("#q", "united helth")
        page.press("#q", "Enter")
        page.wait_for_timeout(800)
        page.screenshot(path=str(out / f"{prefix}-searching-1440.png"))
        page.close()
        b.close()


if __name__ == "__main__":
    shoot(Path(sys.argv[1]), sys.argv[2])
