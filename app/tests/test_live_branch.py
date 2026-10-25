"""Items 3 and 4, live on a t-finder-* branch: the app's own SP searches through the branch Data API,
and holds exactly the Caller grants.

Run: uv run pytest -m live tests/test_live_branch.py
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.live

SEARCH_FNS = {"api.search_insurers(text,text,integer,integer)",
              "search_idx.search_core(text,text,integer,integer,text)"}

# What PUBLIC can reach on this project by platform default (read 2026-10-23 on t-finder-1023a).
# Pinned, so any change turns the test red and gets a human look. None is in the Data API's schema `api`.
PUBLIC_PLATFORM_VIEWS = {"databricks_synced_table_managers", "databricks_list_roles"}
PUBLIC_PLATFORM_FUNCTIONS = {"grant_usage_on_new_schema()", "grant_select_on_new_objects()",
                             "grant_all_on_new_sequences()", "pgrst.pre_config()"}
PUBLIC_DEFINER_EXTENSION_FUNCTIONS = {
    "_dbx_bind_synced_owner(text,name)", "databricks_pipeline_claim_table(text,text,uuid)",
    "databricks_pipeline_assert_table(text,text,uuid)", "databricks_init_progress(oid,text)",
    "databricks_init_sync_log(oid,text)"}
PROTECTED_SCHEMAS = {"api", "search_idx", "payer_serving", "audit", "tokenizer_ext"}


# ---- Item 3 ---------------------------------------------------------------------------------

def test_branch_data_api_search_as_the_app_sp(branch_api_base, app_sp_token, app_sp, admin_sql):
    tenant = f"finder-live-{uuid.uuid4().hex[:10]}"
    r = httpx.post(f"{branch_api_base}/api/rpc/search_insurers",
                   json={"q": "bcbs georgia", "customer_id": tenant, "lim": 10, "off": 0},
                   headers={"Authorization": f"Bearer {app_sp_token}"}, timeout=20)
    assert r.status_code == 200, r.status_code
    rows = r.json()
    assert len(rows) == 10
    assert "Blue Cross and Blue Shield of Georgia" in rows[0]["payer_name"]
    # The search ran as the app's SP: the audit row the function wrote names it.
    logged = admin_sql("select caller, result_count from audit.search_log where customer_id = :'t'", t=tenant)
    assert logged == [[app_sp, "10"]]


# ---- Item 4 ---------------------------------------------------------------------------------

GRANTS_SQL = r"""
with sp as (select oid from pg_roles where rolname = :'sp'),
ns as (select oid, nspname from pg_namespace where nspname !~ '^pg_' and nspname <> 'information_schema')
select 'role', rolname, concat_ws(',', nullif(rolsuper, false)::text, nullif(rolcreaterole, false)::text,
       nullif(rolcreatedb, false)::text, nullif(rolbypassrls, false)::text, nullif(rolreplication, false)::text)
  from pg_roles where oid = (select oid from sp)
union all select 'member_of', r.rolname, '' from pg_auth_members m join pg_roles r on r.oid = m.roleid
  where m.member = (select oid from sp)
union all select 'direct', 'function ' || p.oid::regprocedure::text, a.privilege_type
  from pg_proc p, aclexplode(p.proacl) a where a.grantee = (select oid from sp)
union all select 'direct', 'schema ' || n.nspname, a.privilege_type
  from pg_namespace n, aclexplode(n.nspacl) a where a.grantee = (select oid from sp)
union all select 'direct', 'relation ' || c.oid::regclass::text, a.privilege_type
  from pg_class c, aclexplode(c.relacl) a where a.grantee = (select oid from sp)
union all select 'direct', 'column ' || c.oid::regclass::text || '.' || att.attname, a.privilege_type
  from pg_attribute att join pg_class c on c.oid = att.attrelid, aclexplode(att.attacl) a where a.grantee = (select oid from sp)
union all select 'direct', 'database ' || d.datname, a.privilege_type
  from pg_database d, aclexplode(d.datacl) a where a.grantee = (select oid from sp)
union all select 'direct', 'type ' || t.oid::regtype::text, a.privilege_type
  from pg_type t, aclexplode(t.typacl) a where a.grantee = (select oid from sp)
union all select 'direct', 'language ' || l.lanname, a.privilege_type
  from pg_language l, aclexplode(l.lanacl) a where a.grantee = (select oid from sp)
union all select 'direct', 'default_acl', d.defaclobjtype::text
  from pg_default_acl d, aclexplode(d.defaclacl) a where a.grantee = (select oid from sp)
union all select 'table', c.oid::regclass::text, ns.nspname
  from pg_class c join ns on ns.oid = c.relnamespace where c.relkind in ('r', 'v', 'm', 'p', 'f')
  and (has_table_privilege(:'sp', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
       or has_any_column_privilege(:'sp', c.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))
union all select 'sequence', c.oid::regclass::text, ns.nspname
  from pg_class c join ns on ns.oid = c.relnamespace
  where case when c.relkind = 'S' then has_sequence_privilege(:'sp', c.oid, 'USAGE,SELECT,UPDATE') else false end
union all select 'create', ns.nspname, '' from ns where has_schema_privilege(:'sp', ns.oid, 'CREATE')
union all select 'db_create', current_database(), '' where has_database_privilege(:'sp', current_database(), 'CREATE')
union all select 'exec', p.oid::regprocedure::text,
       case when exists (select 1 from pg_depend d where d.classid = 'pg_proc'::regclass and d.objid = p.oid
                         and d.deptype = 'e') then 'ext' else 'own' end || case when p.prosecdef then ',definer' else '' end
  from pg_proc p join ns on ns.oid = p.pronamespace where has_function_privilege(:'sp', p.oid, 'EXECUTE')
union all select 'public_exec', p.oid::regprocedure::text, ''
  from pg_proc p where p.oid::regprocedure::text in ('api.search_insurers(text,text,integer,integer)',
                                                    'search_idx.search_core(text,text,integer,integer,text)')
  and has_function_privilege('public', p.oid, 'EXECUTE')
"""


def read_grants(admin_sql, role: str) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for kind, name, extra in admin_sql(GRANTS_SQL, sp=role):
        out.setdefault(kind, []).append((name, extra))
    return out


def test_app_sp_grants(ro_sql, app_sp, ws, live_env, target):
    """The app SP is granted EXECUTE on exactly the two search functions (plus USAGE on their two schemas),
    no table, column or sequence right, no CREATE anywhere, no role membership. Everything else it can
    reach comes through PUBLIC and is the pinned platform default above. Read-only; FINDER_TARGET=production
    runs it on production (I2)."""
    # B7: the role under test is the app's SP as Databricks reports it at this moment.
    assert app_sp == ws.apps.get(live_env["APP_NAME"]).service_principal_client_id
    print(f"target: {target}")
    g = read_grants(ro_sql, app_sp)
    print({k: sorted(v) for k, v in g.items()})

    assert g.get("role") == [(app_sp, "")], f"role missing or has attributes: {g.get('role')}"
    assert "member_of" not in g, f"member of roles: {g['member_of']}"
    assert sorted(g.get("direct", [])) == sorted([
        ("function api.search_insurers(text,text,integer,integer)", "EXECUTE"),
        ("function search_idx.search_core(text,text,integer,integer,text)", "EXECUTE"),
        ("schema api", "USAGE"),
        ("schema search_idx", "USAGE"),
    ]), f"direct grants: {sorted(g.get('direct', []))}"

    tables = g.get("table", [])
    assert not [t for t, schema in tables if schema in PROTECTED_SCHEMAS], f"table rights in data schemas: {tables}"
    assert {t for t, _ in tables} <= PUBLIC_PLATFORM_VIEWS, f"table rights: {tables}"
    assert "sequence" not in g, f"sequence rights: {g['sequence']}"
    assert "create" not in g, f"CREATE on schemas: {g['create']}"
    assert "db_create" not in g, "CREATE on the database"

    execs = dict(g.get("exec", []))
    own = {f for f, kind in execs.items() if kind.startswith("own")}
    assert SEARCH_FNS <= own, f"the two search functions are not executable: {SEARCH_FNS - own}"
    assert own == SEARCH_FNS | PUBLIC_PLATFORM_FUNCTIONS, f"EXECUTE beyond the two: {sorted(own - SEARCH_FNS)}"
    definer_ext = {f for f, kind in execs.items() if kind == "ext,definer"}
    assert definer_ext == PUBLIC_DEFINER_EXTENSION_FUNCTIONS, f"definer extension functions: {sorted(definer_ext)}"
    assert "public_exec" not in g, f"PUBLIC can run a search function: {g['public_exec']}"


# ---- Session 4, item 3: the role's source and the read-only path ---------------------------

def test_app_sp_role_is_a_data_api_role(ro_sql, app_sp, ws, live_env, target):
    """I1: the role came from databricks_create_role (a service principal with OAuth login in Lakebase's own
    record) and is granted to authenticator, or the Data API answers 403."""
    roles = ws.api_client.do("GET", f"/api/2.0/postgres/projects/{live_env['LAKEBASE_PROJECT']}/branches/{target}/roles")
    mine = [r["status"] for r in roles.get("roles", []) if r.get("status", {}).get("postgres_role") == app_sp]
    assert len(mine) == 1, f"the app SP has {len(mine)} role records on {target}"
    assert (mine[0].get("identity_type"), mine[0].get("auth_method")) == ("SERVICE_PRINCIPAL", "LAKEBASE_OAUTH_V1"), \
        f"the role is not a service principal with OAuth login: {mine[0].get('identity_type')}, {mine[0].get('auth_method')}"
    granted = ro_sql("select count(*) from pg_auth_members m join pg_roles r on r.oid = m.roleid "
                     "join pg_roles a on a.oid = m.member where r.rolname = :'sp' and a.rolname = 'authenticator'",
                     sp=app_sp)
    assert granted == [["1"]], "authenticator is not a member of the app SP's role"


def test_read_only_path_refuses_a_write(live_env):
    """I4: scripts/psql_ro.sh cannot write. Shown on the t-finder branch only: no write is tried on production."""
    p = subprocess.run([str(ROOT / "scripts/psql_ro.sh"), "-At", "-c", "create temp table ro_probe(x int)"],
                       capture_output=True, text=True, env={**os.environ, "TARGET": live_env["BRANCH"]})
    assert p.returncode != 0 and "read-only transaction" in p.stderr, "the read-only path accepted a CREATE"
