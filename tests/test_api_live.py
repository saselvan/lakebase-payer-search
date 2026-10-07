"""Live tests: the real Data API, the real service principal, the real 3 M-row corpus.

Each test names the behaviour it proves. Run: uv run pytest -m live
"""
import re
import uuid

import pytest
import requests

from insurer_search.client import SearchError

pytestmark = pytest.mark.live

T = "tenant-test"


def names(rows):
    return [r["payer_name"] for r in rows]


# ---- Search quality: typos, abbreviations, synonyms, word order --------------------------------

@pytest.mark.parametrize("q,must_contain", [
    ("bcbs georgia", "Blue Cross and Blue Shield of Georgia"),      # abbreviation
    ("ga bcbs", "Blue Cross and Blue Shield of Georgia"),           # state first, both short
    ("blue cross blue shield of georgia", "Blue Cross and Blue Shield of Georgia"),  # missing "and"
    ("united helth", "UnitedHealthcare"),                           # typo
    ("unitedhealth care", "UnitedHealthcare"),                      # word split
    ("uhc", "UnitedHealthcare"),                                    # abbreviation, no shared letters
    ("medicad", "Medicaid"),                                        # typo
    ("masshealth", "MassHealth"),                                   # state program brand
    ("ma blue cross", "Blue Cross Blue Shield of Massachusetts"),   # MA = state, not plan
    ("aetna", "Aetna"),
    ("tricare prime", "TRICARE Prime"),
])
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
