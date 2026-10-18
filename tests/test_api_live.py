"""Live tests: the real Data API, the real service principal, the real 3 M-row corpus.

Each test names the behaviour it proves. Run: uv run pytest -m live
"""
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest
import requests

from insurer_search.client import SearchError

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.live

T = "tenant-test"


def names(rows):
    return [r["payer_name"] for r in rows]


def load_top_hit_cases():
    """Load top-hit test cases from the shared data file.

    This is the shared list for the flywheel gate (ticket #8).
    """
    data_file = ROOT / "data" / "test_top_hit_cases.json"
    with open(data_file) as f:
        return json.load(f)


# ---- Search quality: typos, abbreviations, synonyms, word order --------------------------------

# Load test cases from the shared data file (also used by ticket #8 flywheel gate)
_TOP_HIT_CASES = [(case["query"], case["expected_brand"]) for case in load_top_hit_cases()]

@pytest.mark.parametrize("q,must_contain", _TOP_HIT_CASES)
def test_top_hit(client, q, must_contain):
    rows = client.search(q, T)
    assert rows, f"no results for {q!r}"
    assert must_contain.lower() in rows[0]["payer_name"].lower(), names(rows)[:3]


def test_typo_query_still_finds_the_blue_plan_in_georgia(client):
    rows = client.search("blu cros georgai", T)
    top10 = names(rows)
    assert all("Blue Cross" in n and n.endswith(("GA", "Georgia")) or "of Georgia" in n for n in top10[:3]), top10
    assert any("Blue Cross and Blue Shield of Georgia" in n for n in names(
        rows + client.search("blu cros georgai", T, page=2))), top10


# ---- Result shape and paging ---------------------------------------------------------------

def test_result_shape_and_ranks(client):
    rows = client.search("aetna", T)
    assert len(rows) == 10
    assert [r["rank"] for r in rows] == list(range(1, 11))
    assert set(rows[0]) == {"rank", "payer_id", "payer_name", "matched_alias", "state", "plan_type", "score"}
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_five_pages_of_ten_then_nothing(client):
    pages = [client.search("blue cross", T, page=p) for p in range(1, 7)]
    assert [len(p) for p in pages[:5]] == [10] * 5
    assert pages[5] == []                               # page 6 = results 51-60: capped
    ranks = [r["rank"] for p in pages for r in p]
    assert ranks == list(range(1, 51))


def test_one_row_per_payer_across_all_pages(client):
    # HOSTILE: each payer has ~470 near-identical alias rows; they must collapse to one result.
    rows = [r for p in range(1, 6) for r in client.search("blue cross", T, page=p)]
    ids = [r["payer_id"] for r in rows]
    assert len(ids) == len(set(ids)) == 50


def test_paging_is_stable(client):
    a = client.search("kaiser", T, page=2)
    b = client.search("kaiser", T, page=2)
    assert a == b


def test_offset_far_past_the_cap_is_empty(client):
    assert client.call("search_insurers", {"q": "aetna", "customer_id": T, "lim": 10, "off": 60}) == []


def test_no_match_is_empty_not_an_error(client):
    assert client.search("zzqx vvkj", T) == []


# ---- Input contract: bad input is HTTP 400 with a message ------------------------------------

@pytest.mark.parametrize("body,needle", [
    ({"q": "aetna", "customer_id": T, "lim": 11, "off": 0}, "lim"),
    ({"q": "aetna", "customer_id": T, "lim": 0, "off": 0}, "lim"),
    ({"q": "aetna", "customer_id": T, "lim": 10, "off": -1}, "off"),
    ({"q": "a", "customer_id": T}, "q must"),
    ({"q": "   ", "customer_id": T}, "q must"),
    ({"q": "x" * 101, "customer_id": T}, "q must"),
    ({"q": "aetna", "customer_id": ""}, "customer_id"),
    ({"q": "aetna", "customer_id": "acme corp; drop"}, "customer_id"),
])
def test_bad_input_is_400(client, body, needle):
    with pytest.raises(SearchError) as e:
        client.call("search_insurers", body)
    assert e.value.status == 400 and needle in e.value.body


def test_missing_customer_id_is_rejected(client):
    with pytest.raises(SearchError) as e:
        client.call("search_insurers", {"q": "aetna"})
    assert e.value.status in (400, 404)                 # PostgREST: no function with that signature


def test_sql_injection_text_is_just_text(client, admin_sql):
    before = admin_sql("SELECT count(*) FROM payer_serving.insurer_alias")[0][0]
    rows = client.search("aetna'); DROP TABLE payer_serving.insurer_alias; --", T)
    assert isinstance(rows, list)
    assert admin_sql("SELECT count(*) FROM payer_serving.insurer_alias")[0][0] == before


# ---- Security: who can call, and what the caller can reach -----------------------------------

def _post(client, path, token, body=None):
    return requests.post(f"{client.api_base}/{path}", json=body or {}, timeout=10,
                         headers={} if token is None else {"Authorization": f"Bearer {token}"})


# The Lakebase gateway answers 400 (not 401) when the token is missing, not a JWT, or has a bad
# signature (observed live 2026-10-06). What matters: refused, and no rows in the body.
@pytest.mark.parametrize("token,needle", [
    (None, "missing authentication credentials"),
    ("eyJhbGciOiJub25lIn0.e30.", "missing key id"),       # unsigned JWT
])
def test_missing_or_malformed_token_is_refused(client, token, needle):
    r = _post(client, "api/rpc/search_insurers", token, {"q": "aetna", "customer_id": T})
    assert r.status_code in (400, 401) and needle in r.text and "payer" not in r.text


def test_tampered_signature_is_refused(client):
    h, p, sig = client.tokens.token().split(".")
    bad = f"{h}.{p}.{'A' if sig[0] != 'A' else 'B'}{sig[1:]}"
    r = _post(client, "api/rpc/search_insurers", bad, {"q": "aetna", "customer_id": T})
    assert r.status_code in (400, 401) and "signature" in r.text and "payer" not in r.text


def test_a_valid_token_for_another_identity_is_403(client):
    """A real workspace identity that was never granted to the API (the admin user) is refused."""
    import json
    import os
    import subprocess
    out = subprocess.run(["databricks", "auth", "token", "-p", os.environ["DATABRICKS_PROFILE"], "-o", "json"],
                         capture_output=True, text=True, check=True).stdout
    r = _post(client, "api/rpc/search_insurers", json.loads(out)["access_token"], {"q": "aetna", "customer_id": T})
    assert r.status_code == 403 and "permission denied" in r.text


@pytest.mark.parametrize("path", [
    "payer_serving/insurer_alias", "search_idx/alias_key", "audit/search_log",
])
def test_caller_cannot_read_tables_over_rest(client, path):
    r = requests.get(f"{client.api_base}/{path}?limit=1", timeout=10,
                     headers={"Authorization": f"Bearer {client.tokens.token()}"})
    assert r.status_code in (400, 401, 403, 404, 406), r.status_code
    # No row data in the body. (The error text may repeat the path, e.g. "payer_serving", so do not
    # test for a word; test for rows: a JSON array, or a synthetic payer id such as SYN000123.)
    assert not r.text.lstrip().startswith("["), r.text[:200]
    assert not re.search(r"SYN\d{6}", r.text), r.text[:200]


def test_caller_cannot_call_the_private_function_over_rest(client):
    r = _post(client, "search_idx/rpc/search_core", client.tokens.token(),
              {"q": "aetna", "customer_id": T, "lim": 10, "off": 0, "caller": "forged"})
    assert r.status_code in (400, 404, 406), r.status_code


def test_caller_role_has_no_table_privileges(admin_sql):
    import os
    sp = os.environ["SP_APPLICATION_ID"]
    rows = admin_sql(f"""
      SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname IN ('payer_serving','search_idx','audit') AND c.relkind IN ('r','m','p','v')
        AND (has_table_privilege('{sp}', c.oid, 'SELECT') OR has_table_privilege('{sp}', c.oid, 'INSERT')
             OR has_table_privilege('{sp}', c.oid, 'UPDATE') OR has_table_privilege('{sp}', c.oid, 'DELETE'))""")
    assert rows == []


# ---- Traceability: every Search is logged with Tenant and Caller -----------------------------

def test_every_search_is_logged_with_tenant_and_caller(client, admin_sql):
    import os
    tenant = f"t-{uuid.uuid4().hex[:12]}"
    client.search("cigna ppo", tenant)
    client.search("cigna ppo", tenant, page=2)
    rows = admin_sql(f"SELECT caller, q, off, result_count FROM audit.search_log "
                     f"WHERE customer_id = '{tenant}' ORDER BY search_id")
    assert [r[2] for r in rows] == ["0", "10"]
    assert all(r[0] == os.environ["SP_APPLICATION_ID"] and r[1] == "cigna ppo" for r in rows)
    assert all(int(r[3]) == 10 for r in rows)


@pytest.mark.parametrize("forged", ["forged-caller", None])
def test_a_direct_login_cannot_forge_the_caller(caller_sql, admin_sql, forged):
    # The Caller holds EXECUTE on search_core, so it can skip the Data API and pass any caller text.
    import os
    tenant = f"t-{uuid.uuid4().hex[:12]}"
    arg = "NULL" if forged is None else f"'{forged}'"
    caller_sql(f"SELECT count(*) FROM search_idx.search_core('cigna ppo', '{tenant}', 10, 0, {arg})")
    rows = admin_sql(f"SELECT caller FROM audit.search_log WHERE customer_id = '{tenant}'")
    assert rows == [[os.environ["SP_APPLICATION_ID"]]]


def test_caller_cannot_set_role_authenticator_to_forge_data_api_identity(admin_sql):
    # Attacker item 3: The Caller tries `SET ROLE authenticator` to look like a Data API call.
    # The Caller SP is not a member of the `authenticator` role (40_caller_grants.sql runs
    # GRANT sp TO authenticator, not the reverse). So SET ROLE authenticator is refused.
    # This test verifies the refusal, confirming the fix's session_user check cannot be bypassed
    # by role-switching.
    tenant = f"t-{uuid.uuid4().hex[:12]}"
    # Attempt SET ROLE authenticator as the Caller (will fail with permission denied)
    result = subprocess.run([str(ROOT / "scripts/psql.sh"), "-At", "-c",
                            f"SET ROLE authenticator; SELECT 'ok'"],
                           capture_output=True, text=True,
                           env={**os.environ, "AS_CALLER": "1"})
    # Assert SET ROLE is refused
    assert result.returncode != 0, "SET ROLE authenticator should be refused"
    assert "permission denied to set role" in result.stderr, result.stderr
    # Verify no audit row was created (the SET ROLE failed before any search ran)
    rows = admin_sql(f"SELECT caller FROM audit.search_log WHERE customer_id = '{tenant}'")
    assert rows == [], f"No log row expected; got {rows}"


def test_data_api_call_records_the_token_identity_not_a_passed_caller(client, admin_sql):
    # Attacker item 4: A Data API call (session_user = authenticator) must still record the token's identity.
    # The Data API wrapper (api.search_insurers) passes current_user (the Caller SP identity) as the
    # caller parameter to search_core. The SECURITY INVOKER wrapper ensures the token identity is logged.
    import os
    tenant = f"t-{uuid.uuid4().hex[:12]}"
    client.search("cigna ppo", tenant)
    rows = admin_sql(f"SELECT caller FROM audit.search_log WHERE customer_id = '{tenant}'")
    # The Data API logs the Caller SP identity, not the authenticator role
    assert rows == [[os.environ["SP_APPLICATION_ID"]]]
