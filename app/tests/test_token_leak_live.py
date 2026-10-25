"""Item 5 (risk f, claim C7): no browser response holds the token, `Bearer`, or the client secret.

The real server runs in this process against the t-finder branch's Data API as the app's SP, so the test
reads the exact token the server holds. A real Chromium drives the page; every response the browser
receives (HTML, JS, CSS, fonts, API JSON, error bodies, headers) is captured and scanned, along with the
rendered DOM, the console, and the server's own log.

Run: uv run pytest -m live tests/test_token_leak_live.py
"""
from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
from urllib.parse import urlparse

import pytest
import uvicorn
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from server.dataapi import CONTRACT_FIELDS
from server.main import create_app
from tests.conftest import JWT

pytestmark = pytest.mark.live

HOSTILE = "<img src=x onerror=alert(1)> '; drop table x; --"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest.fixture(scope="module")
def server(live_env, branch_api_base, app_sp):
    env = {"DATABRICKS_HOST": live_env["WORKSPACE_HOST"], "DATABRICKS_CLIENT_ID": app_sp,
           "DATABRICKS_CLIENT_SECRET": live_env["APP_SP_CLIENT_SECRET"], "DATA_API_BASE": branch_api_base}
    app = create_app(env=env, tenant="finder-leak-test")
    cap = Capture()
    root = logging.getLogger()
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    # httpcore's DEBUG trace names each connection step but no header value; it only buried the failure output.
    core = logging.getLogger("httpcore")
    core_level = core.level
    core.setLevel(logging.INFO)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="debug"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started
    yield f"http://127.0.0.1:{port}", app, cap
    srv.should_exit = True
    t.join(10)
    root.removeHandler(cap)
    core.setLevel(core_level)


def drive(base: str):
    responses, requests, dialogs, console = [], [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def on_response(resp):
            try:
                body = resp.body()
            except Exception:
                body = b""
            responses.append({"url": resp.url, "status": resp.status, "headers": resp.all_headers(), "body": body})

        page.on("response", on_response)
        page.on("request", lambda r: requests.append(r.url))
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.on("console", lambda m: console.append(m.text))

        page.goto(base + "/")
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="bcbs georgia", exact=True).click()
        page.locator("#results .group.lead").wait_for(timeout=20000)
        page.get_by_role("button", name="Show 10 more").click()
        # Exactly 20, not "more than 10": page 2 must add to page 1, and every ID must hold text.
        page.wait_for_function("""() => {
            const ids = [...document.querySelectorAll('#results [data-field="payer_id"]')];
            return ids.length === 20 && ids.every(id => id.textContent.trim() !== '');
        }""", timeout=20000)
        page.get_by_role("button", name="How this search ran").click()
        page.get_by_text("Evidence for this page", exact=True).click()
        page.get_by_text("Response rows (JSON)", exact=True).click()
        page.locator("#lens-def > summary").click()  # ticket 05: the drawer and the definition open
        page.locator("#lens-def pre").first.wait_for(state="visible")
        dom_fields = page.eval_on_selector_all("[data-field]", "els => els.map(e => e.dataset.field)")
        results_text = page.inner_text("#results")
        lens_text = page.inner_text("#lens")
        lens_def_text = page.inner_text("#lens-def")
        lens_dom = page.eval_on_selector("#lens", "e => e.outerHTML")
        page.keyboard.press("Escape")

        page.get_by_label("Insurer name").fill(HOSTILE)
        try:
            page.wait_for_function("() => !document.querySelector('#search').classList.contains('busy') "
                                   "&& document.querySelector('main').innerText.includes('onerror=alert(1)')",
                                   timeout=20000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(300)
        hostile_text = page.inner_text("main")

        page.get_by_label("Insurer name").fill("a")
        page.evaluate("""() => Promise.all([
            fetch('/api/search', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                  body: JSON.stringify({q: 'aetna', lim: 50})}).then(r => r.text()),
            fetch('/api/search', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{'}).then(r => r.text()),
            fetch('/.env').then(r => r.text()),
            fetch('/static/..%2f.env').then(r => r.text()),
            fetch('/openapi.json').then(r => r.text()),
        ])""")
        page.wait_for_timeout(500)
        final_dom = page.content()
        browser.close()
    return dict(responses=responses, requests=requests, dialogs=dialogs, console=console, dom_fields=dom_fields,
                results_text=results_text, lens_text=lens_text, lens_def_text=lens_def_text, lens_dom=lens_dom,
                hostile_text=hostile_text, final_dom=final_dom)


@pytest.fixture(scope="module")
def run(server):
    base, app, cap = server
    seen = drive(base)
    return base, app, cap, seen


def test_token_leak_live(run, live_env):
    base, app, cap, seen = run
    token = app.state.client.tokens._token
    secret = live_env["APP_SP_CLIENT_SECRET"]

    # A11: the needles are real and specific.
    assert token and JWT.fullmatch(token), "the server holds no JWT: nothing to look for"
    assert len(secret) >= 30
    signature = token.rsplit(".", 1)[1]
    assert len(signature) >= 40

    # A12: the capture saw the page, its assets, and real searches with rows.
    paths = [urlparse(r["url"]).path for r in seen["responses"]]
    for must in ("/", "/static/app.js", "/static/app.css", "/static/diagram.js", "/static/diagram.css"):
        assert must in paths, f"never loaded {must}"
    searches = [r for r in seen["responses"] if urlparse(r["url"]).path == "/api/search" and r["status"] == 200]
    assert len(searches) >= 2 and all(json.loads(r["body"])["rows"] for r in searches[:2])
    assert any(r["status"] == 400 for r in seen["responses"]), "no error path was driven"

    def scan(label: str, text: str):
        assert token not in text, f"{label}: token"
        assert signature not in text, f"{label}: token signature"
        assert secret not in text, f"{label}: client secret"
        assert not re.search(r"bearer", text, re.I), f"{label}: 'Bearer'"
        assert not JWT.search(text), f"{label}: a JWT"

    for r in seen["responses"]:
        scan(f"body {r['url']}", r["body"].decode("utf-8", "replace"))
        scan(f"headers {r['url']}", json.dumps(r["headers"]))
    scan("rendered DOM", seen["final_dom"])
    scan("lens", seen["lens_text"])
    scan("lens DOM, drawer and definition open", seen["lens_dom"])
    scan("console", "\n".join(seen["console"]))
    scan("server log", "\n".join(cap.lines))

    # A10: the browser talked to nobody but the app.
    origin = urlparse(base).netloc
    assert {urlparse(u).netloc for u in seen["requests"]} == {origin}, set(seen["requests"])


def test_drawer_shows_the_definition_but_not_the_tenant_or_the_host_live(run, branch_api_base):
    *_, seen = run
    assert "api.search_insurers" in seen["lens_def_text"] and "SELECT" in seen["lens_def_text"], "definition not shown"
    assert "finder-leak-test" not in seen["lens_dom"], "the tenant value is on the drawer"
    assert urlparse(branch_api_base).netloc not in seen["lens_dom"], "the Data API host is on the drawer"
    assert "customer_id: a label the server sets" in seen["lens_text"]


def test_page_renders_only_contract_fields_live(run):
    *_, seen = run
    assert seen["dom_fields"], "no fields rendered"
    assert set(seen["dom_fields"]) <= set(CONTRACT_FIELDS) - {"score"}, set(seen["dom_fields"])
    for word in ("undefined", "null", "NaN", "[object Object]"):
        assert word not in seen["results_text"], word


def test_hostile_query_is_text_not_markup_live(run):
    *_, seen = run
    assert seen["dialogs"] == [], seen["dialogs"]
    assert "onerror=alert(1)" in seen["hostile_text"], "the hostile query was not shown as literal text"
