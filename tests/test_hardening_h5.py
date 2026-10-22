"""H5: PUBLIC never holds EXECUTE on a search function, not even for an instant during install.

Live, and run ONLY on a throwaway branch (BRANCH=t-...). Two tests drop and re-create the two functions,
so they refuse to run on anything else. Run: BRANCH=t-h5-xxx uv run pytest tests/test_hardening_h5.py
"""
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FUNCTION_SQL = ROOT / "sql" / "20_search_function.sql"
GRANTS_SQL = ROOT / "sql" / "40_caller_grants.sql"
PSQL = str(ROOT / "scripts" / "psql.sh")

pytestmark = pytest.mark.live

DROP_BOTH = """DROP FUNCTION IF EXISTS api.search_insurers(text, text, int, int);
DROP FUNCTION IF EXISTS search_idx.search_core(text, text, int, int, text);"""

# every function in the two schemas that PUBLIC could execute, by the raw ACL (an empty ACL = the default = PUBLIC)
PUBLIC_EXEC = """
  SELECT n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')'
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname IN ('api', 'search_idx')
    AND (has_function_privilege('public', p.oid, 'EXECUTE')
         OR p.proacl IS NULL
         OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'))
  ORDER BY 1"""


def _psql(*args, check=True, extra_env=None):
    return subprocess.run([PSQL, *args], capture_output=True, text=True, check=check,
                          env={**os.environ, **(extra_env or {})})


def _functions_present(admin_sql):
    return admin_sql("""SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                        WHERE (n.nspname, p.proname) IN (('api','search_insurers'), ('search_idx','search_core'))
                        ORDER BY 1""")


def _install(sql_text: str, check=True):
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
        f.write(sql_text)
    try:
        return _psql("-q", "-f", f.name, check=check)
    finally:
        os.unlink(f.name)


def _restore():
    """Put the branch back: functions from the real file, then the Caller's grants."""
    _install(FUNCTION_SQL.read_text())
    _psql("-q", "-v", f"sp={os.environ['SP_APPLICATION_ID']}", "-f", str(GRANTS_SQL))


def _statements_outside_comments(text: str) -> list[str]:
    no_comments = re.sub(r"--[^\n]*", "", text)
    no_comments = re.sub(r"\$(\w*)\$.*?\$\1\$", "$$BODY$$", no_comments, flags=re.S)  # collapse dollar-quoted bodies
    return [s.strip() for s in no_comments.split(";") if s.strip()]


# ---- the state: nobody but the Caller holds EXECUTE -------------------------------------------

def test_public_holds_no_execute_on_any_function_in_api_or_search_idx(admin_sql):
    # Every function, not two names: a later overload that skips the revoke must fail this.
    assert admin_sql(PUBLIC_EXEC) == []


def test_both_search_functions_exist_and_the_check_sees_them(admin_sql):
    # Guards the test above against passing on an empty schema.
    assert _functions_present(admin_sql) == [["search_core"], ["search_insurers"]]


# ---- the file: one transaction ---------------------------------------------------------------

def test_the_function_file_is_one_transaction_with_stop_on_error_first():
    text = FUNCTION_SQL.read_text()
    code = re.sub(r"--[^\n]*", "", text)
    assert code.index(r"\set ON_ERROR_STOP on") < code.index("BEGIN;")
    stmts = _statements_outside_comments(text)
    assert stmts[0].replace(r"\set ON_ERROR_STOP on", "").strip() == "BEGIN"
    assert stmts[-1] == "COMMIT"
    inner = stmts[1:-1]
    assert not [s for s in inner if re.match(r"(?i)(begin|commit|end|rollback)$", s)], "a nested transaction command ends the transaction early"


def test_a_forced_error_after_create_leaves_no_function_behind(admin_sql, throwaway_branch):
    text = FUNCTION_SQL.read_text()
    assert text.rstrip().endswith("COMMIT;")
    broken = text.rstrip()[: -len("COMMIT;")] + "SELECT 1/0;   -- forced error after both CREATEs\nCOMMIT;\n"
    try:
        _psql("-q", "-c", DROP_BOTH)
        r = _install(broken, check=False)
        assert r.returncode != 0 and "division by zero" in r.stderr
        assert _functions_present(admin_sql) == []          # fresh install that failed: nothing exists
    finally:
        _restore()
    assert admin_sql(PUBLIC_EXEC) == []


# ---- the outside check: a second session polls during a fresh install -------------------------

POLLER = """
DO $do$
DECLARE
  hits int := 0; polls int := 0; absent boolean := false; first_hit text := '';
  n int; hit text; done boolean := false; t_end timestamptz := clock_timestamp() + interval '150 seconds';
BEGIN
  RAISE NOTICE 'POLLER_READY';
  WHILE NOT done AND clock_timestamp() < t_end LOOP
    polls := polls + 1;
    SELECT count(*) INTO n FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
      WHERE s.nspname IN ('api','search_idx') AND p.proname IN ('search_insurers','search_core');
    IF n = 0 THEN absent := true; END IF;
    SELECT string_agg(s.nspname || '.' || p.proname, ',') INTO hit FROM pg_proc p JOIN pg_namespace s ON s.oid = p.pronamespace
      WHERE s.nspname IN ('api','search_idx')
        AND (has_function_privilege('public', p.oid, 'EXECUTE')
             OR EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                        WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'));
    IF hit IS NOT NULL THEN hits := hits + 1; IF first_hit = '' THEN first_hit := hit; END IF; END IF;
    -- finished: seen the empty start, and now the wrapper is there with its comment (the file's last statement)
    IF absent AND n = 2 AND obj_description(to_regprocedure('api.search_insurers(text,text,int,int)'), 'pg_proc') IS NOT NULL THEN
      done := true;
    END IF;
  END LOOP;
  RAISE NOTICE 'POLLER_RESULT done=% absent_seen=% polls=% hits=% first=%', done, absent, polls, hits, first_hit;
END $do$;
"""


def _fresh_install_with_poller(sql_text: str) -> dict:
    """Drop both functions, start a second session that polls, run the install, return the poller's counts."""
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
        f.write(POLLER)
    poller = subprocess.Popen([PSQL, "-q", "-f", f.name], stderr=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, env=dict(os.environ))
    try:
        _psql("-q", "-c", DROP_BOTH)       # the poller starts after the drop is not required: it waits for absent
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline and not ready:      # poller connected and looping
            line = poller.stderr.readline()
            ready = "POLLER_READY" in line
            if not line and poller.poll() is not None:
                break
        assert ready, "the polling session did not start"
        time.sleep(1)
        _install(sql_text)
        out, err = poller.communicate(timeout=180)
    finally:
        os.unlink(f.name)
        if poller.poll() is None:
            poller.kill()
    m = re.search(r"POLLER_RESULT done=(\w) absent_seen=(\w) polls=(\d+) hits=(\d+) first=(.*)", err)
    assert m, err
    return {"done": m[1] == "t", "absent_seen": m[2] == "t", "polls": int(m[3]), "hits": int(m[4]), "first": m[5].strip()}


def test_a_polling_session_never_sees_a_function_PUBLIC_can_execute_on_a_fresh_install(admin_sql, throwaway_branch):
    try:
        r = _fresh_install_with_poller(FUNCTION_SQL.read_text())
    finally:
        _restore()
    print("POLL RESULT (file as committed):", r)
    assert r["done"] and r["absent_seen"], r        # it watched the whole install, from empty to finished
    assert r["hits"] == 0, r


def test_the_poller_does_see_the_window_when_the_file_is_not_one_transaction(admin_sql, throwaway_branch):
    # Control: proves the poller can see the window at all. Strip the transaction from the real file.
    unwrapped = re.sub(r"(?m)^(BEGIN|COMMIT);\s*$", "", FUNCTION_SQL.read_text())
    assert unwrapped != FUNCTION_SQL.read_text()
    try:
        r = _fresh_install_with_poller(unwrapped)
    finally:
        _restore()
    print("POLL RESULT (no transaction):", r)
    assert r["done"] and r["hits"] > 0, r
