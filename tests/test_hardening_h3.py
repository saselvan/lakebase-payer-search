"""H3: search_core trusts only pg_catalog and pg_temp, and returns the same results.

Live, on a THROWAWAY branch only (BRANCH=t-...). The fixture installs the real OLD search_core (the text from
git commit BASE, only its name changed) as search_idx.search_core_old, then applies the new
sql/20_search_function.sql. Both are called the same way: as the Caller over a direct login.
Run: BRANCH=t-h3-x uv run pytest tests/test_hardening_h3.py
Evidence is written to $H3_EVIDENCE (a directory), when set.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

from insurer_search.top_hits import load_top_hits

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parents[1]
BASE = "62d7439"                      # the integration commit before H3: its search_core is the baseline
TENANT = "tenant-h3"
SP = os.environ.get("SP_APPLICATION_ID", "")
PIN = "search_path=pg_catalog, pg_temp"

# Every qualified call in the new body, with the exact signature it must match (regprocedure / regoperator).
FUNCTIONS = ["public.similarity(text,text)", "public.word_similarity(text,text)", "public.levenshtein(text,text)"]
# to_bm25query(tsvector,regclass) is NOT called: its SQL body names bm25query_tsvector unqualified, so it
# resolves the type by the caller's search_path and fails without public. The body is ROW($1,$2)::bm25query_tsvector.
OPERATORS = ["public.<->(text,text)", "public.<<->(text,text)", "public.<@>(tsvector,bm25query_tsvector)"]


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _extract_core(sql_text: str) -> str:
    m = re.search(r"CREATE OR REPLACE FUNCTION search_idx\.search_core\(.*?\n\$fn\$;", sql_text, re.S)
    assert m, "search_core block not found"
    return m.group(0)


def _old_core_block() -> str:
    old = subprocess.run(["git", "show", f"{BASE}:sql/20_search_function.sql"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return _extract_core(old)


def _baseline_install_sql() -> str:
    # The ONLY change to the old text is the function name. No filter, no edit.
    block = _old_core_block().replace("search_idx.search_core(", "search_idx.search_core_old(", 1)
    return (block + "\nALTER FUNCTION search_idx.search_core_old(text, text, int, int, text) OWNER TO search_owner;\n"
            "REVOKE ALL ON FUNCTION search_idx.search_core_old(text, text, int, int, text) FROM PUBLIC;\n"
            f'GRANT EXECUTE ON FUNCTION search_idx.search_core_old(text, text, int, int, text) TO "{SP}";\n')


def _psql_file(path: Path):
    assert os.environ.get("BRANCH", "").startswith("t-"), "live H3 tests run only on a t- branch"
    subprocess.run([str(ROOT / "scripts/psql.sh"), "-q", "-f", str(path)], check=True,
                   capture_output=True, text=True)


@pytest.fixture(scope="session", autouse=True)
def both_functions(tmp_path_factory):
    assert os.environ.get("BRANCH", "").startswith("t-"), "live H3 tests run only on a t- branch"
    d = tmp_path_factory.mktemp("h3")
    # ownership moves need search_owner to own the old copy; GRANT CREATE around it as sql/20 does
    f = d / "baseline.sql"
    f.write_text("GRANT CREATE ON SCHEMA search_idx TO search_owner;\n" + _baseline_install_sql()
                 + "REVOKE CREATE ON SCHEMA search_idx FROM search_owner;\n")
    _psql_file(f)
    _psql_file(ROOT / "sql/20_search_function.sql")


def _cases():
    """Every top-hit query, plus the paging, typo, join, one-word and no-match cases of the live tests."""
    cases = [(q, 10, 0) for q, _ in load_top_hits()]
    cases += [("blu cros georgai", 10, 0), ("blu cros georgai", 10, 10), ("aetna", 10, 0), ("kaiser", 10, 10),
              ("zzqx vvkj", 10, 0), ("united health", 10, 0), ("aetna", 10, 60), ("cigna ppo", 5, 0)]
    cases += [("blue cross", 10, 10 * p) for p in range(0, 6)]
    seen, out = set(), []
    for c in cases:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _values(cases):
    return ",\n".join(f"({_sql_str(q)}, {lim}, {off})" for q, lim, off in cases)


def _evidence(name: str, text: str):
    d = os.environ.get("H3_EVIDENCE")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        (Path(d) / name).write_text(text)


def _compare_sql(cases, new_fn="search_idx.search_core", old_fn="search_idx.search_core_old"):
    """One row per case: q, lim, off, rows_old, rows_new, same. A row is the whole result row, in rank order."""
    row = ("format('%s|%s|%s|%s|%s|%s|%s', r.rank, r.payer_id, r.payer_name, r.matched_alias, "
           "r.state, r.plan_type, r.score)")
    return f"""
SELECT c.q, c.lim, c.off, o.n, w.n, (o.s IS NOT DISTINCT FROM w.s)
FROM (VALUES {_values(cases)}) AS c(q, lim, off)
CROSS JOIN LATERAL (SELECT count(*) n, string_agg({row}, ' ; ' ORDER BY r.rank) s
                    FROM {old_fn}(c.q, {_sql_str(TENANT)}, c.lim, c.off, 'x') r) o
CROSS JOIN LATERAL (SELECT count(*) n, string_agg({row}, ' ; ' ORDER BY r.rank) s
                    FROM {new_fn}(c.q, {_sql_str(TENANT)}, c.lim, c.off, 'x') r) w
ORDER BY c.q, c.off"""


# ---------- the pin and the body --------------------------------------------------------------------------

def test_search_core_pin_is_exactly_pg_catalog_and_pg_temp(admin_sql):
    cfg = admin_sql("SELECT proconfig::text FROM pg_proc WHERE oid = 'search_idx.search_core(text,text,int,int,text)'::regprocedure")
    assert cfg == [['{"' + PIN + '"}']]


def _prosrc(fn: str) -> str:
    return subprocess.run([str(ROOT / "scripts/psql.sh"), "-At", "-c",
                           f"SELECT prosrc FROM pg_proc WHERE oid = '{fn}(text,text,int,int,text)'::regprocedure"],
                          capture_output=True, text=True, check=True).stdout


def _code_only(body: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in body.splitlines())


def test_no_unqualified_extension_call_is_left_in_the_body(admin_sql):
    code = _code_only(_prosrc("search_idx.search_core"))
    # a function call is "name(" not preceded by "public."
    for fn in ("similarity", "word_similarity", "levenshtein", "to_bm25query"):   # to_bm25query: no call at all
        assert not re.findall(rf"(?<![\w.]){fn}\s*\(", code), f"unqualified call to {fn}"
    assert "to_bm25query" not in code, "to_bm25query resolves its type by the caller's search_path"
    assert not re.findall(r"(?<![\w.])bm25query_tsvector", code), "unqualified type bm25query_tsvector"
    # an operator is "<->", "<<->" or "<@>" that is not written OPERATOR(public.<op>)
    stripped = re.sub(r"OPERATOR\(public\.(<->|<<->|<@>)\)", "", code)
    for op in ("<->", "<<->", "<@>"):
        assert op not in stripped, f"unqualified operator {op}"
    assert "OPERATOR(public." in code and "public.similarity(" in code   # the qualified forms are really there


def test_every_qualified_call_has_an_exact_type_match(admin_sql):
    """A cast that fails means there is no exact match. The OIDs are the record."""
    out = []
    for sig in FUNCTIONS:
        out.append((sig, admin_sql(f"SELECT {_sql_str(sig)}::regprocedure::oid")[0][0]))
    for sig in OPERATORS:
        out.append((sig, admin_sql(f"SELECT {_sql_str(sig)}::regoperator::oid")[0][0]))
    out.append(("public.bm25query_tsvector (type)", admin_sql("SELECT 'public.bm25query_tsvector'::regtype::oid")[0][0]))
    # the ROW(tsvector, regclass) cast needs the composite's two columns to be exactly these types
    cols = admin_sql("SELECT string_agg(format_type(atttypid, null), ',' ORDER BY attnum) FROM pg_attribute "
                     "WHERE attrelid = 'public.bm25query_tsvector'::regclass AND attnum > 0")
    assert cols == [["tsvector,regclass"]], cols
    _evidence("exact_match_casts.txt", "\n".join(f"{s}\t{o}" for s, o in out) + "\n")
    # and the body uses only these signatures' names: every qualified name it writes is on the list
    code = _code_only(_prosrc("search_idx.search_core"))
    used = set(re.findall(r"public\.(\w+)\(", code)) | set(re.findall(r"OPERATOR\(public\.([^)]+)\)", code))
    assert set(re.findall(r"::public\.(\w+)", code)) == {"bm25query_tsvector"}
    allowed = {re.match(r"public\.(.+?)\(", s).group(1) for s in FUNCTIONS + OPERATORS}
    assert used == allowed, (used, allowed)


def _call_args(code: str, name: str):
    """Top-level arguments of every `public.<name>(...)` call."""
    out = []
    for m in re.finditer(rf"public\.{re.escape(name)}\(", code):
        depth, cur, args, i = 1, "", [], m.end()
        while depth:
            ch = code[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            if ch == "," and depth == 1:
                args.append(cur.strip())
                cur = ""
            else:
                cur += ch
            i += 1
        args.append(cur.strip())
        out.append(args)
    return out


def test_every_argument_of_a_qualified_function_is_cast_to_text():
    code = _code_only(_extract_core((ROOT / "sql/20_search_function.sql").read_text()))
    n = 0
    for name in ("similarity", "word_similarity", "levenshtein"):
        for args in _call_args(code, name):
            n += 1
            assert len(args) == 2 and all(a.endswith("::text") for a in args), (name, args)
    assert n == 7, n     # 2 levenshtein + 2 similarity + 3 word_similarity in the body


def test_every_operand_of_a_qualified_operator_is_cast_to_text_or_has_its_exact_type():
    sql = (ROOT / "sql/20_search_function.sql").read_text()
    code = _code_only(_extract_core(sql))
    # the BM25 query is built by ROW(tsvector, '...'::regclass)::public.bm25query_tsvector, never a bare literal
    rows = re.findall(r"ROW\(([^;]*?)\)::public\.bm25query_tsvector", code)
    assert len(rows) == 3, rows
    assert all(r.rstrip().endswith("'search_idx.alias_key_bm25'::regclass") for r in rows), rows
    # the trigram operators: both sides end in ::text
    for m in re.finditer(r"(\S+)\s+OPERATOR\(public\.(<->|<<->)\)\s+(\S+)", code):
        assert m.group(1).endswith("::text") or m.group(1).endswith("::text)"), m.group(0)
        assert m.group(3).rstrip(",").endswith("::text") or m.group(3).rstrip(",").endswith("::text)"), m.group(0)


# ---------- the comparison ----------------------------------------------------------------------------------

def test_baseline_is_the_unchanged_old_function(admin_sql):
    old_body = re.search(r"\$fn\$(.*)\$fn\$;", _old_core_block(), re.S).group(1)
    assert _prosrc("search_idx.search_core_old").strip() == old_body.strip(), "baseline body is not the old text"
    # the old pin still has public; it is the baseline's whole point
    pin = admin_sql("SELECT proconfig::text FROM pg_proc WHERE oid = 'search_idx.search_core_old(text,text,int,int,text)'::regprocedure")
    assert "pg_catalog, public, pg_temp" in pin[0][0]


def test_baseline_sees_everything_the_new_function_sees(caller_sql):
    """A filtered baseline would return fewer rows for a broad query."""
    r = caller_sql(f"""SELECT (SELECT count(*) FROM search_idx.search_core_old('blue cross', {_sql_str(TENANT)}, 10, 0, 'x')),
                              (SELECT count(*) FROM search_idx.search_core('blue cross', {_sql_str(TENANT)}, 10, 0, 'x'))""")
    assert r == [["10", "10"]]


def test_old_and_new_return_the_same_ordered_top_10(caller_sql):
    cases = _cases()
    rows = caller_sql(_compare_sql(cases))
    assert len(rows) == len(cases)
    _evidence("comparison.tsv", "q\tlim\toff\trows_old\trows_new\tsame\n"
              + "\n".join("\t".join(r) for r in rows) + "\n")
    diffs = [r for r in rows if r[5] != "t" or r[3] != r[4]]
    assert not diffs, f"{len(diffs)} of {len(rows)} differ: {diffs}"


# ---------- the attack: planted overloads --------------------------------------------------------------------

PLANT = """
CREATE FUNCTION public.similarity(varchar, varchar) RETURNS real LANGUAGE plpgsql AS $p$ BEGIN RAISE EXCEPTION 'PLANTED similarity'; END $p$;
CREATE FUNCTION public.word_similarity(varchar, varchar) RETURNS real LANGUAGE plpgsql AS $p$ BEGIN RAISE EXCEPTION 'PLANTED word_similarity'; END $p$;
CREATE FUNCTION public.levenshtein(varchar, varchar) RETURNS int LANGUAGE plpgsql AS $p$ BEGIN RAISE EXCEPTION 'PLANTED levenshtein'; END $p$;
CREATE FUNCTION public.to_bm25query(tsvector, text) RETURNS public.bm25query_tsvector LANGUAGE plpgsql AS $p$ BEGIN RAISE EXCEPTION 'PLANTED to_bm25query text'; END $p$;
CREATE FUNCTION public.to_bm25query(tsvector, varchar) RETURNS public.bm25query_tsvector LANGUAGE plpgsql AS $p$ BEGIN RAISE EXCEPTION 'PLANTED to_bm25query varchar'; END $p$;
CREATE FUNCTION public.similarity_dist(varchar, varchar) RETURNS real LANGUAGE plpgsql AS $p$ BEGIN RAISE EXCEPTION 'PLANTED dist'; END $p$;
CREATE OPERATOR public.<-> (LEFTARG = varchar, RIGHTARG = varchar, FUNCTION = public.similarity_dist);
"""
UNPLANT = """
DROP OPERATOR IF EXISTS public.<-> (varchar, varchar);
DROP FUNCTION IF EXISTS public.similarity_dist(varchar, varchar);
DROP FUNCTION IF EXISTS public.to_bm25query(tsvector, varchar);
DROP FUNCTION IF EXISTS public.to_bm25query(tsvector, text);
DROP FUNCTION IF EXISTS public.levenshtein(varchar, varchar);
DROP FUNCTION IF EXISTS public.word_similarity(varchar, varchar);
DROP FUNCTION IF EXISTS public.similarity(varchar, varchar);
"""


def _run_admin_script(sql: str):
    return subprocess.run([str(ROOT / "scripts/psql.sh"), "-q", "-c", sql], capture_output=True, text=True)


def test_planted_overloads_change_nothing_and_never_run(caller_sql):
    cases = _cases()
    clean = caller_sql(f"""SELECT c.q, c.off, string_agg(format('%s|%s|%s', r.rank, r.payer_id, r.score), ';' ORDER BY r.rank)
        FROM (VALUES {_values(cases)}) AS c(q, lim, off)
        CROSS JOIN LATERAL search_idx.search_core(c.q, {_sql_str(TENANT)}, c.lim, c.off, 'x') r GROUP BY c.q, c.off ORDER BY 1, 2""")
    assert _run_admin_script(UNPLANT).returncode == 0
    planted = _run_admin_script(PLANT)
    assert planted.returncode == 0, planted.stderr
    try:
        q = f"""SELECT c.q, c.off, string_agg(format('%s|%s|%s', r.rank, r.payer_id, r.score), ';' ORDER BY r.rank)
            FROM (VALUES {_values(cases)}) AS c(q, lim, off)
            CROSS JOIN LATERAL search_idx.search_core(c.q, {_sql_str(TENANT)}, c.lim, c.off, 'x') r GROUP BY c.q, c.off ORDER BY 1, 2"""
        run = subprocess.run([str(ROOT / "scripts/psql.sh"), "-At", "-F", "\t", "-c", q], capture_output=True,
                             text=True, env={**os.environ, "AS_CALLER": "1"})
        assert run.returncode == 0, f"search_core failed under the planted overloads: {run.stderr.strip()[-300:]}"
        after = [ln.split("\t") for ln in run.stdout.splitlines() if ln]
        # the baseline under the same attack: recorded, not asserted (it is the thing being fixed)
        old = subprocess.run([str(ROOT / "scripts/psql.sh"), "-At", "-c",
                              f"SELECT count(*) FROM search_idx.search_core_old('aetna', {_sql_str(TENANT)}, 10, 0, 'x')"],
                             capture_output=True, text=True, env={**os.environ, "AS_CALLER": "1"})
        _evidence("planted_attack.txt", f"cases={len(cases)} identical_after_plant={clean == after}\n"
                  f"old_function_under_attack: rc={old.returncode} stdout={old.stdout.strip()!r} "
                  f"first_error={(old.stderr.strip().splitlines() or [''])[0][:160]!r}\n")
    finally:
        assert _run_admin_script(UNPLANT).returncode == 0
    assert clean == after


def test_a_caller_planted_temp_function_and_table_change_nothing(caller_sql):
    cases = _cases()[:12]
    base = f"""SELECT string_agg(format('%s|%s', r.q, r.n), ';') FROM (SELECT c.q, count(*) n
        FROM (VALUES {_values(cases)}) AS c(q, lim, off)
        CROSS JOIN LATERAL search_idx.search_core(c.q, {_sql_str(TENANT)}, c.lim, c.off, 'x') r GROUP BY c.q ORDER BY 1) r"""
    plant = ("CREATE FUNCTION pg_temp.levenshtein(text, text) RETURNS int LANGUAGE sql AS 'SELECT 0'; "
             "CREATE FUNCTION pg_temp.similarity(text, text) RETURNS real LANGUAGE sql AS 'SELECT 1.0::real'; "
             "CREATE TEMP TABLE alias_key (key_norm text); CREATE TEMP TABLE vocab (word text); ")
    # the caller's own session, CREATE FUNCTION in pg_temp is allowed to any role
    # -c prints one command tag per statement; the last row is the SELECT result
    assert caller_sql(plant + base)[-1:] == caller_sql(base)[-1:]


# ---------- plans and latency --------------------------------------------------------------------------------

def test_the_indexes_are_still_used(admin_sql):
    body = _prosrc("search_idx.search_core")
    # the statements below are the function's own ORDER BY clauses, with the same operators
    assert "ORDER BY qn::text OPERATOR(public.<<->) t.key_norm::text, t.text_id" in body
    assert "ORDER BY k.tsv OPERATOR(public.<@>) ROW(to_tsvector('search_idx.payer_cfg', qc), " \
           "'search_idx.alias_key_bm25'::regclass)::public.bm25query_tsvector, k.key_id" in body
    bm = admin_sql("EXPLAIN (COSTS OFF) SELECT k.key_norm FROM search_idx.alias_key k "
                   "ORDER BY k.tsv OPERATOR(public.<@>) ROW(to_tsvector('search_idx.payer_cfg', 'blue cross'), "
                   "'search_idx.alias_key_bm25'::regclass)::public.bm25query_tsvector, k.key_id LIMIT 1000")
    tr = admin_sql("EXPLAIN (COSTS OFF) SELECT t.text_id FROM search_idx.search_text t "
                   "ORDER BY 'blue cross'::text OPERATOR(public.<<->) t.key_norm::text, t.text_id LIMIT 100")
    text = "\n".join(r[0] for r in bm + tr)
    _evidence("plans.txt", text + "\n")
    assert "alias_key_bm25" in "\n".join(r[0] for r in bm)
    assert "search_text_trgm" in "\n".join(r[0] for r in tr)


def test_latency_p50_p95_old_vs_new(caller_sql):
    cases = [c for c in _cases() if c[2] == 0][:30]
    runs = 30
    sql = f"""
CREATE TEMP TABLE lat(fn text, q text, ms double precision);
DO $d$ DECLARE c record; i int; t0 timestamptz; BEGIN
  FOR c IN SELECT * FROM (VALUES {_values(cases)}) AS v(q, lim, off) LOOP
    FOR i IN 0..{runs} LOOP     -- run 0 is the warm-up, not recorded
      IF i % 2 = 0 THEN
        t0 := clock_timestamp(); PERFORM * FROM search_idx.search_core_old(c.q, {_sql_str(TENANT)}, c.lim, c.off, 'x');
        IF i > 0 THEN INSERT INTO lat VALUES ('old', c.q, extract(epoch FROM clock_timestamp() - t0) * 1000); END IF;
        t0 := clock_timestamp(); PERFORM * FROM search_idx.search_core(c.q, {_sql_str(TENANT)}, c.lim, c.off, 'x');
        IF i > 0 THEN INSERT INTO lat VALUES ('new', c.q, extract(epoch FROM clock_timestamp() - t0) * 1000); END IF;
      ELSE
        t0 := clock_timestamp(); PERFORM * FROM search_idx.search_core(c.q, {_sql_str(TENANT)}, c.lim, c.off, 'x');
        INSERT INTO lat VALUES ('new', c.q, extract(epoch FROM clock_timestamp() - t0) * 1000);
        t0 := clock_timestamp(); PERFORM * FROM search_idx.search_core_old(c.q, {_sql_str(TENANT)}, c.lim, c.off, 'x');
        INSERT INTO lat VALUES ('old', c.q, extract(epoch FROM clock_timestamp() - t0) * 1000);
      END IF;
    END LOOP;
  END LOOP;
END $d$;
SELECT fn, count(*), round(percentile_cont(0.5) WITHIN GROUP (ORDER BY ms)::numeric, 2),
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY ms)::numeric, 2) FROM lat GROUP BY fn ORDER BY fn"""
    rows = caller_sql(sql)
    _evidence("latency.tsv", "fn\tn\tp50_ms\tp95_ms\n" + "\n".join("\t".join(r) for r in rows) + "\n")
    stat = {r[0]: (float(r[2]), float(r[3])) for r in rows if len(r) == 4}
    assert set(stat) == {"old", "new"}
    assert stat["new"][0] <= stat["old"][0] * 1.3, stat      # p50 within 30 percent
    assert stat["new"][1] <= stat["old"][1] * 1.5, stat      # p95 within 50 percent


# ---------- guard: the inlined to_bm25query ------------------------------------------------------------------

# What search_core inlines: ROW(tsvector, regclass)::public.bm25query_tsvector, which is the extension's own body.
TO_BM25QUERY_BODY = "SELECT ROW($1, $2)::bm25query_tsvector"
TO_BM25QUERY_ARGS = "tsvector, regclass"
TO_BM25QUERY_RESULT = "bm25query_tsvector"
BM25_TYPE_SCHEMA = "public"
BM25_TYPE_ATTRS = "tsvector,regclass"


def test_the_inlined_to_bm25query_still_matches_the_extension(admin_sql):
    msg = "lakebase_text changed: re-check the inlined to_bm25query in search_core"
    fn = "public.to_bm25query(tsvector,regclass)"
    got = admin_sql(f"SELECT pg_get_function_arguments(oid), pg_get_function_result(oid) "
                    f"FROM pg_proc WHERE oid = '{fn}'::regprocedure")
    assert got == [[TO_BM25QUERY_ARGS, TO_BM25QUERY_RESULT]], msg
    body = subprocess.run([str(ROOT / "scripts/psql.sh"), "-At", "-c",
                           f"SELECT prosrc FROM pg_proc WHERE oid = '{fn}'::regprocedure"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert body == TO_BM25QUERY_BODY, msg
    assert admin_sql("SELECT typnamespace::regnamespace::text FROM pg_type WHERE oid = 'public.bm25query_tsvector'::regtype") \
        == [[BM25_TYPE_SCHEMA]], msg
    assert admin_sql("SELECT string_agg(format_type(atttypid, null), ',' ORDER BY attnum) FROM pg_attribute "
                     "WHERE attrelid = 'public.bm25query_tsvector'::regclass AND attnum > 0") == [[BM25_TYPE_ATTRS]], msg
