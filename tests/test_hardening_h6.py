"""H6 guard tests (ticket #14): the search_path pins stay meaningful only while the schemas they name stay
closed to untrusted writers, and nothing in `public` imitates a pg_catalog function or operator.

Both live tests read the pins from the LIVE proconfig of every function in `api` and `search_idx` (never from the
SQL file, never from a hardcoded schema list), so they work with today's pin and with any later pin.

Checks that "pass" over an empty list are bugs, so every list is asserted non-empty. The check functions take their
scope as arguments and the *_empty_scope tests prove they fail when the scope matches nothing.

Run: BRANCH=<t- branch> uv run pytest tests/test_hardening_h6.py   (DATA_API_BASE is not needed; direct logins only)
"""
import pytest

SCOPES = ("api", "search_idx")

# pg_trgm's operators carry the same *names* as pg_catalog operators over other types (`%`, `<->`). They are
# extension-owned and never share a signature with a pg_catalog operator. Anything else with a clashing name fails.
ALLOWED_EXTENSION_OPERATORS = {("%", "text", "text", "pg_trgm"), ("<->", "text", "text", "pg_trgm")}


def parse_search_path(entry: str) -> list[str]:
    """'search_path=pg_catalog, public, pg_temp' -> ['pg_catalog', 'public', 'pg_temp'] (quotes stripped)."""
    name, _, value = entry.partition("=")
    assert name == "search_path", entry
    return [part.strip().strip('"') for part in value.split(",") if part.strip()]


def _lit(names) -> str:
    return "array[" + ",".join("'" + n.replace("'", "''") + "'" for n in names) + "]::text[]"


def live_pins(admin_sql, scopes=SCOPES) -> dict[str, list[str]]:
    """{function signature: pinned schemas} for every function in the scope schemas, from live proconfig."""
    rows = admin_sql(
        "select p.oid::regprocedure::text, coalesce(c, '') from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        f"left join lateral unnest(p.proconfig) c on c like 'search\\_path=%' where n.nspname = any({_lit(scopes)}) "
        "order by 1")
    assert rows, f"no functions found in schemas {scopes}: nothing to guard"
    pins: dict[str, list[str]] = {}
    for sig, entry in rows:
        assert entry, f"{sig} has no search_path pin"
        assert sig not in pins, f"{sig} has more than one search_path entry"
        pins[sig] = parse_search_path(entry)
    return pins


def check_pinned_schemas_closed(admin_sql, caller_sql, scopes=SCOPES) -> list[str]:
    pins = live_pins(admin_sql, scopes)
    schemas = sorted({s for pin in pins.values() for s in pin if s != "pg_temp"})
    assert schemas, f"pins {pins} name no schema to check"
    assert not any(s.startswith("$") for s in schemas), f"a pin names a per-user schema: {schemas}"
    present = {r[0] for r in admin_sql(f"select nspname from pg_namespace where nspname = any({_lit(schemas)})")}
    assert present == set(schemas), f"pinned schemas that do not exist (anyone could create them): {set(schemas) - present}"

    caller = caller_sql("select current_user")[0][0]
    # The Caller itself, plus every role reachable through membership (recursive, inherit flag ignored: a NOINHERIT
    # member can still SET ROLE), plus PUBLIC.
    reachable = [r[0] for r in admin_sql(
        "with recursive m(oid) as (select oid from pg_roles where rolname = " f"'{caller}' "
        "union select am.roleid from pg_auth_members am join m on am.member = m.oid) "
        "select rolname from pg_roles where oid in (select oid from m)")]
    assert caller in reachable
    roles = sorted(set(reachable) | {"public"})
    bad = admin_sql(
        f"select r, s from unnest({_lit(roles)}) r, unnest({_lit(schemas)}) s where has_schema_privilege(r, s, 'CREATE')")
    assert len(roles) >= 2 and schemas   # the Caller and PUBLIC were both compared against at least one schema
    assert not bad, f"CREATE granted on a pinned schema to {bad} (Caller {caller}, roles checked {roles})"
    return schemas


def check_no_lookalikes(admin_sql, shadow_schema="public", catalog_schema="pg_catalog") -> tuple[int, int]:
    nf = int(admin_sql(f"select count(*) from pg_proc where pronamespace = '{shadow_schema}'::regnamespace")[0][0])
    no = int(admin_sql(f"select count(*) from pg_operator where oprnamespace = '{shadow_schema}'::regnamespace")[0][0])
    assert nf > 0, f"no functions in {shadow_schema}: nothing compared"
    assert no > 0, f"no operators in {shadow_schema}: nothing compared"
    assert int(admin_sql(f"select count(*) from pg_proc where pronamespace = '{catalog_schema}'::regnamespace")[0][0]) > 0
    assert int(admin_sql(f"select count(*) from pg_operator where oprnamespace = '{catalog_schema}'::regnamespace")[0][0]) > 0

    funcs = admin_sql(
        f"select p.oid::regprocedure::text from pg_proc p where p.pronamespace = '{shadow_schema}'::regnamespace "
        f"and p.proname in (select proname from pg_proc where pronamespace = '{catalog_schema}'::regnamespace) order by 1")
    ops = admin_sql(
        "select o.oprname, o.oprleft::regtype::text, o.oprright::regtype::text, coalesce(e.extname, ''), "
        f"exists (select 1 from pg_operator c where c.oprnamespace = '{catalog_schema}'::regnamespace "
        "and c.oprname = o.oprname and c.oprleft = o.oprleft and c.oprright = o.oprright) "
        f"from pg_operator o left join pg_depend d on d.objid = o.oid and d.deptype = 'e' "
        "left join pg_extension e on e.oid = d.refobjid "
        f"where o.oprnamespace = '{shadow_schema}'::regnamespace "
        f"and o.oprname in (select oprname from pg_operator where oprnamespace = '{catalog_schema}'::regnamespace) order by 1, 2, 3")
    bad_ops = [r for r in ops
               if r[4] == "t" or (r[0], r[1], r[2], r[3]) not in ALLOWED_EXTENSION_OPERATORS]
    assert not funcs, f"{shadow_schema} functions share a name with {catalog_schema}: {funcs}"
    assert not bad_ops, f"{shadow_schema} operators share a name with {catalog_schema} (name, left, right, extension, same-signature): {bad_ops}"
    return nf, no


# ---- live guard tests -------------------------------------------------------------------------------------------

def test_no_create_on_pinned_schemas(admin_sql, caller_sql):
    schemas = check_pinned_schemas_closed(admin_sql, caller_sql)
    assert "pg_catalog" in schemas or "public" in schemas   # sanity: today's and the new pin both name one of these


def test_no_pg_catalog_lookalikes_in_public(admin_sql):
    check_no_lookalikes(admin_sql)


# ---- the checks fail when their list is empty (live, scope arguments point at nothing) --------------------------

def test_pin_check_fails_on_empty_scope(admin_sql, caller_sql):
    with pytest.raises(AssertionError, match="no functions found"):
        check_pinned_schemas_closed(admin_sql, caller_sql, scopes=("h6_no_such_schema",))


def test_lookalike_check_fails_on_empty_scope(admin_sql):
    with pytest.raises(AssertionError):
        check_no_lookalikes(admin_sql, shadow_schema="information_schema")   # has functions? no operators: empty list
    with pytest.raises(Exception):
        check_no_lookalikes(admin_sql, shadow_schema="h6_no_such_schema")


# ---- the checks fail on hostile input (stubbed SQL, no live branch needed) --------------------------------------

def _stub(table):
    def run(sql):
        for needle, rows in table:
            if needle in sql:
                return rows
        raise AssertionError(f"unexpected sql: {sql[:120]}")
    return run


def test_pin_with_user_schema_or_missing_schema_or_unpinned_function_fails():
    caller = _stub([("current_user", [["c"]])])
    with pytest.raises(AssertionError, match="no search_path pin"):
        check_pinned_schemas_closed(_stub([("pg_proc", [["api.f()", ""]])]), caller)
    with pytest.raises(AssertionError, match="per-user"):
        check_pinned_schemas_closed(_stub([("pg_proc", [["api.f()", 'search_path="$user", pg_temp']])]), caller)
    with pytest.raises(AssertionError, match="do not exist"):
        check_pinned_schemas_closed(_stub([("pg_proc", [["api.f()", "search_path=ghost, pg_temp"]]),
                                           ("pg_namespace", [])]), caller)
    with pytest.raises(AssertionError, match="name no schema"):
        check_pinned_schemas_closed(_stub([("pg_proc", [["api.f()", "search_path=pg_temp"]])]), caller)


def test_parse_search_path_variants():
    assert parse_search_path("search_path=pg_catalog, public, pg_temp") == ["pg_catalog", "public", "pg_temp"]
    assert parse_search_path('search_path=pg_catalog, "x y", search_idx') == ["pg_catalog", "x y", "search_idx"]
