"""Ticket 05: the approved diagram in the drawer, from the lens of the search on screen (T5D-1..T5D-17).

The answers are real: the app's own create_app with a fake Data API (the unit-test Upstream), and the
deployed definition through load_definition(). A Playwright route sends every request (page, static files,
/api/search) to that app, so the browser runs under the app's own CSP.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from server.dataapi import DataApiClient, TokenProvider
from server.main import create_app
from tests.test_server_unit import API, CLIENT_ID, HOST, SECRET, Upstream
from tests.test_static_checks import FORBIDDEN

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
BASE = "http://finder.test"
FIXTURES = json.loads((ROOT / "tests/fixtures/search_rows.json").read_text())["queries"]
TENANT = "drawer-fixture-tenant"
KEEP = object()
MODE = {"fail": False, "status": None, "elapsed": KEEP}


def _rpc(req: httpx.Request) -> httpx.Response:
    if MODE["fail"] == 502:
        return httpx.Response(502, text="bad gateway")
    if MODE["fail"]:
        return httpx.Response(403, json={"code": "42501", "message": "permission denied"})
    body = json.loads(req.content)
    return httpx.Response(200, json=FIXTURES.get(body["q"], {}).get(f"page{body['off'] // 10 + 1}", []))


@pytest.fixture(scope="module")
def app():
    http = httpx.AsyncClient(transport=httpx.MockTransport(Upstream(_rpc)))
    client = DataApiClient(API, TokenProvider(HOST, CLIENT_ID, SECRET, http), http)
    with TestClient(create_app(client=client, tenant=TENANT)) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset():
    MODE.update(fail=False, status=None, elapsed=KEEP)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _router(app):
    def handle(route):
        req = route.request
        path = req.url.removeprefix(BASE)
        if req.method == "POST":
            r = app.post(path, content=req.post_data or "", headers={"content-type": "application/json"})
            body = r.text
            if (MODE["status"] is not None or MODE["elapsed"] is not KEEP) and r.status_code == 200:
                data = r.json()
                if MODE["status"] is not None:
                    data["lens"]["status"] = MODE["status"]
                if MODE["elapsed"] == "missing":
                    del data["elapsed_ms"]
                elif MODE["elapsed"] is not KEEP:
                    data["elapsed_ms"] = MODE["elapsed"]
                body = json.dumps(data)
        else:
            r = app.get(path)
            body = r.content
        headers = {k: v for k, v in r.headers.items() if k.lower() not in {"content-length", "content-encoding"}}
        return route.fulfill(status=r.status_code, headers=headers, body=body)
    return handle


def search(page, q):
    page.fill("#q", q)
    page.press("#q", "Enter")
    page.wait_for_function("q => document.querySelector('#lens-failed:not([hidden])') || "
                           "(document.querySelector('#lens-diagram') || {textContent: ''}).textContent.includes(q)", arg=q)


def open_drawer(browser, app, q="bcbs georgia", width=1440, height=900, reduced=False):
    page = browser.new_page(viewport={"width": width, "height": height},
                            reduced_motion="reduce" if reduced else "no-preference")
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route(f"{BASE}/**", _router(app))
    page.goto(f"{BASE}/")
    page.fill("#q", q)
    page.wait_for_selector("#results:not([hidden]) .group")
    page.get_by_role("button", name="How this search ran").click()
    page.wait_for_selector("#lens-diagram .pfd-card", timeout=5000)
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(150)
    page.errors = errors
    return page


def payload(app, q, page=1):
    return app.post("/api/search", json={"q": q, "page": page}).json()


ANIMS = """() => document.getAnimations().map(a => ({state: a.playState, t: a.currentTime,
    end: a.effect.getComputedTiming().endTime, inside: !!a.effect.target.closest('#lens-diagram')}))"""

# Visible text: not aria-hidden, effective opacity 1, not in a closed <details> body.
VISIBLE_TEXT = """sel => { const out = [];
  const walk = (n) => { for (const c of n.childNodes) {
    if (c.nodeType === 3) { if (c.textContent.trim()) out.push(c.textContent); continue; }
    if (c.nodeType !== 1 || c.closest('[aria-hidden="true"]') || c.hidden) continue;
    const det = c.parentElement.closest('details');
    if (det && !det.open && c.tagName !== 'SUMMARY' && !c.closest('summary')) continue;
    let o = 1; for (let e = c; e; e = e.parentElement) o *= +getComputedStyle(e).opacity;
    if (o < 0.99 || getComputedStyle(c).visibility === 'hidden' || getComputedStyle(c).display === 'none') continue;
    walk(c); } };
  walk(document.querySelector(sel)); return out.join(' ').replace(/\\s+/g, ' '); }"""


# ---- T5D-9: the drawer runs the approved component, byte for byte ---------------------------------

@pytest.mark.parametrize("name", ["diagram.js", "diagram.css"])
def test_static_diagram_is_the_approved_copy(name):
    assert (STATIC / name).is_file() and (STATIC / name).read_bytes() == (ROOT / "diagram" / name).read_bytes(), \
        f"static/{name} is not the approved copy"


def test_page_loads_the_diagram_from_the_same_origin():
    index = (STATIC / "index.html").read_text()
    assert '<script src="/static/diagram.js" defer></script>' in index, "the page does not load diagram.js"
    assert '<link rel="stylesheet" href="/static/diagram.css">' in index, "the page does not load diagram.css"
    assert index.index("/static/diagram.js") < index.index("/static/app.js"), "diagram.js loads after app.js"


# ---- T5D-6: the definition comes only from lens.definition ----------------------------------------

def test_static_files_hold_no_typed_definition():
    for path in STATIC.rglob("*"):
        if path.suffix in {".html", ".js", ".css"}:
            text = path.read_text()
            for needle in ("CREATE OR REPLACE", "CREATE FUNCTION", "$fn$", "plpgsql", "LANGUAGE sql"):
                assert needle not in text, f"a static file types the definition: {path.name}: {needle}"


def test_app_reads_only_lens_fields_the_server_sends(app):
    js = (STATIC / "app.js").read_text()
    lens = payload(app, "bcbs georgia")["lens"]
    paths = set(re.findall(r"(?<![.\w])lens\.([a-z_]+(?:\.[a-z_]+)*)", js))  # not ui.lens, the drawer node
    assert {"server_request.body", "function.arguments", "definition", "status"} <= paths, paths
    for p in paths:
        node = lens
        for part in p.split("."):
            assert isinstance(node, dict) and part in node, f"lens.{p} is not in the payload"
            node = node[part]


def test_definition_is_closed_opens_by_click_and_keyboard_and_is_the_payload(browser, app):
    page = open_drawer(browser, app)
    want = payload(app, "bcbs georgia")["lens"]["definition"]
    det = page.locator("#lens-def")
    assert det.count() == 1 and det.evaluate("e => e.tagName") == "DETAILS"
    assert det.evaluate("e => !!e.closest('[data-hop=\"function\"]')"), "the definition is not under hop 4"
    assert not det.evaluate("e => e.open"), "the definition opens by default"
    assert page.locator("#lens-def pre").first.is_hidden()
    page.click("#lens-def > summary")
    assert det.evaluate("e => e.open")
    assert page.eval_on_selector_all("#lens-def pre", "ps => ps.map(p => p.textContent)") == \
        [f["statement"] for f in want["functions"]], "a statement differs from lens.definition"
    src = page.inner_text("#lens-def .def-src")
    for value in want["source"].values():
        assert value in src, value
    page.click("#lens-def > summary")
    assert not det.evaluate("e => e.open")
    page.focus("#lens-def > summary")
    page.keyboard.press("Enter")
    assert det.evaluate("e => e.open"), "the keyboard does not open the definition"
    assert not page.errors, page.errors
    page.close()


# ---- T5D-11, T5D-17: still final frame, Play only on request, never under reduce or at 390 ----------

def test_drawer_opens_on_the_final_frame_and_never_autoplays(browser, app):
    page = open_drawer(browser, app)
    page.wait_for_timeout(800)
    anims = [a for a in page.evaluate(ANIMS) if a["inside"]]
    assert anims, "no motion is mounted at 1440"
    assert all(a["state"] == "paused" and abs(a["t"] - a["end"]) < 1 for a in anims), "the drawer autoplays"
    play = page.get_by_role("button", name="Play")
    assert play.count() == 1 and play.evaluate("b => b.tagName === 'BUTTON' && b.type === 'button'")
    assert not page.errors, page.errors
    page.close()


def test_play_runs_the_approved_motion_on_the_drawer_layout(browser, app):
    page = open_drawer(browser, app)
    page.get_by_role("button", name="Play").click()
    page.wait_for_timeout(100)
    anims = [a for a in page.evaluate(ANIMS) if a["inside"]]
    assert any(a["state"] == "running" and a["t"] < 1500 for a in anims), "Play does not start the motion"
    page.evaluate("() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = a.effect.getComputedTiming().endTime; })")
    box = page.evaluate("() => document.querySelector('#lens-diagram [data-hop=\"app\"] .pfd-box').getBoundingClientRect().height")
    assert box > 40, "the motion measured a hidden drawer"
    page.close()


@pytest.mark.parametrize("width, reduced", [(1440, True), (390, False), (744, True)])
def test_no_play_and_no_motion_under_reduced_motion_or_on_phones(browser, app, width, reduced):
    page = open_drawer(browser, app, width=width, height=844, reduced=reduced)
    assert page.get_by_role("button", name="Play").count() == 0, "Play shows"
    assert [a for a in page.evaluate(ANIMS) if a["inside"]] == [], "motion runs"
    assert "200 OK · 10 rows" in page.evaluate(VISIBLE_TEXT, "#lens-diagram")
    page.close()


# ---- T5D-10: the drawer layout is the "720 page" layout that diagram/check.py checks ----------------

@pytest.mark.parametrize("width", [1440, 744])
def test_drawer_layout_is_the_checked_page_layout(browser, app, width):
    page = open_drawer(browser, app, width=width)
    m = page.evaluate("""() => { const pfd = document.querySelector('#lens-diagram .pfd');
        const card = pfd.querySelector('.pfd-card').getBoundingClientRect();
        const lens = document.querySelector('#lens').getBoundingClientRect();
        const sizes = [...document.querySelectorAll('#lens *')].filter(e => [...e.childNodes].some(c => c.nodeType === 3 && c.textContent.trim()))
          .filter(e => e.getClientRects().length).map(e => parseFloat(getComputedStyle(e).fontSize));
        return {u: getComputedStyle(pfd).getPropertyValue('--u').trim(), card: card.width, right: card.right,
                lensRight: lens.right, min: Math.min(...sizes)}; }""")
    assert m["u"] == "1px", f"the drawer frame is not the checked page layout: {m}"
    assert abs(m["card"] - 672) < 0.6, f"the drawer frame is not the checked page layout: {m}"
    assert m["right"] <= m["lensRight"], f"the drawer frame is not the checked page layout: {m}"
    assert m["min"] >= 13, f"the drawer frame is not the checked page layout: {m}"
    page.close()


# ---- T5D-12, T5D-13: every fact also in text; the answer line is derived ----------------------------

def test_every_fact_is_in_visible_text(browser, app):
    page = open_drawer(browser, app, reduced=True)
    text = page.evaluate(VISIBLE_TEXT, "#lens-diagram") + " " + page.evaluate(VISIBLE_TEXT, "#lens-run")
    for fact in ("POST /api/search", '"q": "bcbs georgia"', '"page": 1', "POST /api/rpc/search_insurers",
                 "q, customer_id, lim, off", "200 OK · 10 rows", "api.search_insurers", "search_idx.search_core",
                 "Function definition", "lim: 10", "off: 0", "customer_id: a label the server sets"):
        assert fact in text, f"a fact is not in visible text: {fact}"
    page.close()


def test_unknown_status_shows_bare(browser, app):
    MODE["status"] = 299
    page = open_drawer(browser, app)
    text = page.inner_text("#lens")
    meta = page.inner_text("#lens-meta")
    assert meta.startswith("299 · 10 rows") and not re.search(r"\bOK\b", meta), f"the drawer types a status reason: {meta}"
    page.close()


# ---- T5D-2: page 2 shows page 2 ------------------------------------------------------------------

def test_page_two_shows_page_two_and_off_10(browser, app):
    page = open_drawer(browser, app, q="united helth")
    page.get_by_role("button", name="Show 10 more").click()
    page.wait_for_function("s => document.querySelector('#lens-diagram').textContent.includes(s)", arg='"page": 2')
    args = page.inner_text("#lens-args")
    assert "off: 10" in args and "lim: 10" in args and "off: 0" not in args, f"the drawer does not show page two's arguments: {args}"
    assert page.locator("#lens-diagram .pfd-card").count() == 1
    page.close()


# ---- T5D-1: a new search remounts the drawer --------------------------------------------------------

def test_new_search_remounts_the_drawer(browser, app):
    page = open_drawer(browser, app)
    page.get_by_role("button", name="Play").click()
    page.evaluate("() => { window.__old = document.querySelector('#lens-diagram > div').getAnimations({ subtree: true }); }")
    assert page.evaluate("() => window.__old.some(a => a.playState === 'running')"), "Play did not start the old motion"
    search(page, "united helth")
    page.wait_for_timeout(300)
    assert page.locator("#lens-diagram .pfd-card").count() == 1, "the drawer does not hold one diagram"
    assert page.get_by_role("button", name="Play").count() == 1, "the drawer does not hold one Play"
    assert "bcbs georgia" not in page.inner_text("#lens"), "the drawer keeps the previous search"
    # T6-12: the old root's own animations, kept from before the search, are cancelled (detached nodes leave
    # document.getAnimations(), so that list cannot show them).
    assert page.evaluate("() => window.__old.every(a => a.playState === 'idle')"), "the old motion still plays"
    anims = page.evaluate(ANIMS)
    assert all(a["state"] == "paused" and abs(a["t"] - a["end"]) < 1 for a in anims), "the new motion plays by itself"
    assert not page.errors, page.errors
    page.close()


# ---- Fix round (T6): timing, failures, searching state, listener leaks ------------------------------------

@pytest.mark.parametrize("elapsed", [0, 0.3, None, "missing"])
def test_drawer_shows_no_number_when_the_timing_is_missing_or_zero(browser, app, elapsed):
    """T6-4 (non-negotiable 4): a real Data API search is never 0 ms, so 0 or no timing shows no number."""
    MODE["elapsed"] = elapsed
    page = open_drawer(browser, app)
    meta = page.inner_text("#lens-meta")
    assert meta.startswith("200 OK · 10 rows"), meta
    assert not re.search(r"\d\s*ms\b|\bms\b", meta), f"a timing shows: {meta}"
    page.close()


def test_drawer_shows_the_measured_ms(browser, app):
    MODE["elapsed"] = 41.6
    page = open_drawer(browser, app)
    assert page.inner_text("#lens-meta") == "200 OK · 10 rows · 42 ms"
    page.close()


def _page(browser, app, post=None, block=None, width=1440):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    router = _router(app)

    def handle(route):
        if post and route.request.method == "POST":
            return post(route)
        if block and route.request.url.endswith(block):
            return route.fulfill(status=404, body="")
        return router(route)
    page.route(f"{BASE}/**", handle)
    page.goto(f"{BASE}/")
    return page


def test_a_200_answer_that_is_not_json_says_so(browser, app):
    """T6-8: a 200 with an HTML body is a failure, but not a "200 OK" one."""
    page = _page(browser, app, post=lambda r: r.fulfill(status=200, content_type="text/html", body="<html>proxy</html>"))
    page.fill("#q", "bcbs georgia")
    page.wait_for_selector("#lens-failed:not([hidden])", state="attached")
    text = page.text_content("#lens-failed")
    assert "The app server sent an answer that is not JSON." in text, f"the drawer does not say the answer is not JSON: {text}"
    assert not re.search(r"\bOK\b|\b200\b", text), f"the drawer gives a not-JSON answer a status: {text}"
    page.close()


def test_results_show_when_the_diagram_does_not_load(browser, app):
    """T6-9: a diagram.js 404 must not hide the search results."""
    page = _page(browser, app, block="/static/diagram.js")
    page.fill("#q", "bcbs georgia")
    try:
        page.wait_for_selector("#results:not([hidden]) .group", timeout=5000)
    except PlaywrightTimeout:
        raise AssertionError("the results do not show when the diagram does not load") from None
    page.get_by_role("button", name="How this search ran").click()
    assert "The diagram did not load." in page.inner_text("#lens-diagram"), "the drawer does not say the diagram is missing"
    assert page.inner_text("#lens-meta").startswith("200 OK · 10 rows"), "the drawer drops the facts with the diagram"
    page.close()


def test_results_show_when_the_drawer_cannot_draw_the_search(browser, app):
    """T6-9: the results come first; a drawer error says so and keeps them."""
    broken = payload(app, "bcbs georgia")
    del broken["lens"]["function"]
    page = _page(browser, app, post=lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(broken)))
    page.fill("#q", "bcbs georgia")
    try:
        page.wait_for_selector("#results:not([hidden]) .group", timeout=5000)
    except PlaywrightTimeout:
        raise AssertionError("the results do not show when the drawer fails") from None
    page.get_by_role("button", name="How this search ran").click()
    assert "The drawer could not show this search. The results are not affected." in page.inner_text("#lens"), \
        "the drawer does not say it failed"
    page.close()


COUNT_INIT = """(() => {
  window.__mql = 0; window.__ro = 0;
  const add = MediaQueryList.prototype.addEventListener, rem = MediaQueryList.prototype.removeEventListener;
  MediaQueryList.prototype.addEventListener = function (t, f, o) { if (t === 'change') window.__mql++; return add.call(this, t, f, o); };
  MediaQueryList.prototype.removeEventListener = function (t, f, o) { if (t === 'change') window.__mql--; return rem.call(this, t, f, o); };
  const RO = window.ResizeObserver;
  window.ResizeObserver = class extends RO {
    constructor(cb) { super(cb); this.__on = false; }
    observe(t, o) { if (!this.__on) { this.__on = true; window.__ro++; } return super.observe(t, o); }
    disconnect() { if (this.__on) { this.__on = false; window.__ro--; } return super.disconnect(); }
  };
})();"""


def test_five_searches_leave_one_set_of_listeners(browser, app):
    """T6-10: each mount removes the listeners and the observer of the mount before it."""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script(COUNT_INIT)
    page.route(f"{BASE}/**", _router(app))
    page.goto(f"{BASE}/")
    page.get_by_role("button", name="How this search ran").click()
    for q in ["bcbs georgia", "united helth", "uhc", "medicad", "bcbs georgia"]:
        search(page, q)
    counts = page.evaluate("() => [window.__mql, window.__ro]")
    assert counts == [2, 1], f"live matchMedia listeners, ResizeObservers after 5 searches: {counts}"
    page.close()


def test_drawer_says_searching_while_a_new_search_runs(browser, app):
    """T6-13: while search B runs, the drawer does not present search A as the current one."""
    page = open_drawer(browser, app)
    held = []
    page.route(f"{BASE}/api/search", lambda r: held.append(r))
    page.fill("#q", "united helth")
    page.press("#q", "Enter")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    assert held, "the search was not held"
    try:
        page.wait_for_selector("#lens-searching:not([hidden])", timeout=2000)
    except PlaywrightTimeout:
        raise AssertionError("the drawer does not say it is searching") from None
    assert page.locator("#lens-diagram .pfd-card").count() == 0, "the drawer still shows the previous search"
    assert "Searching for “united helth”." in page.inner_text("#lens"), "the drawer does not say it is searching"
    page.unroute(f"{BASE}/api/search")
    held[0].fallback()
    page.wait_for_selector("#lens-diagram .pfd-card")
    assert "united helth" in page.inner_text("#lens-diagram")
    page.close()


def test_rejected_words_are_in_no_shipped_file():
    """T6-6: "stays on the (app) server" was rejected (owner, 2026-10-24); the diagram, the stills and the server say
    the browser never gets the token (C7)."""
    for path in [STATIC / "diagram.js", ROOT / "diagram/diagram.js", ROOT / "server/main.py", ROOT / "diagram/index.html",
                 ROOT / "diagram/drawer.html", STATIC / "index.html", STATIC / "app.js"]:
        assert not re.search(r"stays on the (app )?server", path.read_text(), re.I), f"a rejected word is in {path.name}"


APPROVED_AUTH = "Authorization: OAuth token of the app's service principal. The browser never gets it."
AUTH_FILES = [STATIC / "diagram.js", ROOT / "diagram/diagram.js", ROOT / "server/main.py", ROOT / "diagram/check.py",
              ROOT / "diagram/index.html", ROOT / "diagram/drawer.html", STATIC / "index.html", STATIC / "app.js"]


def test_authorization_line_is_the_approved_form_in_every_shipped_file():
    """T6-16: box 2's final-frame wording is approved (owner, 2026-10-24, C7); it is the only allowed form."""
    from server.main import AUTH_NOTE
    assert "Authorization: " + AUTH_NOTE == APPROVED_AUTH, "server/main.py: the Authorization line is not the approved form"
    for path in AUTH_FILES:
        text = path.read_text()
        for note in re.findall(r'AUTH_NOTE\s*=\s*"([^"]*)"', text):
            assert "Authorization: " + note == APPROVED_AUTH, f"{path.name}: the Authorization line is not the approved form"
        for m in re.finditer(r'Authorization:\s*(" \+ AUTH_NOTE|[^"<\n]*)', text):
            tail = m.group(1)
            assert tail == '" + AUTH_NOTE' or "Authorization: " + tail == APPROVED_AUTH, \
                f"{path.name}: the Authorization line is not the approved form"


def test_drawer_shows_only_the_approved_authorization_line(browser, app):
    page = open_drawer(browser, app, reduced=True)
    text = page.evaluate(VISIBLE_TEXT, "#lens-diagram") + " " + page.evaluate(VISIBLE_TEXT, "#lens-run")
    lines = re.findall(r"Authorization:.*?\.[^.]*\.", text)
    assert lines and set(lines) == {APPROVED_AUTH}, f"the drawer's Authorization line is not the approved form: {lines}"
    shown = payload(app, "bcbs georgia")["lens"]["server_request"]["authorization"]
    assert "Authorization: " + shown == APPROVED_AUTH, "the payload's Authorization line is not the approved form"
    page.close()


# ---- T5D-3: a failed search shows its real status and no happy path ---------------------------------

def test_failed_search_shows_its_status_and_no_happy_path(browser, app):
    page = open_drawer(browser, app)
    MODE["fail"] = True
    search(page, "uhc")
    assert page.locator("#lens-diagram .pfd-card").count() == 0, "the diagram of the previous search stays"
    text = page.evaluate(VISIBLE_TEXT, "#lens")
    assert "502 Bad Gateway" in text and "403 Forbidden" in text, f"the drawer hides a status: {text}"
    assert not re.search(r"\bOK\b|\brows\b", text), f"the drawer shows the happy path on a failed search: {text}"
    page.close()


def test_short_query_empties_the_drawer(browser, app):
    page = open_drawer(browser, app)
    page.fill("#q", "b")
    page.wait_for_timeout(400)
    assert page.locator("#lens-diagram .pfd-card").count() == 0, "the drawer keeps the diagram on a short query"
    assert page.is_visible("#lens-empty"), "the drawer does not say it is empty"
    page.close()


# ---- T5D-4, T5D-5, T5D-15: words on the drawer --------------------------------------------------------

# "Select" alone is a plan name in the raw rows; a statement is SELECT ... FROM.
SELECT_FROM = r"\bselect\s.{0,160}?\bfrom\s"
CALL_FORM = [SELECT_FROM, r"=>", r"sql that ran", r"statement that ran", r"\bfrom\s+api\.", r"api\.search_insurers\s*\("]


def test_no_call_form_outside_the_definition(browser, app):
    page = open_drawer(browser, app)
    page.click("#lens-def > summary")
    whole, rest = page.evaluate("""() => { const c = document.querySelector('#lens').cloneNode(true);
        const d = c.querySelectorAll('#lens-def'); if (d.length !== 1 || d[0].tagName !== 'DETAILS') return [null, null];
        const whole = c.textContent; d[0].remove(); return [whole, c.textContent]; }""")
    assert whole is not None, "the definition block is not one #lens-def <details>"
    assert re.search(SELECT_FROM, whole, re.I | re.S), "the scan does not see the definition"
    for pattern in CALL_FORM:
        assert not re.search(pattern, rest, re.I | re.S), f"a call form shows outside the definition: {pattern}"
    page.close()


def test_tenant_value_and_data_api_host_are_not_on_the_drawer(browser, app):
    page = open_drawer(browser, app)
    page.click("#lens-def > summary")
    text = page.text_content("#lens")
    assert TENANT not in text, "the tenant value shows"
    assert "cloud.databricks.com" not in text and API not in text, "the Data API host shows"
    assert "customer_id: a label the server sets" in page.inner_text("#lens-args")
    page.close()


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_drawer_copy_lint(browser, app, pattern):
    if not hasattr(test_drawer_copy_lint, "text"):
        MODE["elapsed"] = 41.6  # a measured timing shows the "Measured by" note, so the lint reads it too
        page = open_drawer(browser, app, reduced=True)
        test_drawer_copy_lint.text = page.evaluate(VISIBLE_TEXT, "#lens")
        page.close()
    assert not re.search(pattern, test_drawer_copy_lint.text, re.I), f"the drawer copy says a rejected word: {pattern}"


# ---- T5D-16: Play closes the definition --------------------------------------------------------------

def test_play_closes_the_definition(browser, app):
    page = open_drawer(browser, app)
    page.click("#lens-def > summary")
    page.get_by_role("button", name="Play").click()
    assert not page.evaluate("document.querySelector('#lens-def').open"), "Play animates over an open definition"
    page.close()


# ---- Fix round 2 (T6-17..T6-30) -----------------------------------------------------------------------

FRONT_FAILURE = "Search is not available right now. Try again in a minute."
FRONT_BANNED = [r"\b\d{3}\b", r"json", r"search service", r"\bstatus", r"app server"]


@pytest.mark.parametrize("case", ["403", "502", "not-json", "network", "502-diagram-blocked"])
def test_front_screen_failure_is_plain_words(browser, app, case):
    """T6-17: every failure shows one plain pair on the front screen; the cause stays in the drawer."""
    post = block = None
    if case == "403":
        MODE["fail"] = True
    elif case.startswith("502"):
        MODE["fail"] = 502
    elif case == "not-json":
        post = lambda r: r.fulfill(status=200, content_type="text/html", body="<html>proxy</html>")
    elif case == "network":
        post = lambda r: r.abort()
    if case.endswith("diagram-blocked"):
        block = "/static/diagram.js"
    page = _page(browser, app, post=post, block=block)
    page.fill("#q", "bcbs georgia")
    page.wait_for_selector("#error:not([hidden])", timeout=5000)
    text = " ".join(page.inner_text("#error").split())
    for pattern in FRONT_BANNED:
        assert not re.search(pattern, text, re.I), f"the front screen names a technical cause ({pattern}): {text}"
    assert text == FRONT_FAILURE, f"the front screen failure is not the plain pair: {text}"
    page.get_by_role("button", name="How this search ran").click()
    assert page.inner_text("#lens-failed-body").strip(), "the drawer lost the cause"
    page.close()


def test_drawer_title_shows_when_the_diagram_did_not_mount(browser, app):
    """T6-19: the drawer's own title hides only under a mounted diagram, whose title bar says the same."""
    page = open_drawer(browser, app)
    assert page.locator("#lens-title").bounding_box()["width"] <= 1, "the drawer title shows over a mounted diagram"
    page.close()
    page = _page(browser, app, block="/static/diagram.js")
    page.fill("#q", "bcbs georgia")
    page.wait_for_selector("#results:not([hidden]) .group", timeout=5000)
    page.get_by_role("button", name="How this search ran").click()
    page.wait_for_selector("#lens-run:not([hidden])")
    box = page.locator("#lens-title").bounding_box()
    assert box and box["width"] > 10, "the drawer title is hidden while no diagram is mounted"
    page.close()


def test_box_1_answer_is_the_apps_own_status(browser, app):
    """T6-20: box 1 is the answer from this app (the browser's res.status); the facts line names the Data API."""
    differ = payload(app, "bcbs georgia")
    differ["lens"]["status"] = 203
    page = _page(browser, app, post=lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(differ)))
    page.fill("#q", "bcbs georgia")
    page.wait_for_selector("#results:not([hidden]) .group", timeout=5000)
    page.get_by_role("button", name="How this search ran").click()
    page.wait_for_selector("#lens-diagram .pfd-card")
    box1 = " ".join(page.eval_on_selector_all("#lens-diagram .pfd-answer", "es => es.map(e => e.textContent)"))
    assert "200 OK" in box1 and "203" not in box1, f"box 1 does not show the app's own status: {box1}"
    assert page.inner_text("#lens-meta").startswith("203"), "the Data API line does not show the Data API's status"
    assert "Answer from the Data API" in page.inner_text("#lens-run"), "the Data API line does not name the Data API"
    page.close()


@pytest.mark.parametrize("width", [390, 744, 1440])
def test_evidence_text_stays_inside_the_drawer(app, browser, width):
    """Session 6 item 1: a long test name in a source line wraps; nothing in the evidence panel is cut off."""
    page = open_drawer(browser, app, width=width, height=844)
    page.evaluate("document.getElementById('proof').open = true")
    over = page.evaluate("""() => [...document.querySelectorAll('#proof .qa dd, #proof .qa dd *')]
        .filter(e => e.scrollWidth > e.clientWidth + 1 || e.getBoundingClientRect().right >
                     e.closest('dd').getBoundingClientRect().right + 1)
        .map(e => e.textContent.trim().slice(0, 60))""")
    page.close()
    assert over == [], over


def test_data_api_box_names_both_schemas_and_the_standard_defaults():
    """Sage Gold item 6: the Data API box gives the app's role no fewer rights than SECURITY.md lists."""
    js = (STATIC / "diagram.js").read_text()
    box = js[js.index('{ id: "data-api"'):js.index('{ id: "function"')]
    short = re.search(r"short: \[(.*?)\],\n", box, re.S).group(1)
    for part, need in (("short", short), ("full", box)):
        for word in ("2 functions" if part == "short" else "two search functions", "schemas", "standard defaults"):
            assert word in need, f"the {part} Data API text leaves out {word!r}"
    assert (ROOT / "diagram/diagram.js").read_text() == js, "app/diagram/diagram.js and app/static/diagram.js differ"
