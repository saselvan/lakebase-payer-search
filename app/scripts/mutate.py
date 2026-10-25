"""Show each test red: apply one mutation, run its tests, require a test failure, restore, require green.

Usage: uv run python scripts/mutate.py <group|name> [...]      groups: unit, contract, look, grants, leak, definition, drawer, searching, settings, panel, all
A mutated run must exit 1 (tests ran and one failed or errored; 2-5 means pytest itself broke) and print the expected text.
Code mutations restore the file byte-for-byte; SQL mutations run their revert on the t-finder branch.
Every run appends its output to runs/<date>/mutations.log (or $MUTATE_LOG): the E lines, the short summary, the tail.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = Path(os.environ["MUTATE_LOG"]).resolve() if os.environ.get("MUTATE_LOG") else \
    ROOT / "runs" / dt.date.today().isoformat() / "mutations.log"
SP = ":\"sp\""
UNIT = ["tests/test_server_unit.py"]
STATIC = ["tests/test_static_checks.py"]
GRANTS = ["-m", "live", "tests/test_live_branch.py::test_app_sp_grants"]
LEAK = ["-m", "live", "tests/test_token_leak_live.py"]
COPY = ["tests/test_deployed_sql.py"]
DEFN_LIVE = ["-m", "live", "tests/test_definition_live.py"]
SQL_COPY = "../sql/20_search_function.sql"
D = "tests/test_drawer.py::"
S = "tests/test_searching_line.py::"
GROUPS = ("unit", "contract", "look", "grants", "leak", "definition", "drawer", "searching", "settings", "deploy", "panel")
CELL = '  li.append(labelled("span", "st", row.state, "state"), labelled("span", "id", row.payer_id, "payer_id"), copyButton(row.payer_id));\n'

M = {
    # ---- item 2: request shape and browser surface (unit) ----
    "wrong-scope": dict(group="unit", file="server/dataapi.py", tests=UNIT, expect="test_token_request_is_client_credentials",
                        old='"scope": "all-apis"', new='"scope": "sql"'),
    "off-by-one-page": dict(group="unit", file="server/dataapi.py", tests=UNIT, expect="test_rpc_request_shape",
                            old='"off": (page - 1) * PAGE_SIZE', new='"off": page * PAGE_SIZE'),
    "browser-sets-fields": dict(group="unit", file="server/main.py", tests=UNIT, expect="test_browser_cannot_set_anything",
                                old='extra="forbid"', new='extra="ignore"'),
    "no-projection": dict(group="unit", file="server/dataapi.py", tests=UNIT, expect="test_response_rows_hold_only_contract",
                          old="[project(row) for row in rows]", new="rows"),
    "echo-upstream-error": dict(group="unit", file="server/dataapi.py",
                                tests=["tests/test_server_unit.py::test_upstream_error_body_never_reaches_the_browser"],
                                expect="test_upstream_error_body_never_reaches_the_browser",
                                old="code = _pg_code(r)", new="code = r.text"),
    "serve-repo-root": dict(group="unit", file="server/main.py", tests=UNIT, expect="test_no_file_outside_static_is_served",
                            old='    return app\n',
                            new='    app.mount("/", StaticFiles(directory=STATIC_DIR.parent), name="root")\n    return app\n'),
    "enable-docs": dict(group="unit", file="server/main.py", tests=UNIT, expect="test_no_docs_or_debug_routes",
                        old="docs_url=None, redoc_url=None, openapi_url=None",
                        new='docs_url="/docs", redoc_url=None, openapi_url="/openapi.json"'),
    "csp-open": dict(group="unit", file="server/main.py", tests=UNIT, expect="test_security_headers_on_page_and_api",
                     old="\"connect-src 'self'; ", new="\"connect-src *; "),
    "retry-403": dict(group="unit", file="server/dataapi.py", tests=UNIT, expect="test_403_is_not_retried",
                      old="if (r.status_code == 429 or r.status_code >= 500) and not retried:",
                      new="if (r.status_code == 429 or r.status_code >= 403) and not retried:"),
    "token-not-cached": dict(group="unit", file="server/dataapi.py", tests=UNIT, expect="test_token_is_cached_across_searches",
                             old="if force or self._token is None or time.time() > self._expires_at - 300:",
                             new="if True:"),
    "no-401-refresh": dict(group="unit", file="server/dataapi.py", tests=UNIT, expect="test_401_gets_one_new_token",
                           old="await self.tokens.token(force=True)", new="await self.tokens.token()"),
    "no-length-check": dict(group="unit", file="server/main.py", tests=UNIT, expect="test_query_length_is_checked",
                            old="if not 2 <= len(q) <= 100:", new="if not 0 <= len(q) <= 100_000:"),
    "echo-token-failure": dict(group="unit", file="server/dataapi.py",
                               tests=["tests/test_server_unit.py::test_token_exchange_failure_body_never_reaches_the_browser"],
                               expect="test_token_exchange_failure_body_never_reaches_the_browser",
                               old='raise UpstreamError(r.status_code, "token")', new="raise UpstreamError(r.status_code, r.text)"),
    "lens-shows-header": dict(group="unit", file="server/main.py",
                              tests=["tests/test_server_unit.py::test_lens_shows_request_without_any_header_value"],
                              expect="test_lens_shows_request_without_any_header_value",
                              old='"authorization": AUTH_NOTE}',
                              new='"authorization": "Bearer " + await c.tokens.token()}'),
    # ---- item 6: contract and copy (static) ----
    "render-network-status": dict(group="contract", file="static/app.js", tests=STATIC, expect="test_contract_fields",
                                  old=CELL, new=CELL + '  li.append(labelled("span", null, row.network_status, "network_status"));\n'),
    "render-score": dict(group="contract", file="static/app.js", tests=STATIC, expect="test_contract_fields",
                         old=CELL, new=CELL + '  li.append(labelled("span", null, row.score, "score"));\n'),
    "bracket-read": dict(group="contract", file="static/app.js", tests=STATIC, expect="test_rows_are_read_only_by_name",
                         old="const suffix = ` - ${row.state}`;", new="const suffix = ` - ${row[\"state\"]}`;"),
    "drop-synthetic-label": dict(group="contract", file="static/index.html", tests=STATIC,
                                 expect="test_copy_lint_synthetic_label",
                                 old="Payer IDs and plans are synthetic.", new="Payer IDs and plans are included."),
    "overclaim-copy": dict(group="contract", file="static/index.html", tests=STATIC, expect="test_copy_lint_forbidden",
                           old="Find the insurer on the card.</h1>", new="Find the insurer on the card, instant and secure.</h1>"),
    "example-not-shared": dict(group="contract", file="static/app.js", tests=STATIC,
                               expect="test_examples_come_from_the_shared_top_hit_list",
                               old='"uhc", "medicad"', new='"cigna", "medicad"'),
    "env-in-html": dict(group="contract", file="static/index.html", tests=STATIC,
                        expect="test_static_files_hold_no_env_or_template",
                        old="<title>Payer finder</title>",
                        new='<title>Payer finder</title><meta name="cfg" content="{{ DATABRICKS_CLIENT_SECRET }}">'),
    "inner-html": dict(group="contract", file="static/app.js", tests=STATIC, expect="test_results_render_as_text_never_html",
                       old="node.textContent = String(text);", new="node.innerHTML = String(text);"),
    # ---- session 2: the approved look (static, real browser on fixture rows) ----
    "best-copy": dict(group="look", file="static/index.html", tests=STATIC, expect="test_copy_lint_forbidden",
                      old="Find the insurer on the card.</h1>", new="Find the best insurer on the card.</h1>"),
    "group-key-any-suffix": dict(group="look", file="static/app.js", tests=STATIC,
                                 expect="test_group_key_strips_only_the_rows_own_state",
                                 old="return row.payer_name.endsWith(suffix) ? row.payer_name.slice(0, -suffix.length) : row.payer_name;",
                                 new='return row.payer_name.replace(/ - [A-Z]{2}$/, "");'),
    "group-by-full-name": dict(group="look", file="static/app.js", tests=STATIC,
                               expect="test_groups_keep_rank_order_and_show_more_extends_the_open_group",
                               old="const name = groupKey(row);", new="const name = row.payer_name;"),
    "page-two-replaces": dict(group="look", file="static/app.js", tests=STATIC,
                              expect="test_groups_keep_rank_order_and_show_more_extends_the_open_group",
                              old="state.rows = state.rows.concat(data.rows);", new="state.rows = data.rows;"),
    "count-without-page": dict(group="look", file="static/app.js",
                               tests=["tests/test_static_checks.py::test_counts_say_on_this_page_and_every_id_is_marked_synthetic"],
                               expect="test_counts_say_on_this_page_and_every_id_is_marked_synthetic",
                               old="return `${group.rows.length} ${noun} on this page`;", new="return `${group.rows.length} ${noun}`;"),
    "id-unmarked": dict(group="look", file="static/app.js", tests=STATIC,
                        expect="test_counts_say_on_this_page_and_every_id_is_marked_synthetic",
                        old="section.append(band, label, list);", new="section.append(band, list);"),
    "timing-on-front": dict(group="look", file="static/app.js", tests=STATIC,
                            expect="test_no_timing_and_no_score_outside_the_drawer",
                            old="${rows} on this page`;", new="${rows} on this page · ${Math.round(data.elapsed_ms)} ms`;"),
    "no-wake-up-note": dict(group="look", file="static/index.html", tests=STATIC,
                            expect="test_no_timing_and_no_score_outside_the_drawer",
                            old=" A first search after a pause can include a database wake-up.", new=""),
    "drawer-claims-live-grants": dict(group="look", file="static/index.html", tests=STATIC,
                                      expect="test_drawer_answers_name_their_tests_and_dates",
                                      old=" Not read live.", new=" Read live."),
    "red-band": dict(group="look", file="static/app.css", tests=STATIC, expect="test_floors_and_no_status_colour_on_results",
                     old=".band { background: var(--blue);", new=".band { background: var(--red);"),
    "green-state": dict(group="look", file="static/app.css", tests=STATIC, expect="test_floors_and_no_status_colour_on_results",
                        old=".cell .st { width: 30px;", new=".cell .st { color: #1a7f37; width: 30px;"),
    "label-12px": dict(group="look", file="static/app.css", tests=STATIC, expect="text below 13px",
                       old=".label { font-family: var(--mono); font-size: 13px;", new=".label { font-family: var(--mono); font-size: 12px;"),
    "low-contrast": dict(group="look", file="static/app.css", tests=STATIC, expect="test_floors_and_no_status_colour_on_results",
                         old="--ink-3: #4f525a;", new="--ink-3: #a3a5ab;"),
    "small-copy-target": dict(group="look", file="static/app.css", tests=STATIC, expect="test_floors_and_no_status_colour_on_results",
                              old="min-width: 72px; height: 40px;", new="min-width: 72px; height: 28px;"),
    "phone-overflow": dict(group="look", file="static/app.css", tests=STATIC, expect="wider than the screen",
                           old=".line { grid-template-columns: 48px minmax(0, 1fr) 76px;", new=".line { grid-template-columns: 48px 420px 76px;"),
    "drawer-hides-function": dict(group="look", file="static/diagram.js", tests=STATIC,
                                  expect="test_no_timing_and_no_score_outside_the_drawer",
                                  old='name: ["Postgres function ", code("api.search_insurers")]',
                                  new='name: ["Postgres function"]'),
    # ---- item 4: grants (live, SQL on the t-finder branch) ----
    "grant-select-alias-key": dict(group="grants", tests=GRANTS, expect="direct grants",
                                   apply=f"GRANT SELECT ON search_idx.alias_key TO {SP};",
                                   revert=f"REVOKE SELECT ON search_idx.alias_key FROM {SP};"),
    "grant-via-membership": dict(group="grants", tests=GRANTS, expect="member of roles",
                                 apply=f"GRANT search_owner TO {SP};", revert=f"REVOKE search_owner FROM {SP};"),
    "grant-create-api": dict(group="grants", tests=GRANTS, expect="direct grants",
                             apply=f"GRANT CREATE ON SCHEMA api TO {SP};", revert=f"REVOKE CREATE ON SCHEMA api FROM {SP};"),
    "grant-column": dict(group="grants", tests=GRANTS, expect="direct grants",
                         apply=f"GRANT SELECT (payer_id) ON search_idx.alias_key TO {SP};",
                         revert=f"REVOKE SELECT (payer_id) ON search_idx.alias_key FROM {SP};"),
    "grant-third-execute": dict(group="grants", tests=GRANTS, expect="direct grants",
                                apply="CREATE FUNCTION search_idx.mut_extra() RETURNS int LANGUAGE sql AS 'select 1'; "
                                      "REVOKE ALL ON FUNCTION search_idx.mut_extra() FROM PUBLIC; "
                                      f"GRANT EXECUTE ON FUNCTION search_idx.mut_extra() TO {SP};",
                                revert="DROP FUNCTION search_idx.mut_extra();"),
    "public-execute-default": dict(group="grants", tests=GRANTS, expect="EXECUTE beyond the two",
                                   apply="CREATE FUNCTION api.mut_open() RETURNS int LANGUAGE sql AS 'select 1';",
                                   revert="DROP FUNCTION api.mut_open();"),
    "grant-public-table": dict(group="grants", tests=GRANTS, expect="table rights in data schemas",
                               apply="GRANT USAGE ON SCHEMA audit TO PUBLIC; GRANT SELECT ON audit.search_log TO PUBLIC;",
                               revert="REVOKE SELECT ON audit.search_log FROM PUBLIC; REVOKE USAGE ON SCHEMA audit FROM PUBLIC;"),
    "revoke-search-execute": dict(group="grants", tests=GRANTS, expect="direct grants",
                                  apply=f"REVOKE EXECUTE ON FUNCTION api.search_insurers(text, text, int, int) FROM {SP};",
                                  revert=f"GRANT EXECUTE ON FUNCTION api.search_insurers(text, text, int, int) TO {SP};"),
    "search-revoked": dict(group="grants", tests=["-m", "live", "tests/test_live_branch.py::test_branch_data_api_search_as_the_app_sp"],
                           expect="test_branch_data_api_search_as_the_app_sp",
                           apply=f"REVOKE EXECUTE ON FUNCTION api.search_insurers(text, text, int, int) FROM {SP};",
                           revert=f"GRANT EXECUTE ON FUNCTION api.search_insurers(text, text, int, int) TO {SP};"),
    "wrong-role": dict(group="grants", file="tests/conftest.py", tests=GRANTS, expect="assert app_sp ==",
                       old='return ws.apps.get(live_env["APP_NAME"]).service_principal_client_id',
                       new='return "9d1c91f8-a613-446c-ab0d-af0ac9eb7158"'),
    # ---- item 5: token leak (live, real browser) ----
    "echo-auth-header": dict(group="leak", file="server/dataapi.py", tests=LEAK, expect=": token",
                             old="round(elapsed_ms, 1), body, r.status_code)",
                             new='round(elapsed_ms, 1), {**body, "auth": headers["Authorization"]}, r.status_code)'),
    "token-in-header": dict(group="leak", file="server/main.py", tests=LEAK, expect="headers http",
                            old="response.headers.update(SECURITY_HEADERS)",
                            new='response.headers.update({**SECURITY_HEADERS, '
                                '"X-Debug-Auth": await request.app.state.client.tokens.token()})'),
    "log-token": dict(group="leak", file="server/dataapi.py", tests=LEAK, expect="server log",
                      old="            r = await self.http.post(self.rpc_url,",
                      new='            log.info("calling %s with %s", self.rpc_url, headers)\n'
                          '            r = await self.http.post(self.rpc_url,'),
    "lens-says-bearer": dict(group="leak", file="server/main.py", tests=LEAK, expect="'Bearer'",
                             old='AUTH_NOTE = "OAuth token', new='AUTH_NOTE = "Bearer: OAuth token'),
    "live-hostile-markup": dict(group="leak", file="static/app.js",
                                tests=["-m", "live", "tests/test_token_leak_live.py::test_hostile_query_is_text_not_markup_live"],
                                expect="test_hostile_query_is_text_not_markup_live",
                                old="ui.emptyTitle.textContent = `No payer", new="ui.emptyTitle.innerHTML = `No payer"),
    "live-render-missing-field": dict(group="leak", file="static/app.js",
                                      tests=["-m", "live", "tests/test_token_leak_live.py::test_page_renders_only_contract_fields_live"],
                                      expect="test_page_renders_only_contract_fields_live",
                                      old=CELL,
                                      new=CELL + '  li.append(labelled("span", null, String(row.network_status), "network_status"));\n'),
    "no-search": dict(group="leak", file="static/app.js", tests=LEAK, expect="TimeoutError",
                      old='chip.addEventListener("click", () => { searchNow(q); ui.q.focus(); });',
                      new='chip.addEventListener("click", () => { ui.q.focus(); });'),
    # ---- session 3, ticket 03: the definition and the call arguments in the lens (H1-H4, H6) ----
    "copy-one-line": dict(group="definition", file=SQL_COPY, tests=COPY, expect="test_the_repo_function_file_is_the_deployed_blob",
                          old="  who text := current_user;", new="  who text := session_user;"),
    "copy-one-line-live": dict(group="definition", file=SQL_COPY, tests=DEFN_LIVE,
                               expect="test_live_definition_equals_pg_get_functiondef",
                               old="  who text := current_user;", new="  who text := session_user;"),
    "copy-header-invoker-live": dict(group="definition", file=SQL_COPY, tests=DEFN_LIVE,
                                     expect="test_live_definition_equals_pg_get_functiondef",
                                     old="LANGUAGE plpgsql VOLATILE SECURITY DEFINER",
                                     new="LANGUAGE plpgsql VOLATILE SECURITY INVOKER"),
    "copy-header-strict-live": dict(group="definition", file=SQL_COPY, tests=DEFN_LIVE,
                                    expect="header clause the renderer does not know",
                                    old="LANGUAGE plpgsql VOLATILE SECURITY DEFINER",
                                    new="LANGUAGE plpgsql VOLATILE STRICT SECURITY DEFINER"),
    "pin-main-not-deployed": dict(group="definition", file="server/main.py", tests=COPY,
                                  expect="test_the_lens_source_names_the_deployed_commit_and_the_file_blob",
                                  old='DEPLOYED_COMMIT = "b60a1512fd2f43332960ed543021b9e3f20d4114"',
                                  new='DEPLOYED_COMMIT = "a096af2"'),
    "definition-reformatted": dict(group="definition", file="server/main.py", tests=UNIT,
                                   expect="test_lens_definition_is_the_deployed_file_statement",
                                   old='"statement": text[start:end]}', new='"statement": " ".join(text[start:end].split())}'),
    "call-from-defaults": dict(group="definition", file="server/main.py", tests=UNIT,
                               expect="test_lens_function_arguments_are_the_body_the_server_sent",
                               old='"arguments": shown}',
                               new='"arguments": {"q": q, "customer_id": None, "lim": PAGE_SIZE, "off": 0}}'),
    "call-from-defaults-live": dict(group="definition", file="server/main.py", tests=DEFN_LIVE,
                                    expect="test_live_lens_arguments_are_the_body_sent_to_the_branch",
                                    old='"arguments": shown}',
                                    new='"arguments": {"q": q, "customer_id": None, "lim": PAGE_SIZE, "off": 0}}'),
    "call-on-screen": dict(group="definition", file="server/main.py", tests=UNIT, expect="test_lens_holds_no_call_form",
                           old='"definition": request.app.state.definition,',
                           new='"definition": request.app.state.definition, '
                               '"call": f"SELECT * FROM api.search_insurers(q => {q!r})",'),
    # ---- session 3, ticket 05: the diagram in the drawer (T5D-1..T5D-17) ----
    "lens-not-remounted": dict(group="drawer", file="static/app.js", tests=[D + "test_new_search_remounts_the_drawer"],
                               expect="the drawer does not hold one diagram",
                               old="  state.diagram = null;\n  ui.lensDiagram.replaceChildren();\n", new="  state.diagram = null;\n"),
    "args-from-page-1": dict(group="drawer", file="static/app.js", tests=[D + "test_page_two_shows_page_two_and_off_10"],
                             expect="the drawer does not show page two's arguments",
                             old="argsNodes(lens.function.arguments)", new="argsNodes({ ...lens.function.arguments, off: 0 })"),
    "error-keeps-happy-path": dict(group="drawer", file="static/app.js",
                                   tests=[D + "test_failed_search_shows_its_status_and_no_happy_path"],
                                   expect="the drawer shows the happy path on a failed search",
                                   old='ui.lensRun.hidden = which !== "run";', new='ui.lensRun.hidden = which === "empty";'),
    "failed-hides-upstream": dict(group="drawer", file="static/app.js",
                                  tests=[D + "test_failed_search_shows_its_status_and_no_happy_path"],
                                  expect="the drawer hides a status",
                                  old=" The search service answered ${statusText(data.upstream_status)}.", new=""),
    "short-query-keeps-drawer": dict(group="drawer", file="static/app.js", tests=[D + "test_short_query_empties_the_drawer"],
                                     expect="the drawer keeps the diagram on a short query",
                                     old='  show(section);\n  lensShow("empty");', new="  show(section);"),
    "call-form-in-drawer": dict(group="drawer", file="static/index.html", tests=[D + "test_no_call_form_outside_the_definition"],
                                expect="a call form shows outside the definition",
                                old="<dt>Body the app sent to the Data API</dt>",
                                new="<dt>Body the app sent to the Data API (as SELECT * FROM api.search_insurers)</dt>"),
    "tenant-in-drawer": dict(group="drawer", file="static/app.js",
                             tests=[D + "test_tenant_value_and_data_api_host_are_not_on_the_drawer"],
                             expect="the tenant value shows",
                             old='key === "customer_id" ?', new='key === "customer" ?'),
    "definition-typed": dict(group="drawer", file="static/app.js", tests=[D + "test_static_files_hold_no_typed_definition"],
                             expect="a static file types the definition",
                             old='el("pre", "code def-stmt", f.statement)',
                             new='el("pre", "code def-stmt", "CREATE OR REPLACE FUNCTION api.search_insurers(q text)")'),
    "definition-trimmed": dict(group="drawer", file="static/app.js",
                               tests=[D + "test_definition_is_closed_opens_by_click_and_keyboard_and_is_the_payload"],
                               expect="a statement differs from lens.definition",
                               old='el("pre", "code def-stmt", f.statement)',
                               new='el("pre", "code def-stmt", f.statement.split("\\n").slice(0, 30).join("\\n"))'),
    "definition-open": dict(group="drawer", file="static/app.js",
                            tests=[D + "test_definition_is_closed_opens_by_click_and_keyboard_and_is_the_payload"],
                            expect="the definition opens by default",
                            old='  box.id = "lens-def";\n', new='  box.id = "lens-def";\n  box.open = true;\n'),
    "definition-from-elsewhere": dict(group="drawer", file="static/app.js", tests=[D + "test_app_reads_only_lens_fields_the_server_sends"],
                                      expect="is not in the payload",
                                      old="definitionNode(lens.definition)", new="definitionNode(lens.sql_definition || lens.definition)"),
    "diagram-copy-drift": dict(group="drawer", file="static/diagram.js", tests=[D + "test_static_diagram_is_the_approved_copy"],
                               expect="static/diagram.js is not the approved copy",
                               old='node("h2", "pfd-title", ["How this search ran"])', new='node("h2", "pfd-title", ["How the search ran"])'),
    "drawer-frame-layout": dict(group="drawer", file="static/app.css", tests=[D + "test_drawer_layout_is_the_checked_page_layout"],
                                expect="the drawer frame is not the checked page layout",
                                old="#lens-diagram .pfd { --u: 1px; padding: 0; min-height: 0; }",
                                new="#lens-diagram .pfd { min-height: 0; }"),
    "drawer-autoplays": dict(group="drawer", file="static/app.js",
                             tests=[D + "test_drawer_opens_on_the_final_frame_and_never_autoplays"],
                             expect="the drawer autoplays",
                             old="  }, { controls: true });\n",
                             new="  }, { controls: true });\n  root.getAnimations({ subtree: true }).forEach((a) => a.play());\n"),
    "fact-only-in-motion": dict(group="drawer", file="static/app.css", tests=[D + "test_every_fact_is_in_visible_text"],
                                expect="a fact is not in visible text",
                                old="#lens-diagram { max-width: 672px;", new="#lens-diagram .pfd-full { visibility: hidden; }\n#lens-diagram { max-width: 672px;"),
    "typed-ok": dict(group="drawer", file="static/app.js", tests=[D + "test_unknown_status_shows_bare"],
                     expect="the drawer types a status reason",
                     old=": String(code); }", new=": `${code} OK`; }"),
    "play-keeps-definition-open": dict(group="drawer", file="static/app.js", tests=[D + "test_play_closes_the_definition"],
                                       expect="Play animates over an open definition",
                                       old='  root.addEventListener("click", (e) => { if (e.target.closest(".pfd-play")) def.open = false; }, true);\n',
                                       new=""),
    "drawer-overclaim": dict(group="drawer", file="static/index.html", tests=[D + "test_drawer_copy_lint"],
                             expect="the drawer copy says a rejected word",
                             old="Measured by the app server:", new="Measured by the app server, instant:"),
    "diagram-css-missing": dict(group="drawer", file="static/index.html", tests=[D + "test_page_loads_the_diagram_from_the_same_origin"],
                                expect="the page does not load diagram.css",
                                old='  <link rel="stylesheet" href="/static/diagram.css">\n', new=""),
    "tenant-in-drawer-live": dict(group="leak", file="static/app.js",
                                  tests=["-m", "live", "tests/test_token_leak_live.py::test_drawer_shows_the_definition_but_not_the_tenant_or_the_host_live"],
                                  expect="the tenant value is on the drawer",
                                  old='key === "customer_id" ?', new='key === "customer" ?'),
    "definition-hidden-live": dict(group="leak", file="static/app.js",
                                   tests=["-m", "live", "tests/test_token_leak_live.py::test_drawer_shows_the_definition_but_not_the_tenant_or_the_host_live"],
                                   expect="definition not shown",
                                   old='el("pre", "code def-stmt", f.statement)', new='el("pre", "code def-stmt", "")'),
    # ---- session 3 fix round (T6-1..T6-16) ----
    "payload-tenant": dict(group="unit", file="server/main.py",
                           tests=["tests/test_server_unit.py::test_no_response_body_holds_the_tenant_or_a_host"],
                           expect="a tenant value or a host is in a 200 answer",
                           old='shown = {**res.request_body, "customer_id": None}', new="shown = res.request_body"),
    "payload-full-url": dict(group="unit", file="server/main.py",
                             tests=["tests/test_server_unit.py::test_no_response_body_holds_the_tenant_or_a_host"],
                             expect="a tenant value or a host is in a 200 answer",
                             old='"url": RPC_PATH,', new='"url": c.rpc_url,'),
    "typed-200-status": dict(group="unit", file="server/main.py", tests=["tests/test_server_unit.py::test_lens_status_is_the_upstream_status"],
                             expect="the lens status is not the upstream status",
                             old='"status": res.status,', new='"status": 200,'),
    "zero-ms-shown": dict(group="drawer", file="static/app.js",
                          tests=[D + "test_drawer_shows_no_number_when_the_timing_is_missing_or_zero"],
                          expect="a timing shows", old="Number.isFinite(ms) && ms >= 1 ?", new="Number.isFinite(ms) && ms >= 0 ?"),
    "not-json-says-200": dict(group="drawer", file="static/app.js", tests=[D + "test_a_200_answer_that_is_not_json_says_so"],
                              expect="the drawer does not say the answer is not JSON",
                              old='function failedText(res, data) {\n  if (res.ok && !data) return "The app server sent an answer that is not JSON.";\n',
                              new="function failedText(res, data) {\n"),
    "drawer-error-hides-results": dict(group="drawer", file="static/app.js",
                                       tests=[D + "test_results_show_when_the_drawer_cannot_draw_the_search"],
                                       expect="the results do not show when the drawer fails",
                                      old="  renderResults(data, page);\n  try {\n    renderLens(data, appStatus);\n  } catch (err) {",
                                      new="  try {\n    renderLens(data, appStatus);\n    renderResults(data, page);\n  } catch (err) {"),
    "drawer-error-uncaught": dict(group="drawer", file="static/app.js",
                                  tests=[D + "test_results_show_when_the_drawer_cannot_draw_the_search"],
                                  expect="the drawer does not say it failed",
                                  old="  try {\n    renderLens(data, appStatus);\n  } catch (err) {\n"
                                      '    lensFailed("The drawer could not show this search. The results are not affected.", "");\n  }\n',
                                  new="  renderLens(data, appStatus);\n"),
    "diagram-404-drops-facts": dict(group="drawer", file="static/app.js", tests=[D + "test_results_show_when_the_diagram_does_not_load"],
                                    expect="the drawer does not say the diagram is missing",
                                    old="  try {\n    mountDiagram(data, appStatus);\n  } catch (err) {\n    stopDiagram();\n"
                                        '    ui.lensDiagram.replaceChildren(el("p", "step-note", "The diagram did not load. The facts below are from this search."));\n  }\n',
                                    new="  mountDiagram(data, appStatus);\n"),
    "listeners-leak": dict(group="drawer", file="static/app.js", tests=[D + "test_five_searches_leave_one_set_of_listeners"],
                           expect="live matchMedia listeners, ResizeObservers after 5 searches",
                           old="  if (state.diagram) state.diagram.destroy();\n", new=""),
    "observer-leak": dict(group="drawer", file="diagram/diagram.js", tests=[D + "test_five_searches_leave_one_set_of_listeners"],
                          expect="live matchMedia listeners, ResizeObservers after 5 searches",
                          old="      if (observer) observer.disconnect();\n", new="", copy_to="static/diagram.js"),
    "old-motion-not-cancelled": dict(group="drawer", file="static/app.js", tests=[D + "test_new_search_remounts_the_drawer"],
                                     expect="the old motion still plays",
                                     old="  if (old) old.getAnimations({ subtree: true }).forEach((a) => a.cancel());\n", new=""),
    "no-searching-state": dict(group="drawer", file="static/app.js", tests=[D + "test_drawer_says_searching_while_a_new_search_runs"],
                               expect="the drawer does not say it is searching",
                               old='  lensShow("searching");\n', new=""),
    "rejected-words-back": dict(group="drawer", file="server/main.py", tests=[D + "test_rejected_words_are_in_no_shipped_file"],
                                expect="a rejected word is in main.py",
                                old="service principal. The browser never gets it.", new="service principal. It stays on the app server."),
    "auth-old-wording": dict(group="drawer", file="diagram/diagram.js",
                             tests=[D + "test_authorization_line_is_the_approved_form_in_every_shipped_file"],
                             expect="diagram.js: the Authorization line is not the approved form",
                             old="service principal. The browser never gets it.", new="service principal. It stays on the app server."),
    "auth-other-wording": dict(group="drawer", file="static/diagram.js",
                               tests=[D + "test_drawer_shows_only_the_approved_authorization_line"],
                               expect="the drawer's Authorization line is not the approved form",
                               old="service principal. The browser never gets it.", new="service principal. The browser never sees it."),
    "auth-other-wording-server": dict(group="drawer", file="server/main.py",
                                      tests=[D + "test_drawer_shows_only_the_approved_authorization_line"],
                                      expect="the payload's Authorization line is not the approved form",
                                      old="service principal. The browser never gets it.", new="service principal. Only the server holds it."),
    # ---- session 3 fix round 2 (panel review, T6-17..T6-30) ----
    "front-names-cause": dict(group="drawer", file="static/index.html", tests=[D + "test_front_screen_failure_is_plain_words"],
                              expect="the front screen names a technical cause",
                              old='id="error-body">Try again in a minute.</p>',
                              new='id="error-body">The search service answered with status 403.</p>'),
    "front-other-words": dict(group="drawer", file="static/index.html", tests=[D + "test_front_screen_failure_is_plain_words"],
                              expect="the front screen failure is not the plain pair",
                              old='id="error-body">Try again in a minute.</p>', new='id="error-body">Please try later.</p>'),
    "title-hidden-without-diagram": dict(group="drawer", file="static/app.css",
                                         tests=[D + "test_drawer_title_shows_when_the_diagram_did_not_mount"],
                                         expect="the drawer title is hidden while no diagram is mounted",
                                         old=".lens.diagram-on .lens-head h2 {",
                                         new=".lens:has(#lens-run:not([hidden])) .lens-head h2 {"),
    "box1-dataapi-status": dict(group="drawer", file="static/app.js", tests=[D + "test_box_1_answer_is_the_apps_own_status"],
                                expect="box 1 does not show the app's own status",
                                old="    status: appStatus,\n", new="    status: lens.status,\n"),
    "dataapi-line-unnamed": dict(group="drawer", file="static/index.html", tests=[D + "test_box_1_answer_is_the_apps_own_status"],
                                 expect="the Data API line does not name the Data API",
                                 old="<dt>Answer from the Data API</dt>", new="<dt>Answer</dt>"),
    "timing-restarts-on-retry": dict(group="unit", file="server/dataapi.py", tests=["tests/test_server_unit.py::test_timing_includes_retries"],
                                     expect="the timing restarts on a retry",
                                     old="            r = await self.http.post(self.rpc_url,",
                                     new="            t0 = time.perf_counter()\n            r = await self.http.post(self.rpc_url,"),
    "note-old-wording": dict(group="look", file="static/index.html", tests=["tests/test_static_checks.py::test_measured_note_is_the_only_allowed_form"],
                             expect="the Measured by note is not the approved form",
                             old="from its first request to the Data API until the answer, including retries.",
                             new="its request to the Data API and back."),
    "evidence-wider-than-c7": dict(group="look", file="static/index.html",
                                   tests=["tests/test_static_checks.py::test_evidence_token_answer_is_the_only_allowed_form"],
                                   expect="the evidence answer about the token is not the approved form",
                                   old="<dd>No. The app server holds the token; the browser never gets it.",
                                   new="<dd>No. The app server holds the token and sends it only to the Data API."),
    "missing-env-passes": dict(group="unit", file="tests/test_tracked_files.py",
                               tests=["tests/test_tracked_files.py::test_a_missing_env_skips_and_never_passes"],
                               expect="DID NOT RAISE",
                               old='        pytest.skip("no .env, so no deployment values to look for (shape checks still run)")',
                               new="        return {}"),
    # ---- session 4 item 1: the searching status line (I18) ----
    "searching-no-delay": dict(group="searching", file="static/app.js", tests=[S + "test_a_fast_search_never_shows_the_searching_line"],
                               expect="a fast search showed",
                               old="const SEARCHING_MS = 300;", new="const SEARCHING_MS = 0;"),
    "searching-after-answer": dict(group="searching", file="static/app.js", tests=[S + "test_a_fast_search_never_shows_the_searching_line"],
                                   expect="a fast search showed",
                                   old="  if (seq !== state.seq) return;\n  clearTimeout(state.slow);\n",
                                   new="  if (seq !== state.seq) return;\n"),
    "searching-never": dict(group="searching", file="static/app.js", tests=[S + "test_a_slow_search_shows_the_line_after_300_ms_and_keeps_the_old_results"],
                            expect="line after 300 ms",
                            old="  ui.status.textContent = `Searching for “${q}”…`;\n", new=""),
    "searching-hides-results": dict(group="searching", file="static/app.js", tests=[S + "test_a_slow_search_shows_the_line_after_300_ms_and_keeps_the_old_results"],
                                    expect="the old results did not stay",
                                    old="  if (ui.results.hidden) {\n    ui.groups.replaceChildren();",
                                    new="  ui.groups.replaceChildren();\n  if (ui.results.hidden) {"),
    "searching-first-hidden": dict(group="searching", file="static/app.js", tests=[S + "test_a_slow_first_search_shows_the_line_with_no_results_under_it"],
                                   expect="no status line on a slow first search",
                                   old='    show("results");\n  }\n  ui.status.textContent = `Searching', new='  }\n  ui.status.textContent = `Searching'),
    # ---- session 4 item 2: settings from the environment, no value in a tracked file (I9, I10) ----
    "tenant-default-back": dict(group="settings", file="server/main.py", tests=["tests/test_settings.py"],
                                expect="DID NOT RAISE",
                                old='tenant = tenant or setting(e, "FINDER_TENANT")',
                                new=lambda: f'tenant = tenant or e.get("FINDER_TENANT", "{_dotenv("FINDER_TENANT")}")'),
    "placeholder-accepted": dict(group="settings", file="server/main.py", tests=["tests/test_settings.py"],
                                 expect="DID NOT RAISE",
                                 old='if not value or value.startswith("<"):', new="if not value:"),
    "data-api-base-unchecked": dict(group="settings", file="server/main.py",
                                    tests=["tests/test_settings.py::test_server_refuses_to_start_without_a_data_api_base"],
                                    expect="DID NOT RAISE",
                                    old="    if client is None:\n        data_api_base(e)\n", new=""),
    "tenant-planted": dict(group="settings", file="app.yaml", tests=["tests/test_tracked_files.py"],
                           expect="tracked files hold the tenant",
                           old='  - name: FINDER_TENANT\n    value: "<set by scripts/stage_deploy.py>"',
                           new=lambda: f'  - name: FINDER_TENANT\n    value: "{_dotenv("FINDER_TENANT")}"'),
    "data-api-host-planted": dict(group="settings", file="app.yaml",
                                  tests=["tests/test_tracked_files.py::test_no_tracked_file_holds_an_identifying_value[data-api-host]"],
                                  expect="tracked files hold the data-api-host",
                                  old='  - name: DATA_API_BASE\n    value: "<set by scripts/stage_deploy.py>"',
                                  new=lambda: f'  - name: DATA_API_BASE\n    value: "{_dotenv("DATA_API_BASE_PRODUCTION")}"'),
    "stage-copies-env": dict(group="deploy", file="scripts/stage_deploy.py",
                             tests=["tests/test_stage_deploy.py::test_allowlist_does_not_name_env_or_tests"],
                             expect="test_allowlist_does_not_name_env_or_tests",
                             old='ALLOW = ("app.yaml", "requirements.txt")',
                             new='ALLOW = ("app.yaml", "requirements.txt", ".env")'),
    "stage-accepts-branch": dict(group="deploy", file="scripts/stage_deploy.py",
                                 tests=["tests/test_stage_deploy.py::test_refuses_a_branch_data_api_url"],
                                 expect="DID NOT RAISE",
                                 old='    if production_base and value.rstrip("/") != production_base.rstrip("/"):\n        raise RuntimeError("DATA_API_BASE is not production")\n',
                                 new=""),
    "stage-keeps-placeholder": dict(group="deploy", file="scripts/stage_deploy.py",
                                    tests=["tests/test_stage_deploy.py::test_staged_app_yaml_holds_production_and_the_tenant"],
                                    expect="test_staged_app_yaml_holds_production_and_the_tenant",
                                    old="            out.write_text(_fill_yaml(src.read_text(), base, tenant))\n",
                                    new="            shutil.copy2(src, out)\n"),
    "cookie-planted": dict(group="deploy", file="docs/STATE.md",
                           tests=["-m", "deployed", "tests/test_deployed_app.py::test_no_cookie_line_in_tracked_files_or_runs"],
                           expect="cookie or session lines",
                           old="# STATE.md\n", new="# STATE.md\nSet-Cookie: session=planted\n"),
    "top-hit-wrong": dict(group="deploy", file="tests/test_deployed_app.py",
                          tests=["-m", "deployed", "tests/test_deployed_app.py::test_twelve_top_hit_queries"],
                          expect="top-hit misses",
                          old='        if case["expected_brand"] not in top:\n',
                          new='        if True:\n'),
    # ---- session 5: review panel (J1, J13, item 2); the live and deployed ones need FINDER_TARGET=production ----
    "panel-scan-blind-to-secret": dict(group="panel", file="tests/test_panel.py",
                                       tests=["tests/test_panel.py::test_leak_scan_finds_each_planted_value"],
                                       expect="oauth-secret",
                                       old='re.compile(r"\\bdose[0-9a-f]{16,}")', new='re.compile(r"(?!)")'),
    "panel-lint-blind": dict(group="panel", file="tests/test_panel.py",
                             tests=["tests/test_panel.py::test_overclaim_lint_catches_a_planted_line"],
                             expect="assert []",
                             old='        if PUBLIC_DENIAL.search(ln) or (OVERCLAIM.search(ln) and "PUBLIC" not in ln):\n',
                             new='        if False:\n'),
    "panel-overclaim-on-screen": dict(group="panel", file="static/index.html",
                                      tests=["tests/test_panel.py::test_no_public_overclaim_on_screen_or_in_the_claims"],
                                      expect="PUBLIC has no rights",
                                      old="No right on any data table.", new="No right on any data table. PUBLIC has no rights."),
    "panel-public-temp-claimed-gone": dict(group="panel", file="tests/test_panel.py",
                                           tests=["-m", "live", "tests/test_panel.py::test_public_defaults_on_production_are_the_pinned_set"],
                                           expect="TEMPORARY",
                                           old='PUBLIC_DB = {"CONNECT", "TEMPORARY"}', new='PUBLIC_DB = {"CONNECT"}'),
    "panel-deployed-planted-secret": dict(group="panel", file="tests/test_panel.py",
                                          tests=["-m", "deployed", "tests/test_panel.py::test_deployed_responses_hold_no_secret_by_shape"],
                                          expect="deployed responses hold",
                                          old='    hits = leak_hits("\\n".join(parts), live_env)\n',
                                          new='    hits = leak_hits("\\n".join(parts) + "dose0123456789abcdef0123", live_env)\n'),
    # ---- session 4 item 3: the role's source and the read-only path (I1, I3, I4), on the t-finder branch ----
    "role-not-for-authenticator": dict(group="grants", tests=["-m", "live", "tests/test_live_branch.py::test_app_sp_role_is_a_data_api_role"],
                                       expect="authenticator is not a member",
                                       apply=f"REVOKE {SP} FROM authenticator;", revert=f"GRANT {SP} TO authenticator;"),
    "ro-path-writes": dict(group="grants", file="scripts/psql_ro.sh", tests=["-m", "live", "tests/test_live_branch.py::test_read_only_path_refuses_a_write"],
                           expect="the read-only path accepted a CREATE",
                           old='export PGOPTIONS="-c default_transaction_read_only=on"\n', new=""),
    "ro-wrong-endpoint": dict(group="grants", file="scripts/psql_ro.sh", tests=GRANTS,
                              expect="did not reach the",
                              old='branches/${TARGET}/endpoints', new='branches/production/endpoints'),
    "searching-ignores-seq": dict(group="searching", file="static/app.js", tests=[S + "test_clearing_the_box_during_a_slow_search_shows_no_searching_line"],
                                  expect="a cancelled search showed",
                                  old="if (seq === state.seq) showSearching(q);", new="showSearching(q);"),
}


def pytest(args: list[str]) -> tuple[int, str]:
    # A same-size mutation restored within the same second keeps its mtime and size, so a .pyc written
    # during the mutated run would be reused by the restored run.
    p = subprocess.run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", "-x", *args],
                       cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return p.returncode, redact(p.stdout + p.stderr)


_JWT = re.compile(r"eyJ[A-Za-z0-9_.-]{8,}(?:\.\.\.[A-Za-z0-9_.-]*)?")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")


# Any SP id, whole or as pytest shortens it in a diff ("14e0ad94-5ae...-e118d4fca22f").
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{8}-[0-9a-f-]{0,12}\.\.\.[0-9a-f-]*", re.I)
_BRANCH_HOST = re.compile(r"ep-[a-z0-9]+(?:-[a-z0-9]+)+\.database\.[a-z0-9.-]+", re.I)


def redact(text: str) -> str:
    """Leak mutations put the real token in failure output; the log must never keep it. Nor may a tracked log keep
    a value that identifies the deployment: the SP ids, the tenant label, the workspace host or a branch host."""
    text = _BEARER.sub(r"\1<redacted>", _JWT.sub("<jwt:redacted>", text))
    secret = os.environ.get("APP_SP_CLIENT_SECRET") or _dotenv("APP_SP_CLIENT_SECRET")
    if secret:
        text = text.replace(secret, "<secret:redacted>")
    return mask(text)


def mask(text: str) -> str:
    for key in ("APP_SP_CLIENT_ID", "APP_SP_ID"):
        if len(value := _dotenv(key)) >= 6:
            text = text.replace(value, "<sp-id:masked>")
    if len(value := _dotenv("APP_SP_SECRET_ID")) >= 6:
        text = text.replace(value, "<secret-id:masked>")
    if len(tenant := _dotenv("FINDER_TENANT")) >= 6:
        text = text.replace(tenant, "<tenant:masked>")
    host = _dotenv("WORKSPACE_HOST")
    if host:
        text = re.sub(re.escape(re.sub(r"^https?://", "", host).split("/")[0]), "<host:masked>", text, flags=re.I)
    for ws_id in set(re.findall(r"/workspace/(\d{6,})", _dotenv("DATA_API_BASE_PRODUCTION"))):
        text = text.replace(ws_id, "<workspace-id:masked>")
    text = _UUID.sub("<uuid:masked>", text)
    return _BRANCH_HOST.sub("<host:masked>", text)


def _dotenv(key: str) -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


_sp: list[str] = []


def app_sp() -> str:
    if not _sp:
        _sp.append(subprocess.run(
            ["uv", "run", "python", "-c",
             "from databricks.sdk import WorkspaceClient as W; import tests.conftest as c; e = c._dotenv(); "
             "print(W(profile=e['DATABRICKS_PROFILE']).apps.get(e['APP_NAME']).service_principal_client_id)"],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
    return _sp[0]


def sql(statement: str) -> None:
    """A GRANT the owner may not give only warns ("no privileges were granted"): treat it as a setup failure,
    or the mutation is a no-op and the green run proves nothing."""
    p = subprocess.run([str(ROOT / "scripts/psql.sh"), "-q", "-v", f"sp={app_sp()}", "-f", "-"], input=statement,
                       cwd=ROOT, check=True, capture_output=True, text=True)
    if "WARNING" in p.stderr:
        raise RuntimeError(f"mutation SQL did not take effect: {p.stderr.strip()}")


def summary(out: str) -> str:
    """The lines that say why a run failed; the 6000-character tail can cut them off."""
    lines = out.splitlines()
    keep = [ln for ln in lines if ln.startswith("E ")][:60]
    if "short test summary info" in out:
        start = next(i for i, ln in enumerate(lines) if "short test summary info" in ln)
        keep += lines[start:start + 20]
    return "\n".join(keep)


def mutate(name: str) -> bool:
    m = M[name]
    is_code = "file" in m
    if is_code:
        path = ROOT / m["file"]
        original = path.read_bytes()
        copy = ROOT / m["copy_to"] if "copy_to" in m else None
        copy_original = copy.read_bytes() if copy else None
        if original.decode().count(m["old"]) != 1:
            print(f"BAD {name:24s} SETUP: the anchor is not in {m['file']} exactly once")
            return False
    applied = False
    try:
        if is_code:
            # A callable `new` reads a deployment value from .env at run time, so no tracked file holds it.
            new = m["new"]() if callable(m["new"]) else m["new"]
            path.write_text(original.decode().replace(m["old"], new))
            if copy:
                copy.write_bytes(path.read_bytes())
        else:
            sql(m["apply"])
        applied = True
        rc, out = pytest(m["tests"])
    finally:
        if is_code:
            path.write_bytes(original)
            if copy:
                copy.write_bytes(copy_original)
        elif applied:
            sql(m["revert"])
    rc2, out2 = pytest(m["tests"])
    ok = rc == 1 and m["expect"] in out and rc2 == 0
    with LOG.open("a") as f:
        f.write(f"\n===== {dt.datetime.now().isoformat(timespec='seconds')} {name} "
                f"mutated_rc={rc} restored_rc={rc2} verdict={'RED-THEN-GREEN' if ok else 'FAIL'}\n")
        f.write("----- E lines and short summary\n" + summary(out) + "\n----- tail\n")
        f.write(out[-6000:])
        f.write(f"\n----- restored run\n{out2[-1500:]}\n")
    failed = next((ln for ln in out.splitlines() if ln.startswith(("FAILED", "ERROR"))), "")
    print(f"{'OK ' if ok else 'BAD'} {name:24s} mutated_rc={rc} restored_rc={rc2} {failed[:120]}")
    return ok


def main(argv: list[str]) -> int:
    if argv[:1] == ["--mask"]:  # mask files in place, e.g. a log written by hand
        for name in argv[1:]:
            path = Path(name)
            text = path.read_text()
            if (new := mask(text)) != text:
                path.write_text(new)
                print(f"masked {name}")
        return 0
    LOG.parent.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for a in argv or ["all"]:
        names += [n for n, m in M.items() if a in ("all", m["group"])] if a in ("all", *GROUPS) else [a]
    results = [mutate(n) for n in names]
    print(f"{sum(results)}/{len(results)} red-then-green; log: {LOG.relative_to(ROOT) if LOG.is_relative_to(ROOT) else LOG}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
