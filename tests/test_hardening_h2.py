"""H2: the wrapper trusts only pg_catalog and pg_temp (live tests, read the catalog, not the SQL file).

Protects the Caller's rights: api.search_insurers is the one object the Data API exposes.
Run on a throwaway branch: BRANCH=t-... uv run pytest tests/test_hardening_h2.py
Tests that plant objects or re-create functions refuse to run unless BRANCH starts with "t-".
"""
import os

import pytest

pytestmark = pytest.mark.live

PIN = ["search_path=pg_catalog, pg_temp"]
SIG = "text, text, int, int, text"   # search_core
QUERIES = ["uhc", "united helth", "aetna", "blue cross texas", "kaiser"]
TENANT = "tenant-test"

API_FUNCTIONS = ("SELECT p.oid::regprocedure::text, coalesce(p.proconfig::text, 'NULL') "
                 "FROM pg_proc p WHERE p.pronamespace = 'api'::regnamespace ORDER BY 1")


def _live_pins(admin_sql):
    """{regprocedure: proconfig as the live catalog holds it}, every object in api (any kind)."""
    return {sig: cfg for sig, cfg in admin_sql(API_FUNCTIONS)}


def _bad(pins):
    return {s: c for s, c in pins.items() if c != '{"search_path=pg_catalog, pg_temp"}'}


def test_every_api_function_has_exactly_the_pin(admin_sql):
    pins = _live_pins(admin_sql)
    assert "api.search_insurers(text,text,integer,integer)" in pins   # a floor: an empty catalog read cannot pass
    assert _bad(pins) == {}


def test_catalog_check_sees_every_overload(admin_sql, throwaway_branch):
    # HOSTILE: an unpinned overload, one pinned with public, and one pinned with nothing else but a second SET.
    admin_sql("CREATE FUNCTION api.search_insurers(q text) RETURNS int LANGUAGE sql AS 'SELECT 1'")
    admin_sql("CREATE FUNCTION api.search_insurers(q text, k int) RETURNS int LANGUAGE sql "
              "SET search_path = pg_catalog, public, pg_temp AS 'SELECT 1'")
    admin_sql("CREATE FUNCTION api.search_insurers(q text, k int, j int) RETURNS int LANGUAGE sql "
              "SET search_path = pg_catalog, pg_temp SET work_mem = '1MB' AS 'SELECT 1'")
    try:
        bad = _bad(_live_pins(admin_sql))
        assert set(bad) == {"api.search_insurers(text)", "api.search_insurers(text,integer)",
                            "api.search_insurers(text,integer,integer)"}
    finally:
        for a in ("text", "text,int", "text,int,int"):
            admin_sql(f"DROP FUNCTION IF EXISTS api.search_insurers({a})")
    assert _bad(_live_pins(admin_sql)) == {}


def test_lint_0011_function_search_path_mutable_reports_nothing_for_api(admin_sql):
    # Query shape from supabase/splinter lint 0011 (function_search_path_mutable), limited to schema api.
    rows = admin_sql("""
        SELECT p.proname FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        LEFT JOIN pg_depend dep ON p.oid = dep.objid AND dep.deptype = 'e'
        WHERE n.nspname = 'api' AND dep.objid IS NULL
          AND NOT EXISTS (SELECT 1 FROM unnest(coalesce(p.proconfig, '{}')) cfg WHERE cfg LIKE 'search_path=%')""")
    assert rows == []


def test_no_non_superuser_can_create_in_the_pinned_catalog_schema(admin_sql):
    # pg_catalog is searched first: if any non-superuser could write there, the pin would trust them.
    rows = admin_sql("SELECT rolname FROM pg_roles WHERE NOT rolsuper "
                     "AND has_schema_privilege(oid, 'pg_catalog', 'CREATE')")
    assert rows == []


def _plant(admin_sql, schema):
    admin_sql(f"CREATE FUNCTION {schema}.search_core(q text, customer_id text, lim int, off int, caller text) "
              "RETURNS TABLE (rank int, payer_id text, payer_name text, matched_alias text, state text, "
              "plan_type text, score real) LANGUAGE plpgsql AS $f$ BEGIN "
              "RAISE EXCEPTION 'PLANTED look-alike was called'; END $f$")
    admin_sql(f"GRANT EXECUTE ON FUNCTION {schema}.search_core({SIG}) TO PUBLIC")


def test_planted_look_alike_in_public_is_never_called(admin_sql, caller_sql, throwaway_branch):
    # HOSTILE: same name, same signature, raises, executable by everyone, in the schema the old pin trusted.
    _plant(admin_sql, "public")
    try:
        rows = caller_sql(f"SELECT payer_name FROM api.search_insurers('uhc', '{TENANT}', 5, 0)")
    finally:
        admin_sql(f"DROP FUNCTION IF EXISTS public.search_core({SIG})")
    assert rows and any("United" in r[0] for r in rows)   # the real function ran, no PLANTED error


def test_temp_schema_look_alike_is_never_called(caller_sql, throwaway_branch):
    # HOSTILE: the Caller plants pg_temp.search_core in its own session; pg_temp is last in the pin.
    rows = caller_sql(
        "CREATE FUNCTION pg_temp.search_core(q text, customer_id text, lim int, off int, caller text) "
        "RETURNS TABLE (rank int, payer_id text, payer_name text, matched_alias text, state text, "
        "plan_type text, score real) LANGUAGE plpgsql AS $f$ BEGIN RAISE EXCEPTION 'PLANTED temp'; END $f$; "
        f"SELECT payer_name FROM api.search_insurers('uhc', '{TENANT}', 5, 0)")
    assert rows and any("United" in r[0] for r in rows)


def test_wrapper_results_equal_the_core_results(caller_sql):
    # No search result changes because of the pin: the wrapper returns what the core returns, row for row.
    for q in QUERIES:
        via_wrapper = caller_sql(f"SELECT * FROM api.search_insurers('{q}', '{TENANT}', 10, 0)")
        direct = caller_sql(f"SELECT * FROM search_idx.search_core('{q}', '{TENANT}', 10, 0, 'x')")
        assert via_wrapper and via_wrapper == direct, q


def test_reapplying_the_sql_file_keeps_the_pin(admin_sql, throwaway_branch):
    # A later deploy re-creates the wrapper from the file: the live function must still carry the pin.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import subprocess
    subprocess.run([os.path.join(root, "scripts/psql.sh"), "-q", "-f", os.path.join(root, "sql/20_search_function.sql")],
                   check=True, capture_output=True)
    assert _bad(_live_pins(admin_sql)) == {}
