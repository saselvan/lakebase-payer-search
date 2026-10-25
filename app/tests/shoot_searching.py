"""Screens for session 4 item 1: the page 600 ms into a slow search, after a first answer and as a first search.

Usage: uv run python -m tests.shoot_searching <out-dir> <label>      for example runs/2026-10-24/s4/screens before
The answers are fixtures through the app's own create_app (tests/test_drawer.py); no ms is shown on the front.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from server.dataapi import DataApiClient, TokenProvider
from server.main import create_app
from tests.test_drawer import BASE, TENANT, Upstream, _router, _rpc
from tests.test_server_unit import API, CLIENT_ID, HOST, SECRET


def main(out: Path, label: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    http = httpx.AsyncClient(transport=httpx.MockTransport(Upstream(_rpc)))
    client = DataApiClient(API, TokenProvider(HOST, CLIENT_ID, SECRET, http), http)
    with TestClient(create_app(client=client, tenant=TENANT)) as app, sync_playwright() as p:
        b = p.chromium.launch()
        for width, height in ((1440, 900), (390, 844)):
            for case in ("after-answer", "first-search"):
                page = b.new_page(viewport={"width": width, "height": height})
                handle, held, hold = _router(app), [], [False]
                page.route(f"{BASE}/**", lambda r: held.append(r) if r.request.method == "POST" and hold[0] else handle(r))
                page.goto(f"{BASE}/")
                if case == "after-answer":
                    page.fill("#q", "bcbs georgia")
                    page.press("#q", "Enter")
                    page.wait_for_selector("#results:not([hidden]) .group")
                hold[0] = True
                page.fill("#q", "united helth")
                page.press("#q", "Enter")
                page.wait_for_timeout(600)
                page.evaluate("document.fonts.ready")
                page.screenshot(path=str(out / f"searching-{label}-{case}-{width}.png"))
                for r in held:
                    handle(r)
                page.close()
        b.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
