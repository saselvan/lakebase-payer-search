"""Item 5 (I5, I6, I12-I17): live checks against the deployed app. The CLI OAuth token is used in memory;
it is never written. No browser cookies are stored.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOP_HITS = ROOT.parent / "data/test_top_hit_cases.json"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
COOKIE_RE = re.compile(r"(?i)(set-cookie:|cookie:)\s*\S+")

pytestmark = pytest.mark.deployed


def _dotenv() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


@pytest.fixture(scope="module")
def deployed():
    env = _dotenv()
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=env["DATABRICKS_PROFILE"])
    app = w.apps.get(env["APP_NAME"])
    url = app.url.rstrip("/")
    token = json.loads(subprocess.check_output(
        ["databricks", "auth", "token", "-p", env["DATABRICKS_PROFILE"], "-o", "json"]))["access_token"]
    dep = app.active_deployment
    dep_state = getattr(dep.status.state, "value", dep.status.state) if dep and dep.status else None
    app_state = getattr(app.app_status.state, "value", app.app_status.state) if app.app_status else None
    assert dep and dep_state == "SUCCEEDED", f"deployment is not SUCCEEDED: {dep}"
    assert app_state == "RUNNING", f"app is not RUNNING: {app_state}"
    yield {"url": url, "token": token, "app": app, "env": env, "ws": w,
           "headers": {"Authorization": f"Bearer {token}"}}


def _get(d, path: str) -> httpx.Response:
    return httpx.get(d["url"] + path, headers=d["headers"], timeout=30, follow_redirects=False)


def _search(d, q: str, page: int = 1) -> httpx.Response:
    return httpx.post(d["url"] + "/api/search", headers={**d["headers"], "Content-Type": "application/json"},
                      json={"q": q, "page": page}, timeout=30, follow_redirects=False)


def test_served_static_files_match_local_hashes(deployed):
    files = subprocess.check_output(["git", "ls-files", "static/"], cwd=ROOT, text=True).splitlines()
    pairs = [("/", ROOT / "static/index.html")] + [(f"/{rel}", ROOT / rel) for rel in files]
    for path, local in pairs:
        r = _get(deployed, path)
        assert r.status_code == 200, f"{path} status {r.status_code}"
        h_local = hashlib.sha256(local.read_bytes()).hexdigest()
        h_served = hashlib.sha256(r.content).hexdigest()
        assert h_local == h_served, f"{path} hash mismatch local={h_local} served={h_served}"


def test_one_search_returns_rows(deployed):
    r = _search(deployed, "bcbs georgia")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert len(data["rows"]) == 10
    assert "Blue Cross and Blue Shield of Georgia" in data["rows"][0]["payer_name"]
    assert data["elapsed_ms"] > 0
    assert "lens" in data


def test_twelve_top_hit_queries(deployed):
    cases = json.loads(TOP_HITS.read_text())
    assert len(cases) >= 11
    failed = []
    for case in cases:
        r = _search(deployed, case["query"])
        if r.status_code != 200:
            failed.append((case["query"], r.status_code, None))
            continue
        top = r.json()["rows"][0]["payer_name"]
        if case["expected_brand"] not in top:
            failed.append((case["query"], 200, top))
    assert not failed, f"top-hit misses: {failed}"


def test_security_headers_on_the_deployed_app(deployed):
    for path in ("/", "/static/app.js"):
        r = _get(deployed, path)
        assert r.headers.get("content-security-policy") == CSP, path
        assert r.headers.get("x-content-type-options") == "nosniff"
    r = _search(deployed, "aetna")
    assert r.headers.get("content-security-policy") == CSP
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("cache-control") == "no-store"


def test_no_response_holds_a_secret_or_host(deployed):
    env = deployed["env"]
    r = _search(deployed, "bcbs georgia")
    text = r.text + json.dumps(dict(r.headers))
    page = _get(deployed, "/").text + _get(deployed, "/static/app.js").text
    blob = text + page
    assert "Bearer " not in blob
    assert env["FINDER_TENANT"] not in blob
    assert urlparse(env["DATA_API_BASE_PRODUCTION"]).netloc not in blob
    assert env["APP_SP_CLIENT_SECRET"] not in blob
    assert not re.search(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", blob)


def test_audit_row_names_the_app_sp(deployed):
    env = deployed["env"]
    q = f"bcbs georgia s4 {int(time.time())}"
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    r = _search(deployed, q)
    assert r.status_code == 200 and r.json()["rows"]
    sql = ("select caller, q, result_count from audit.search_log "
           "where customer_id = :'t' and q = :'q' and searched_at >= (:'ts' || '+00')::timestamptz "
           "order by searched_at desc limit 1")
    args = [str(ROOT / "scripts/psql_ro.sh"), "-At", "-F", "\t", "-v", f"t={env['FINDER_TENANT']}",
            "-v", f"q={q}", "-v", f"ts={started}", "-f", "-"]
    p = subprocess.run(args, input=sql, capture_output=True, text=True,
                       env={**os.environ, "TARGET": "production"})
    assert p.returncode == 0, p.stderr[-400:]
    lines = [ln for ln in p.stdout.splitlines() if ln and not ln.startswith("host=")]
    assert lines, f"no audit row for q={q!r}"
    caller, logged_q, count = lines[0].split("\t")
    assert caller == env["APP_SP_CLIENT_ID"], f"caller is {caller}"
    assert logged_q == q and int(count) >= 1


def test_drawer_shows_a_real_ms(deployed):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(extra_http_headers=deployed["headers"])
        page.goto(deployed["url"] + "/")
        page.get_by_role("button", name="bcbs georgia", exact=True).click()
        page.locator("#results .group.lead").wait_for(timeout=30000)
        page.get_by_role("button", name="How this search ran").click()
        meta = page.inner_text("#lens-meta")
        browser.close()
    assert re.search(r"\b([1-9]\d*) ms\b", meta), f"no real ms in drawer: {meta!r}"
    assert "0 ms" not in meta


def test_token_leak_on_the_deployed_page(deployed):
    """I12: the leak scan runs against the apps get URL, after a real search with rows."""
    from playwright.sync_api import sync_playwright
    env = deployed["env"]
    bodies = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(extra_http_headers=deployed["headers"])
        page.on("response", lambda r: bodies.append((r.url, r.status, r.body() if r.status == 200 else b"")))
        page.goto(deployed["url"] + "/")
        assert page.url.startswith(deployed["url"])
        page.get_by_role("button", name="bcbs georgia", exact=True).click()
        page.locator("#results .group.lead").wait_for(timeout=30000)
        page.get_by_role("button", name="How this search ran").click()
        html = page.content()
        lens = page.inner_text("#lens")
        browser.close()
    served_index = next(b for u, s, b in bodies if urlparse(u).path in ("/", "") and s == 200)
    assert hashlib.sha256(served_index).hexdigest() == hashlib.sha256(
        (ROOT / "static/index.html").read_bytes()).hexdigest()
    parts = []
    for _, _, b in bodies:
        parts.append(b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b))
    blob = html + lens + "".join(parts)
    assert "Bearer " not in blob
    assert env["FINDER_TENANT"] not in blob
    assert urlparse(env["DATA_API_BASE_PRODUCTION"]).netloc not in blob
    assert env["APP_SP_CLIENT_SECRET"] not in blob
    assert any(urlparse(u).path == "/api/search" and s == 200 for u, s, _ in bodies)


def test_no_cookie_line_in_tracked_files_or_runs():
    skip = {"tests/test_deployed_app.py", "scripts/mutate.py"}
    found = []
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    paths = [ROOT / p for p in tracked if p not in skip] + list((ROOT / "runs").rglob("*"))
    for path in paths:
        if not path.is_file() or path.suffix in {".png", ".woff2", ".mp4", ".jpg"}:
            continue
        try:
            text = path.read_text(errors="replace")
        except Exception:
            continue
        if COOKIE_RE.search(text):
            found.append(str(path.relative_to(ROOT)))
    assert not found, f"cookie or session lines: {found[:10]}"
