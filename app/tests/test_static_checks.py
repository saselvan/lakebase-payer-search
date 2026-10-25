"""Checks on the files the browser gets: contract fields (item 6, risk b), copy (risks c, D), no env templating (A3).

The static files are served as they are on disk (no build step), so reading the disk reads what ships.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from server.dataapi import CONTRACT_FIELDS
from server.main import load_definition

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
APP_JS = (STATIC / "app.js").read_text()
INDEX = (STATIC / "index.html").read_text()
TOP_HITS = ROOT.parent / "data/test_top_hit_cases.json"

# Contract: DATA_CONTRACT.md section 1. `score` exists but the contract says never show it to users.
FRONT_DOOR_FIELDS = set(CONTRACT_FIELDS) - {"score"}


def rendered_fields(js: str) -> set[str]:
    """Every field of a result row the page reads, and every field it labels in the DOM."""
    reads = set(re.findall(r"\brow\??\.([A-Za-z_]\w*)", js))
    labels = set(re.findall(r"dataset\.field\s*=\s*[\"'](\w+)[\"']", js))
    labels |= set(re.findall(r"metaItem\([^)]*?,[^)]*?,\s*[\"'](\w+)[\"']", js))
    labels |= set(re.findall(r"cell\([^)]*?,[^)]*?,\s*[\"'](\w+)[\"']", js))
    return reads | labels


def test_contract_fields():
    fields = rendered_fields(APP_JS)
    assert fields, "found no rendered fields: the scan is broken"
    assert fields <= FRONT_DOOR_FIELDS, f"page renders fields the API lacks or forbids: {fields - FRONT_DOOR_FIELDS}"
    assert {"payer_name", "payer_id", "state", "plan_type"} <= fields


def test_rows_are_read_only_by_name():
    # The contract scan above sees `row.x`. A bracket read or a destructure would hide a field from it.
    assert not re.search(r"\brow\s*\[", APP_JS), "bracket access on a row"
    assert not re.search(r"\}\s*=\s*row\b", APP_JS), "destructured row"
    assert not re.search(r"Object\.(keys|values|entries)\(\s*row\b", APP_JS), "row iterated by key"


TEXT = INDEX + "\n" + APP_JS

FORBIDDEN = [r"\binstant", r"\bsecure\b", r"no credential", r"real insurer", r"generally available",
             r"\bGA\b", r"\bpreview\b", r"\bblazing", r"\blightning", r"\bguarantee", r"\bHIPAA\b",
             r"\bbest\b",  # G1: ties are broken alphabetically, so no row is "best"
             r"no rights? on any table",  # C6: the role has no rights on any data table; "any table" hides the platform views
             r"stays on the (app )?server",
             r"sends it only to"]  # C7: the panel cut "sends it only to the Data API" (2026-10-24)  # C7: the token goes to the Data API in a header (owner, 2026-10-24)


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_copy_lint_forbidden(pattern):
    visible = re.sub(r"<[^>]+>", " ", TEXT)
    assert not re.search(pattern, visible, re.I), pattern


def test_copy_lint_synthetic_label_is_on_the_page():
    # Risk c: synthetic IDs and plans beside real public names must be labelled.
    assert "Payer names are public. Payer IDs and plans are synthetic." in INDEX
    assert '"State · Payer ID · synthetic"' in APP_JS and '"Payer ID · synthetic"' in APP_JS  # the labels over the IDs


def test_examples_come_from_the_shared_top_hit_list():
    examples = json.loads("[" + re.search(r"const EXAMPLES = \[(.*?)\];", APP_JS, re.S).group(1) + "]")
    shared = {r["query"] for r in json.loads(TOP_HITS.read_text())}
    assert examples and set(examples) <= shared, set(examples) - shared


@pytest.mark.parametrize("path", sorted(p for p in STATIC.rglob("*") if p.suffix in {".html", ".js", ".css"}))
def test_static_files_hold_no_env_or_template(path):
    text = path.read_text()
    for needle in ("process.env", "import.meta", "DATABRICKS_", "DATA_API_BASE", "CLIENT_SECRET", "{{", "{%",
                   "Bearer", "localStorage", "sessionStorage", "document.cookie"):
        assert needle not in text, f"{path.name}: {needle}"


def test_results_render_as_text_never_html():
    assert "innerHTML" not in APP_JS and "outerHTML" not in APP_JS and "insertAdjacentHTML" not in APP_JS
    assert "eval(" not in APP_JS and "new Function" not in APP_JS


# ---- The page in a real browser (G7: checks read computed styles, not the CSS text) ----
# The rows are real answers of api.search_insurers on a t-finder branch (tests/fixtures/search_rows.json);
# a Playwright route serves static/ and answers /api/search from them, so no server and no network.

FIXTURES = json.loads((ROOT / "tests/fixtures/search_rows.json").read_text())["queries"]
BASE = "http://finder.test"
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".woff2": "font/woff2"}


DEFINITION = load_definition()


def _answer(q: str, page: int) -> dict:
    rows = FIXTURES.get(q, {}).get(f"page{page}", [])
    body = {"q": q, "customer_id": "fixture-tenant", "lim": 10, "off": (page - 1) * 10}
    return {"query": q, "page": page, "rows": rows, "end": page >= 2 or len(rows) < 10, "elapsed_ms": 41.6,
            "lens": {"browser_request": {"method": "POST", "path": "/api/search", "body": {"q": q, "page": page}},
                     "server_request": {"method": "POST", "url": "https://example.invalid/api/rpc/search_insurers",
                                        "body": body, "authorization": "OAuth token of the app's service principal."},
                     "status": 200, "function": {"name": "api.search_insurers", "arguments": body},
                     "definition": DEFINITION}}


def _route(route):
    req = route.request
    path = req.url.removeprefix(BASE)
    if path == "/api/search":
        body = json.loads(req.post_data or "{}")
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(_answer(body["q"], body["page"])))
    file = STATIC / "index.html" if path == "/" else ROOT / path.lstrip("/")
    if not file.resolve().is_relative_to(STATIC) or not file.is_file():
        return route.fulfill(status=404, body="")
    return route.fulfill(status=200, content_type=TYPES.get(file.suffix, "application/octet-stream"), body=file.read_bytes())


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_page(browser, q: str, width: int = 1440, height: int = 900):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.route(f"{BASE}/**", _route)
    page.goto(f"{BASE}/")
    page.get_by_role("button", name=q, exact=True).click() if q in EXAMPLES_ON_PAGE else page.fill("#q", q)
    page.wait_for_selector("#results:not([hidden]) .group")
    page.evaluate("document.fonts.ready")
    return page


EXAMPLES_ON_PAGE = json.loads("[" + re.search(r"const EXAMPLES = \[(.*?)\];", APP_JS, re.S).group(1) + "]")


@pytest.mark.parametrize("name, state, key", [
    ("UnitedHealthcare Choice PPO - AK", "AK", "UnitedHealthcare Choice PPO"),
    ("CHIP - GA", "GA", "CHIP"),
    ("Aetna Choice POS II - TX", "GA", "Aetna Choice POS II - TX"),  # suffix is not this row's state: keep it
    ("Blue Cross and Blue Shield of Georgia HMO", "GA", "Blue Cross and Blue Shield of Georgia HMO"),
    ("Plan - GA - GA", "GA", "Plan - GA"),
])
def test_group_key_strips_only_the_rows_own_state(browser, name, state, key):
    page = browser.new_page()
    page.route(f"{BASE}/**", _route)
    page.goto(f"{BASE}/")
    assert page.evaluate("r => groupKey(r)", {"payer_name": name, "state": state}) == key
    page.close()


def test_groups_keep_rank_order_and_show_more_extends_the_open_group(browser):
    page = open_page(browser, "united helth")
    assert page.locator(".group").count() == 1
    assert page.inner_text(".group.lead .count") == "10 states on this page"
    page.get_by_role("button", name="Show 10 more").click()
    page.wait_for_function("document.querySelectorAll('.group.lead .cell').length === 20")
    assert page.locator(".group").count() == 1
    assert page.inner_text(".group.lead .count") == "20 states on this page"
    page.close()


@pytest.mark.parametrize("q", ["united helth", "bcbs georgia", "medicad"])
def test_counts_say_on_this_page_and_every_id_is_marked_synthetic(browser, q):
    page = open_page(browser, q)
    texts = page.eval_on_selector_all(".count, #status", "els => els.map(e => e.textContent)")
    numbered = [t for t in texts if re.search(r"\d", t)]
    assert numbered and all("on this page" in t for t in numbered), numbered  # G3
    unmarked = page.evaluate("""() => [...document.querySelectorAll('[data-field="payer_id"]')].filter(id => {
        const box = id.closest('.states') ? id.closest('.group').querySelector('.states-head')
                                          : id.closest('.lines') && id.closest('.lines').previousElementSibling;
        return !box || !/synthetic/i.test(box.textContent); }).length""")
    assert unmarked == 0
    page.close()


def test_no_timing_and_no_score_outside_the_drawer(browser):
    page = open_page(browser, "bcbs georgia")
    front = page.evaluate("() => { const c = document.body.cloneNode(true); c.querySelector('#lens').remove(); "
                          "return c.textContent; }")
    assert not re.search(r"\d\s*ms\b|millisecond|\bscore\b", front, re.I), "timing or score on the front screen"  # G4
    page.get_by_role("button", name="How this search ran").click()
    assert re.search(r"\b42 ms\b", page.inner_text("#lens-meta"))
    assert page.inner_text("#lens-meta").startswith("200 OK · "), "the status shows without its reason phrase"
    assert "wake-up" in page.inner_text("#lens")
    hop = page.inner_text('#lens-diagram [data-hop="function"]')  # the call reaches the function, not only the URL
    assert "api.search_insurers" in hop and "/api/rpc/search_insurers" in page.inner_text('#lens-diagram [data-hop="app"]')
    assert '"score"' in page.text_content("#lens-rows")
    page.close()


def test_drawer_answers_name_their_tests_and_dates():
    for needle in ("test_app_sp_grants", "test_deployed_responses_hold_no_secret_by_shape", "2026-10-24",
                   "Not read live", "api.search_insurers"):
        assert needle in INDEX, needle  # G5
    assert "GRANT " not in INDEX and "SELECT " not in INDEX  # no SQL typed by hand


_STYLE_PROBE = """() => {
  const rgb = c => (c.match(/[\\d.]+/g) || []).map(Number);
  const lum = c => { const [r, g, b] = rgb(c).slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
                     return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const opaque = c => !/rgba\\(.*, 0\\)$|transparent/.test(c) && rgb(c).length >= 3;
  const bgOf = e => { for (; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; if (opaque(c)) return c; } return 'rgb(255, 255, 255)'; };
  const hue = c => { const [r, g, b] = rgb(c).slice(0, 3).map(v => v / 255), mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    if (d < 0.25 || mx < 0.25) return null;  // grey, black or white: not a status colour
    const h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; return (h * 60 + 360) % 360; };
  const statusHue = h => h !== null && (h < 70 || h > 330 || (h >= 70 && h < 170));  // red, amber, yellow, green
  const out = { minContrast: 99, worst: '', small: [], overflow: [], status: [], smallTargets: [] };
  for (const e of document.querySelectorAll('body *')) {
    const box = e.getBoundingClientRect();
    if (!e.closest('#lens') && box.right > innerWidth + 0.5) out.overflow.push(e.className || e.tagName);
    if (e.closest('#results') && !e.closest('mark')) {
      const cs = getComputedStyle(e);
      for (const c of [cs.color, cs.backgroundColor, cs.borderTopColor, cs.borderBottomColor])
        if (opaque(c) && statusHue(hue(c))) { out.status.push(`${e.className || e.tagName} ${c}`); break; }
    }
    if (e.matches('button') && e.offsetParent && Math.min(box.width, box.height) < 32) out.smallTargets.push(e.textContent);
    if (!e.offsetParent || ![...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    const cs = getComputedStyle(e), a = lum(cs.color), b = lum(bgOf(e)), ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (ratio < out.minContrast) { out.minContrast = ratio; out.worst = `${e.className || e.tagName}: ${e.textContent.trim().slice(0, 30)}`; }
    if (parseFloat(cs.fontSize) < 13) out.small.push(e.className || e.tagName);
  }
  return out;
}"""


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("q", ["united helth", "bcbs georgia", "medicad"])
def test_floors_and_no_status_colour_on_results(browser, q, width):
    page = open_page(browser, q, width=width, height=900 if width > 1000 else 844)
    r = page.evaluate(_STYLE_PROBE)
    assert r["minContrast"] >= 4.5, r["worst"]  # non-negotiable 7
    assert not r["small"], f"text below 13px: {r['small'][:5]}"
    assert not r["overflow"], f"wider than the screen: {r['overflow'][:5]}"
    assert not r["status"], f"status colour on a result: {r['status'][:5]}"  # G8
    assert not r["smallTargets"], r["smallTargets"][:5]
    page.close()


MEASURED_NOTE = ("Measured by the app server: from its first request to the Data API until the answer, including retries. "
                 "A first search after a pause can include a database wake-up.")
TOKEN_ANSWER = "No. The app server holds the token; the browser never gets it."


def test_measured_note_is_the_only_allowed_form():
    """T6-22, T6-23: the note says what server/dataapi.py times (t0 before the first request)."""
    notes = re.findall(r'id="lens-note">([^<]*)<', INDEX)
    assert notes == [MEASURED_NOTE], f"the Measured by note is not the approved form: {notes}"


def test_evidence_token_answer_is_the_only_allowed_form():
    """T6-24: C7 says the browser never gets the token; the answer says no more than that."""
    m = re.search(r"<dt>Can the browser get the token\?</dt>\s*<dd>(.*?)\s*<span", INDEX, re.S)
    assert m and " ".join(m.group(1).split()) == TOKEN_ANSWER, "the evidence answer about the token is not the approved form"
