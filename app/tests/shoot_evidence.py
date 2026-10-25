"""Screens for session 6 item 1: the evidence panel of the drawer, after a fixture search.

Usage: uv run python -m tests.shoot_evidence <out-dir> <label>      for example runs/2026-10-25/s6/screens before
The answers are fixtures through the app's own create_app (tests/test_drawer.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from server.dataapi import DataApiClient, TokenProvider
from server.main import create_app
from tests.test_drawer import TENANT, Upstream, _rpc, open_drawer
from tests.test_server_unit import API, CLIENT_ID, HOST, SECRET


def main(out: Path, label: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    http = httpx.AsyncClient(transport=httpx.MockTransport(Upstream(_rpc)))
    client = DataApiClient(API, TokenProvider(HOST, CLIENT_ID, SECRET, http), http)
    with TestClient(create_app(client=client, tenant=TENANT)) as app, sync_playwright() as p:
        b = p.chromium.launch()
        for width, height in ((1440, 900), (390, 844)):
            page = open_drawer(b, app, width=width, height=height)
            page.evaluate("document.getElementById('proof').open = true")
            proof = page.locator("#proof .qa")
            proof.scroll_into_view_if_needed()
            proof.screenshot(path=str(out / f"evidence-{label}-{width}.png"))
            page.close()
        b.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
