"""Session 5 review panel (DBA/security seat, objections J). Reads only.

Live checks run on production with no app-SP secret: the fixtures below replace the conftest chain for this
module, so nothing is cached from a t-finder branch run (I3). The deployed scan checks secrets by shape,
because the deployed app's own secret comes from the Apps runtime and is never on this laptop (J13).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from tests.test_live_branch import test_app_sp_grants, test_app_sp_role_is_a_data_api_role

test_app_sp_grants = pytest.mark.live(test_app_sp_grants)
test_app_sp_role_is_a_data_api_role = pytest.mark.live(test_app_sp_role_is_a_data_api_role)

ROOT = Path(__file__).resolve().parents[1]

# What PUBLIC holds on production (read 2026-10-24, runs/2026-10-24/s5/item2-public-inventory-production.txt).
PUBLIC_DB = {"CONNECT", "TEMPORARY"}
PUBLIC_SCHEMAS = {("public", "USAGE")}
PUBLIC_RELATIONS = {("databricks_list_roles", "SELECT"), ("databricks_synced_table_managers", "SELECT")}
PUBLIC_FUNCTIONS = {("grant_all_on_new_sequences()", "cloud_admin"), ("grant_select_on_new_objects()", "cloud_admin"),
                    ("grant_usage_on_new_schema()", "cloud_admin"), ("pgrst.pre_config()", "databricks_control_plane")}

SHAPES = {
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "oauth-secret": re.compile(r"\bdose[0-9a-f]{16,}"),
    "pat": re.compile(r"\bdapi[0-9a-f]{16,}"),
    "bearer": re.compile(r"Bearer [A-Za-z0-9._-]{8,}"),
    "data-api-host": re.compile(r"[a-z0-9-]+\.database\.[a-z0-9.-]+\.databricks\.com"),
}


def _dotenv() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


def leak_hits(blob: str, env: dict[str, str]) -> list[str]:
    hits = [kind for kind, rx in SHAPES.items() if rx.search(blob)]
    if env.get("FINDER_TENANT") and env["FINDER_TENANT"] in blob:
        hits.append("tenant")
    host = urlparse(env.get("DATA_API_BASE_PRODUCTION", "")).netloc
    if host and host in blob:
        hits.append("production-host")
    return hits


# ---- Offline: the scan and the overclaim lint, with their red proofs -------------------------------------------

def test_leak_scan_finds_each_planted_value():
    env = {"FINDER_TENANT": "tenant-x1", "DATA_API_BASE_PRODUCTION": "https://ep-a.database.x.databricks.com/api"}
    assert leak_hits("<html>Payer finder</html> Authorization: OAuth token of the app's service principal.", env) == []
    planted = {
        "jwt": "eyJhbGciOiJSUzI1.eyJzdWIiOiIxMjM0.c2lnbmF0dXJlMTIz",
        "oauth-secret": "dose0123456789abcdef0123",
        "pat": "dapi0123456789abcdef0123",
        "bearer": "Bearer abcdefgh1234",
        "data-api-host": "ep-b.database.y.databricks.com",
        "tenant": "tenant-x1",
        "production-host": "ep-a.database.x.databricks.com",
    }
    for kind, value in planted.items():
        assert kind in leak_hits(f"ok {value} ok", env), kind


OVERCLAIM = re.compile(r"(?i)(PUBLIC (has|holds|gets|can do) (no|nothing)|no other (right|grant|privilege)s?\b|"
                       r"\bonly (two|2) (EXECUTE|rights|grants)|holds no other|can do nothing else)")
PUBLIC_DENIAL = re.compile(r"(?i)PUBLIC (has|holds|gets|can do) (no|nothing)")


def overclaims(text: str) -> list[str]:
    """J1: a line on the SP's rights that says 'only', 'no other' or 'nothing' must name the PUBLIC defaults in
    the same line; a line that denies PUBLIC any right is always an overclaim."""
    out = []
    for ln in text.splitlines():
        if PUBLIC_DENIAL.search(ln) or (OVERCLAIM.search(ln) and "PUBLIC" not in ln):
            out.append(ln.strip())
    return out


def test_overclaim_lint_catches_a_planted_line():
    assert overclaims("Direct grants: EXECUTE on two functions. PUBLIC defaults add platform views and TEMP.") == []
    assert overclaims("The SP holds no other rights.")
    assert overclaims("PUBLIC has no rights on this database.")


def test_no_public_overclaim_on_screen_or_in_the_claims():
    files = [ROOT / "static/index.html", ROOT / "static/app.js", ROOT / "docs/CATECHISM.md", ROOT / "docs/BRIEF.md"]
    found = {str(f.relative_to(ROOT)): overclaims(f.read_text()) for f in files}
    assert not any(found.values()), found


SRC_LINE = re.compile(r'<span class="src">Checked (?:on the deployed app )?by <code>(\w+)</code>[^<]*?(\d{4}-\d{2}-\d{2})')
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def catechism_rows(text: str) -> dict[str, tuple[str, str]]:
    """Row id -> (verified date, evidence text) for each claims row."""
    rows = {}
    for ln in text.splitlines():
        cells = [c.strip() for c in ln.split("|")]
        if len(cells) > 6 and re.fullmatch(r"C\d+", cells[1]):
            m = DATE.search(cells[4])
            rows[cells[1]] = (m.group(0) if m else "", cells[5])
    return rows


def page_source_mismatches(page: str, catechism: str) -> list[str]:
    """J15: each evidence-page source line names a test and a date; the date must equal the verified date of a
    catechism row whose evidence names that test."""
    rows, out = catechism_rows(catechism), []
    found = SRC_LINE.findall(page)
    if not found:
        return ["no source line on the page"]
    for test, date in found:
        owners = [(rid, d) for rid, (d, ev) in rows.items() if f"`{test}`" in ev or f"::{test}`" in ev]
        if not owners:
            out.append(f"{test}: no catechism row names it")
        elif all(d != date for _, d in owners):
            out.append(f"{test}: page {date}, rows {owners}")
    return out


def test_page_source_dates_match_catechism_rows():
    page = (ROOT / "static/index.html").read_text()
    assert page_source_mismatches(page, (ROOT / "docs/CATECHISM.md").read_text()) == []


def test_page_source_check_catches_a_stale_date():
    cat = "| C6 | x | 1 | 2026-10-24 (panel) | `test_app_sp_grants` on production | OK |"
    ok = '<span class="src">Checked by <code>test_app_sp_grants</code> on production, 2026-10-24. Not read live.</span>'
    assert page_source_mismatches(ok, cat) == []
    assert page_source_mismatches(ok.replace("2026-10-24", "2026-10-23"), cat)
    assert page_source_mismatches(ok.replace("test_app_sp_grants", "test_other"), cat)


def test_grants_answer_names_the_defaults_in_one_clause():
    """Entry 19 (DECISIONS.md 2026-10-24): one calm clause on the page, no list of the default kinds."""
    page = (ROOT / "static/index.html").read_text()
    assert "Like every role in the database, it also has the defaults that all roles get, such as connect." in page
    assert "temporary tables" not in page and "extension functions" not in page


# ---- Live, production, read-only -------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def live_env():
    env = _dotenv()
    for k in ("DATABRICKS_PROFILE", "LAKEBASE_PROJECT", "LAKEBASE_ENDPOINT", "APP_NAME"):
        if not env.get(k):
            pytest.skip(f"panel: .env has no {k}")
    return env


@pytest.fixture(scope="module")
def target():
    if os.environ.get("FINDER_TARGET") != "production":
        pytest.skip("panel: run with FINDER_TARGET=production, so the log header names the target")
    return "production"


@pytest.fixture(scope="module")
def ws(live_env):
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient(profile=live_env["DATABRICKS_PROFILE"])


@pytest.fixture(scope="module")
def app_sp(ws, live_env):
    return ws.apps.get(live_env["APP_NAME"]).service_principal_client_id


@pytest.fixture(scope="module")
def ro_sql(live_env, ws, target):
    ep = f"projects/{live_env['LAKEBASE_PROJECT']}/branches/{target}/endpoints/{live_env['LAKEBASE_ENDPOINT']}"
    expected = ws.api_client.do("GET", f"/api/2.0/postgres/{ep}")["status"]["hosts"]["host"]

    def run(sql: str, **variables: str) -> list[list[str]]:
        args = [str(ROOT / "scripts/psql_ro.sh"), "-At", "-F", "\t", "-f", "-"]
        for k, v in variables.items():
            args += ["-v", f"{k}={v}"]
        p = subprocess.run(args, input=sql, capture_output=True, text=True, env={**os.environ, "TARGET": target})
        out = p.stdout.splitlines()
        assert out and out[0] == f"host={expected}", f"the read-only session did not reach the {target} endpoint"
        assert p.returncode == 0, f"read-only SQL failed on {target}: {p.stderr.strip()[-300:]}"
        return [line.split("\t") for line in out[1:] if line]
    return run


PUBLIC_SQL = r"""
select 'db', a.privilege_type, '' from pg_database d, aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a
  where a.grantee = 0 and d.datname = current_database()
union all select 'schema', n.nspname, a.privilege_type from pg_namespace n, aclexplode(n.nspacl) a
  where a.grantee = 0 and n.nspname !~ '^pg_' and n.nspname <> 'information_schema'
union all select 'relation', c.oid::regclass::text, a.privilege_type
  from pg_class c join pg_namespace n on n.oid = c.relnamespace, aclexplode(c.relacl) a
  where a.grantee = 0 and n.nspname !~ '^pg_' and n.nspname <> 'information_schema'
union all select 'function', p.oid::regprocedure::text, pg_get_userbyid(p.proowner)
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname !~ '^pg_' and n.nspname <> 'information_schema' and has_function_privilege('public', p.oid, 'EXECUTE')
  and not exists (select 1 from pg_depend d where d.classid = 'pg_proc'::regclass and d.objid = p.oid and d.deptype = 'e')
"""


@pytest.mark.live
def test_public_defaults_on_production_are_the_pinned_set(ro_sql):
    """Item 2: what PUBLIC holds on production, pinned. A revoke or a new default turns this red, and the claims
    (C6, C8, REGISTER) must change with it."""
    got: dict[str, set] = {}
    for kind, a, b in ro_sql(PUBLIC_SQL):
        got.setdefault(kind, set()).add(a if kind == "db" else (a, b))
    print({k: sorted(v) for k, v in got.items()})
    assert got.get("db", set()) == PUBLIC_DB
    assert got.get("schema", set()) == PUBLIC_SCHEMAS
    assert got.get("relation", set()) == PUBLIC_RELATIONS
    assert got.get("function", set()) == PUBLIC_FUNCTIONS


# ---- Deployed app ----------------------------------------------------------------------------------------------

@pytest.mark.deployed
def test_deployed_responses_hold_no_secret_by_shape(live_env):
    """J13: the deployed app's own secret is checked by shape, plus the tenant and the production host by value."""
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=live_env["DATABRICKS_PROFILE"])
    url = w.apps.get(live_env["APP_NAME"]).url.rstrip("/")
    token = json.loads(subprocess.check_output(
        ["databricks", "auth", "token", "-p", live_env["DATABRICKS_PROFILE"], "-o", "json"]))["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    files = subprocess.check_output(["git", "ls-files", "static/"], cwd=ROOT, text=True).splitlines()
    parts = []
    for path in ["/"] + [f"/{f}" for f in files if not f.endswith((".woff2", ".png"))]:
        r = httpx.get(url + path, headers=h, timeout=30, follow_redirects=False)
        assert r.status_code == 200, f"{path} {r.status_code}"
        parts.append(r.text + json.dumps(dict(r.headers)))
    rows = 0
    for q in ("bcbs georgia", "united helth", "zzzz no such payer"):
        r = httpx.post(url + "/api/search", headers={**h, "Content-Type": "application/json"},
                       json={"q": q, "page": 1}, timeout=30, follow_redirects=False)
        assert r.status_code == 200, f"{q} {r.status_code}"
        rows += len(r.json()["rows"])
        parts.append(r.text + json.dumps(dict(r.headers)))
    assert rows >= 10, "no search answered with rows"
    hits = leak_hits("\n".join(parts), live_env)
    assert hits == [], f"deployed responses hold: {hits}"
