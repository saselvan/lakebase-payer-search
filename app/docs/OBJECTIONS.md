# OBJECTIONS.md: the hostile-reviewer pass (session 1, written before code)

Every way to break each "must never", the test that catches it, and the mutation that shows the test red.
`SESSION_PLAN.md` items 4-6 are the minimum, not the scope. Recorded runs live in `runs/`.

## A. A token, `Bearer` header or client secret reaches the browser (risk f, claim C7)

| # | Attack | Test | Red proof (mutation) |
|---|---|---|---|
| A1 | The server copies its `Authorization` header into a JSON response | `test_token_leak_live` scans every browser response body | `echo-auth-header` |
| A2 | The token sits in an error message (token exchange fails, upstream 401/403/500 text echoed) | unit `test_upstream_error_body_never_reaches_the_browser`; live leak test drives a 400 path | `echo-upstream-error` |
| A3 | The JS is built with env vars (Vite `import.meta.env`, a templated `index.html`) | no build step; leak test scans HTML, JS, CSS, fonts as served; unit test: static files hold no `process.env`, `import.meta.env`, or template marks | `env-in-html` |
| A4 | The token rides in a redirect (`Location`), a cookie (`Set-Cookie`), or any response header | leak test scans every response header too | `token-in-header` |
| A5 | A log or debug panel the page shows contains the token | the page shows no server log; the lens shows a request built from an allowlist, never the sent headers | `log-token` (server log), `lens-says-bearer` (lens text) |
| A6 | The lens prints the word `Bearer` to "explain" auth | leak test bans `Bearer` (any case) in every response, the DOM and the lens | `lens-says-bearer` |
| A7 | Path traversal or a stray route serves `.env`, `app.yaml`, server source | unit `test_no_file_outside_static_is_served` (`/.env`, `/static/../.env`, encoded forms) | `serve-repo-root` |
| A8 | FastAPI's `/docs`, `/openapi.json` or a debug route shows config | unit test: those routes 404 | `enable-docs` |
| A9 | The server log (stdout) holds the token, and a later tool shows it | live leak test also scans the server's own stdout/stderr | `log-token` |
| A10 | The browser gets a token by calling the Data API directly | CSP `connect-src 'self'`; unit test checks the header; leak test asserts no request left the app's origin | `csp-open` |
| A11 | A short or reused value matches by accident, or the scan misses a split token | leak test checks the full token, its JWT signature part, and the secret, each asserted long enough to be unique | (precondition in the test) |
| A12 | The test passes because it captured nothing (page did not load, no search ran) | leak test asserts it saw the HTML, the JS, the CSS and at least one 200 search with rows | `no-search` |

## B. The app's SP gets any grant beyond the Caller's two EXECUTEs (risk d, claim C6)

| # | Attack | Test | Red proof |
|---|---|---|---|
| B1 | A direct table grant | `test_app_sp_grants`: `has_table_privilege` over every table, view, matview in every non-system schema, all privileges | `GRANT SELECT ON search_idx.alias_key TO <sp>` (the plan's mutation) |
| B2 | A grant through role membership | `has_*_privilege` follows membership; the test also asserts the SP is a member of no role | `GRANT search_owner TO <sp>` |
| B3 | A grant to `PUBLIC` | `has_*_privilege` includes PUBLIC; the function check counts every function in non-system schemas | `grant-public-table` |
| B4 | CREATE on a pinned schema lets the SP plant a shadow function | no CREATE on any non-system schema and no CREATE on the database (TEMP comes from the PUBLIC default: see REGISTER.md) | `GRANT CREATE ON SCHEMA api TO <sp>` |
| B5 | A third EXECUTE | direct grants = exactly the two EXECUTEs plus USAGE on `api` and `search_idx`; every function the SP can run in a data schema is one of the two; the rest is a pinned allowlist of platform defaults | `grant-third-execute` (owner-made function, direct GRANT), `public-execute-default` (new function in `api`, PUBLIC default) |
| B6 | Role attributes (`CREATEROLE`, `CREATEDB`, `BYPASSRLS`, `SUPERUSER`) | the test reads `pg_roles` | (assertion only) |
| B7 | The test checks the wrong role name and passes on an empty role | the role name comes from `databricks apps get` at test time; the test asserts the role exists and the two EXECUTEs are TRUE | run against the Caller SP's id: red on the identity check |
| B8 | Sequences or column-level grants | the test covers sequences and column privileges | `GRANT SELECT (payer_id) ON search_idx.alias_key TO <sp>` |

## C. Any write to production

| # | Attack | Guard |
|---|---|---|
| C1 | A script runs on production because `BRANCH` is unset | `scripts/psql.sh` refuses unless `BRANCH` matches `^t-finder-`; no default |
| C2 | Live tests hit production's Data API because `.env` holds production's URL | live tests read the branch URL at test time and refuse if it equals production's |
| C3 | A mutation meant for the branch lands on production | mutations go through `scripts/psql.sh` (C1) |
| C4 | The branch outlives the session | TTL 4 h at create; item 7 deletes it and lists branches |

## D. A claim without grade 1-3 evidence appears on screen

| # | Attack | Guard |
|---|---|---|
| D1 | The page says "instant", "secure", "no credentials", "real insurer data" | `test_copy_lint`: forbidden phrases absent from served HTML/JS |
| D2 | A speed number is typed, not measured | the only number is the measured ms of this response, labelled as measured |
| D3 | Synthetic IDs read as real | `test_copy_lint` requires the synthetic label (risk c) |
| D4 | The Data API status (C5, UNPROVEN) is stated | copy lint bans "generally available", "GA", "preview" |
| D5 | Examples do not come from the shared top-hit list | unit test: every example is a query in the public repo's `data/test_top_hit_cases.json` |

## E. A test passes without having been shown red

| # | Attack | Guard |
|---|---|---|
| E1 | The mutated run fails for another reason (setup error) | `scripts/mutate.py` requires pytest exit 1 (tests ran; one failed or errored), not 2-5 (interrupted, internal error, usage, no tests), plus the expected text in the output |
| E2 | A mutation stays in the code | `mutate.py` restores the file byte for byte (or runs the SQL revert) and re-runs green; `git status` must be clean |

## F. Other ways the demo lies or breaks (Search Critic, skeptic)

| # | Attack | Guard |
|---|---|---|
| F1 | A hostile query (`<img onerror>`, SQL text) runs script in the page | results render with `textContent` only; browser test types the payload and asserts no dialog and literal text |
| F2 | The browser sets `customer_id`, `lim`, `off` | the server accepts only `{q, page}`, rejects extra fields, computes `lim`/`off`; unit test |
| F3 | A field the API lacks is rendered (`network_status`) | `test_contract_fields` (item 6) |
| F4 | `score` is shown (the contract forbids it) | contract test |

## G. The brief and the look lie or mislead (session 2, written before code)

| # | Attack | Guard |
|---|---|---|
| G1 | The top result claims "best" when ten plans tie and the alphabet picks the winner | no "best" in copy (copy lint); results group by brand plan, so a tie shows as one group with its states |
| G2 | The group key invents data (a brand the API does not return) | key = `payer_name` minus ` - {state}` only when the suffix equals the row's `state`; unit test on the rule, with a row whose suffix differs |
| G3 | A group count reads as a total ("10 states") when more rows sit on the next page | the count names what it counts: "10 states on this page", or the end note says the list continues |
| G4 | A timing on the front screen reads as the product's speed (a cold start read as slow) | no timing outside the drawer; copy check on the front screen |
| G5 | The drawer claims grants or SQL it did not read live | the drawer names the grants test and its run date, never a grants list typed as text; no SQL text unless read live |
| G6 | The mark or the words look like another product (a to-do app, a claims tool) | wordmark only; the 5-second test names "insurer lookup" |
| G7 | The look passes CSS-text checks but fails in the browser (computed size, contrast) | Playwright reads computed styles: body >= 16px, labels >= 13px, targets >= 32px, contrast table from computed colours |
| G8 | Color carries meaning alone, or collides with clinical status colours (red/amber/green) | the accent is ultramarine; no red/amber/green for state; greyscale screenshot check |

## H. The drawer's motion diagram, the definition and the call lie or leak (session 3, ticket 01, written before code)

The diagram is a HyperFrames composition in the drawer (DECISIONS.md, owner, for session 3). Each guard names its check and the ticket that builds it; each new check needs a red run before it counts (section E).

| # | Attack | Guard (the check that catches it) | Ticket |
|---|---|---|---|
| H1 | The function definition is typed by hand, or copied from a commit that is not the deployed one | the definition is a copy of the public repo's `sql/20_search_function.sql` at the commit in claim C15; a unit test compares the copy byte for byte with `git show <commit>:sql/20_search_function.sql` and its blob hash; red: a one-line change in the copy | 03 |
| H2 | The copy matches the repo, but the database holds another version (a later hotfix, or a branch made before the deploy) | live test: the shown text equals `pg_get_functiondef` for each shown function on the test branch; red: a one-line change in the copy. Before a showing on production, the C15 row is re-read against a fresh production read (Samuel's `!` command), never assumed | 03 |
| H3 | Only the body is compared, so a changed header (arguments, return type, `SECURITY DEFINER`, the pinned `search_path`) passes | the live test compares the whole `pg_get_functiondef` text, not `prosrc`; the deploy record's body-only check (C15) is named as body-only | 03 |
| H4 | The drawer shows a call form ("the SQL that ran", for example `SELECT * FROM api.search_insurers(q => …)`) that no Databricks doc states | claim C14 decides: grade 3 (a quoted docs.databricks.com passage) or UNPROVEN. If UNPROVEN, a copy check bans `SELECT`, `=>` and "SQL that ran" in the drawer text; red: `call-on-screen` puts the call in the payload | 01 (verdict), 03, 05 |
| H5 | A PostgREST page is cited as if it were Databricks documentation, and grades the call form 3 | C14 accepts only a docs.databricks.com quote; PostgREST pages are listed as context with grade "lead" | 01 |
| H6 | The arguments shown differ from the request that ran: rebuilt in the browser, taken from the previous search, or defaults in place of the server's `lim`/`off` | the shown arguments are the JSON body the server sent, returned in the lens payload; unit test feeds a search and compares with the captured upstream body; red: `call-from-defaults` (server shows `off=0` on page 2). A browser check runs two searches and asserts the drawer shows the second | 03, 05 |
| H7 | The diagram plays the full success path when the search failed (403, 5xx, timeout) | the diagram reads the response status and stops at the failing hop with its text; browser test with an error fixture; red: `diagram-ignores-status` | 05 |
| H8 | Motion is the only carrier of a fact (a hop, the function name, the row count appears only mid-animation) | every fact is in text in the final frame and in the drawer text; browser check reads the text with animation disabled; red: `fact-only-in-motion` removes one label from the final frame | 04, 05 |
| H9 | Animation length or easing reads as a measured time | the animation never encodes time; the only timing is the measured ms with its wake-up note (D2, G4); copy check: no unit next to an animation label | 04, 05 |
| H10 | Reduced motion is ignored, or only shortened | under `prefers-reduced-motion: reduce` the drawer shows the final frame with no movement; Playwright emulates it and asserts no running animation (`document.getAnimations()` empty, no transform change across two frames); red: `motion-ignores-reduce` | 04, 05 |
| H11 | A script, font, style or runtime loads from another origin (the HyperFrames runtime or IBM Plex from a CDN), or the CSP is widened to allow it | the CSP stays `'self'` (unit test on the header, A10); the leak test asserts no request left the app origin with the drawer open; a static check bans `http://` and `https://` in served `src`/`href`/`@import`/`url()`; red: `cdn-runtime` | 05 |
| H12 | The drawer renders a hostile query as markup inside the composition (SVG or HTML) | the query reaches the diagram as text only (`textContent`); F1's browser test types the payload with the drawer open; red: `diagram-innerhtml` | 05 |
| H13 | The diagram draws an arrow from the app or the Data API to a table, which says the SP reads tables (C6 says it does not) | the hops are a fixed list: browser → app → Data API → `api.search_insurers` → rows; a test reads the composition's hop labels and asserts that list; no table name appears in the diagram | 04, 05 |
| H14 | A blog still or the preview shows data or a URL that is not public: the workspace or app host name, the Data API host, a branch name, the SP id, the tenant label, a token | ticket 04's still holds only public payer names, synthetic IDs and the hop names; a check scans the still's text layer and the composition for `databricksapps.com`, `cloud.databricks.com`, `database.`, `t-finder-`, the SP ids and `customer_id` values, and fails on any; Samuel signs off the still | 04 |
| H15 | The drawer shows `customer_id` (the tenant label) or `score` as a reader-facing fact | the call, if shown, lists the arguments as sent; the contract test keeps `score` in the raw JSON only (decision 4); the copy check names `customer_id` as a server-set label | 03, 05 |
| H16 | The docs change after the read, and the C14 verdict goes stale | C14 carries the date read and the exact quote; phase 5 re-reads the page and diffs the quote | phase 5 |

## I. The deployed app or the production role breaks a rule (session 4, phase 4, written before code)

The app runs on FEVM as `lakebase-payer-finder` and calls production's Data API as its own SP. Production writes and the deploy are Samuel's `!` commands; every check below is a read. Each guard needs a red run before it counts (section E). "Item" is the session 4 item in `SESSION_PLAN.md` that builds it.

| # | Attack | Guard (the check that catches it) | Red proof | Item |
|---|---|---|---|---|
| I1 | The production role is made the wrong way (plain `CREATE ROLE`, the UI, or not granted to `authenticator`), so the Data API answers 403 | live read on production: the role's Lakebase record is a service principal with OAuth login (`list-roles`), and `authenticator` is a member of the role (`pg_auth_members`); the deployed search returns 200 with rows (I13) | on a `t-finder-` branch: `REVOKE <sp> FROM authenticator` turns the membership check red | 3 |
| I2 | Grants drift on production: an extra grant, a role membership, a CREATE, a PUBLIC default on a search function | `test_app_sp_grants` runs against production through a read-only connection, with the same assertions as on branches (B1-B8) | the session-1 grants mutations on a `t-finder-` branch (`mutate.py grants`) | 3 |
| I3 | The "production" grants run reads a branch (or the reverse), so a green run proves the wrong database | the read-only script resolves the endpoint from the target name, prints the branch it reached, and the test asserts it equals the target; the live log header names it | a target `production` with a `t-finder-` endpoint path turns it red | 3 |
| I4 | The read-only path to production writes after all | `scripts/psql_ro.sh` opens every session with `default_transaction_read_only=on`; a live test on a `t-finder-` branch asserts a `CREATE TEMP TABLE` through it fails with SQLSTATE 25006 | remove the read-only option: the CREATE succeeds and the test is red | 3 |
| I5 | An old static file is served after the deploy (`apps deploy <name>` without an upload re-deploys the old workspace copy; a cache) | every file under `static/` and the page at `/` is fetched from the app URL; its SHA-256 equals the local file; both hashes are recorded | one byte changed in a local static file turns it red | 4 |
| I6 | The hash check proves nothing: it fetched from the local server, skipped files, or the app answered a login page with 200 | the base URL is the `url` from `databricks apps get`; the file list is `git ls-files static/` plus `/`; each answer is 200 with the expected content type; a missing file is a failure, not a skip | a file in `static/` that is not deployed turns it red (404) | 4 |
| I7 | The upload holds `.env`, `.venv`, test files or run logs (secrets in the workspace, a local value read at run time) | `scripts/stage_deploy.py` copies an allowlist only (`app.yaml`, `requirements.txt`, `server/`, `static/`, `sql/deployed/`); a unit test asserts the staged tree equals the allowlist; after the upload, `databricks workspace list` of the source path shows no `.env` | add `.env` to the allowlist: the unit test is red | 4 |
| I8 | `apps get` shows an old or failed deployment, and the claims say "deployed" | `apps get`: the active deployment's source path is the staged path, its state is `SUCCEEDED`, the app is `RUNNING`, and its create time is after the staging time | a staging time after the deployment's create time turns it red | 4 |
| I9 | A setting is missing at run time, and the server starts with a code default (an old tenant, a branch URL) | `create_app` refuses to start without `FINDER_TENANT` and `DATA_API_BASE`; no default in code; unit test | put the old default back: the unit test is red | 2 |
| I10 | A tenant value or the Data API host sits in a tracked file (app.yaml, a default, a plan, a log) | `tests/test_tracked_files.py` with no exceptions; the values come from `.env` | a planted tenant value in a tracked file turns it red | 2 |
| I11 | The deployed app points at a branch's Data API, or a branch value fills app.yaml, and the claims say production | the staging script reads production's Data API URL live and refuses a `t-finder-` URL; the deployed search's audit row is read on production (C9, I14) | a staged base that is not production's turns the staging check red | 4, 5 |
| I12 | The leak test passes because it never reached the deployed app (it ran against the local server, the page did not load, no search ran, or it saw a login page) | the deployed leak run asserts the page origin is the `apps get` URL, the served `index.html` hash equals the local file, and at least one search answered 200 with rows | a planted token, `Bearer` value, tenant value and Data API host, injected into one deployed response by the test's own route, each turn it red | 5 |
| I13 | The deployed search answers 403 or 502 and the page shows the error state, so "it works" is said from the drawer of a local run | the deployed check asserts `/api/search` is 200 with 10 rows for `bcbs georgia`, and the 12 top-hit queries return their expected top hit (public repo `data/test_top_hit_cases.json`, read only) | one changed expected top hit turns the check red | 5 |
| I14 | The audit row is read on the wrong database, or for an older search, so C9 is claimed without this search | the deployed search sends a fresh query text; the read-only production query finds a row after the search's start time with that query, the tenant label and the caller = the app SP | a query text that was never sent turns it red | 5 |
| I15 | A cold start is labelled as slowness, or a warm search is labelled cold | the front screen shows no timing (G4); the C22 recorder labels a search "cold" only if production's endpoint read just before it is not `ACTIVE`, and "warm" only if it is; it reads `suspend_timeout_duration` before and after and refuses if it changed; no suspend change on production | a "cold" label with an `ACTIVE` read before turns the recorder red | 5 |
| I16 | Samuel's sign-in (cookie, session, user token) lands in the repo or a run log | the sign-in stays in memory or in a temporary browser profile outside the repo that is deleted after the run; a test scans tracked files and `runs/` for cookie and token patterns | a planted cookie line in a run log turns it red | 4, 5 |
| I17 | The Apps proxy changes the security headers, so the CSP the unit test checks is not the CSP the browser gets | the deployed check asserts the served CSP and `X-Content-Type-Options` equal the app's values on `/`, a static file and `/api/search` | an expected CSP with one source changed turns it red | 5 |
| I18 | The searching line flickers on fast searches, hides the old results, or stays after the answer | browser tests with a fast and a slow fake Data API: no line under 300 ms, the line after 300 ms, old results present during the search, the line replaced by the answer | the delay set to 0 turns the fast check red; no line at all turns the slow check red | 1 |

## J. The review panel, the PUBLIC defaults and the publish step break a rule (session 5, phase 5, written before any revoke or rewrite)

Production revokes and any history rewrite are Samuel's `!` commands, after a DECISIONS.md entry. The agent reads only. The repo has no remote on 2026-10-24, so no history is published yet. "Item" is the session 5 item in `SESSION_PLAN.md`.

| # | Attack | Guard (the check that catches it) | Red proof | Item |
|---|---|---|---|---|
| J1 | A screen, doc or claim says PUBLIC has no rights, or that the SP can do "only" its two EXECUTEs, while platform views, helper functions and TEMP remain | a static test reads `static/*` and `docs/CATECHISM.md`: any sentence on the SP's rights that says "only", "no other" or "nothing else" must name the PUBLIC defaults in the same row or sentence; `test_app_sp_grants` pins the PUBLIC lists on production | a planted "PUBLIC has no rights" line in a fixture turns the static test red | 1, 2 |
| J2 | A PUBLIC default is revoked on production without Samuel's decision | the agent runs no REVOKE; a revoke needs a dated DECISIONS.md entry by Samuel before the `!` command is handed over | process (no agent command) | 2 |
| J3 | A revoke breaks the Data API for every caller (for example, EXECUTE on `pgrst.pre_config()` from PUBLIC, which `authenticator` may need) | before any revoke, the agent names each object, its owner and who uses it; after the `!`, a deployed search returns 200 with rows and the audit row names the app SP | a revoke run on a `t-finder-` branch first, with the branch Data API search, before production | 2 |
| J4 | A revoke does not hold: the platform re-grants on restart, on a new schema (event triggers), or the object belongs to a platform role the owner cannot change | read the right through the read-only path right after the `!` and again after a compute suspend on a `t-finder-` branch; the claim says "revoked" only if both reads agree | the branch read after the suspend shows the right again: the claim stays "not revoked" | 2 |
| J5 | A revoke of TEMP from PUBLIC breaks a platform job (synced tables, the Data API's own session setup) | branch trial first: Data API search and a sync status read on the branch after the revoke | the branch search fails: no production `!` is handed over | 2 |
| J6 | A claim on the drawer or the evidence page traces to no catechism row, or to a row with an old date | the panel table maps each drawer and evidence-page sentence to a row id; C1, C6, C7, C9 carry the date of a deployed run in this session | a sentence with no row in the panel table is an objection, recorded in J | 1 |
| J7 | The deployed files changed since the last hash check, so the drawer claims match a local file the app does not serve | re-run the deployed hash check (`-m deployed`) in this session and record it | one byte changed in a local static file turns it red (I5) | 1 |
| J8 | The history rewrite loses work | a backup ref and a `git bundle` outside the repo, verified with `git bundle verify`, before the `!`; after the rewrite, the tree of each branch tip equals the old tree except the masked lines | a rewrite plan with no backup ref is not handed over | 3 |
| J9 | The history scan misses a value: an old branch host, an expired secret, a JWT, a value in a commit message, in a branch other than `session-4`, or in a deleted file | scan every blob of `git rev-list --all --objects` and every commit message with two sets of patterns: the exact values from `.env`, and shapes (Data API and endpoint host, workspace id, `eyJ` JWT, `dapi`/`dose` tokens, UUID client ids, the tenant label); list each hit with commit, path and kind | a planted value in a scratch clone turns the scan red | 3 |
| J10 | A value sits in a picture or a video (PNG, MP4), which a text scan cannot read | list every binary file in history; each is reviewed by eye or left out of the public tree; the plan says which | the list is in the plan; a binary with no verdict blocks the publish | 3 |
| J11 | The publish carries the old objects: other local branches (`t01`-`t06`, `main`, `session-3`), the reflog, or a stash | publish only from a fresh clone of the rewritten branch; scan that clone again with J9 | a clone that still holds an old branch tip fails the scan | 3 |
| J12 | A cookie or a user token from a browser sign-in lands in the repo | `test_no_cookie_line_in_tracked_files_or_runs` (I16) runs in this session | the mutation `cookie-in-runs` turns it red | 1, 3 |
| J13 | The deployed leak tests check a secret the deployed app never holds: they look for the local 1-day secret, and the app uses the secret the Apps runtime injects. After the 1-day secret is deleted, both tests stop with a `KeyError` before any check | `test_panel.py::test_deployed_responses_hold_no_secret_by_shape`: the page, every static file and 3 searches hold no JWT, `Bearer` value, `dose`/`dapi` token shape, tenant or production host; needs no local secret | `panel-deployed-planted-secret`, `panel-scan-blind-to-secret` | 1 |
| J14 | `test_app_sp_grants` checks CREATE but not TEMP or CONNECT, so a change in PUBLIC's database rights passes unseen | `test_panel.py::test_public_defaults_on_production_are_the_pinned_set` pins PUBLIC's database, schema, relation and non-extension function rights on production | `panel-public-temp-claimed-gone` | 2 |
| J15 | The evidence page's dates and test names are typed strings; a newer run does not change them, and nothing ties them to a catechism row | `test_panel.py::test_page_source_dates_match_catechism_rows`: each source line's test must be named in a catechism row with the same verified date (entry 19, session 6) | red on the old page (`runs/2026-10-25/s6/item1-red.log`); `test_page_source_check_catches_a_stale_date` | 1 |

### Panel record: the DBA/security seat against the live app (2026-10-24, session 5 item 1)

The seat opened the deployed app (`apps get` URL, CLI OAuth in memory) and traced each sentence of the drawer and the evidence page to a claims row. Runs: `runs/2026-10-24/s5/item1-deployed.log` (7 of 9 passed; the 2 value-based leak tests stop on J13), `item1-panel-production.log` (4 of 4), `item1-panel-mutations.log` (5 of 5 red-then-green), `item1-offline.log` (193 passed).

| Sentence on screen | Row | Verdict |
|---|---|---|
| "Payer names are public. Payer IDs and plans are synthetic. No patient data." | C11 | traces |
| Box 2: "Authorization: OAuth token of the app's service principal. The browser never gets it." | C7, C20 | traces; secret checked by shape only (J13) |
| Diagram: "Switches into the app's own Postgres role" | C18, C9 | traces; C18 on a branch, the production audit row names the app SP (C9) |
| "Measured by the app server: from its first request to the Data API until the answer, including retries." | C21 | traces |
| "A first search after a pause can include a database wake-up." | C22 | traces |
| Body sent, `customer_id` by name only; path `/api/rpc/search_insurers` | C17 | traces |
| "Function definition" text | C16, C15 | traces; read on a branch, not on production (C15 limits) |
| "Direct grants: EXECUTE on … USAGE on schemas api and search_idx. No right on any data table." | C6 | traces; true for direct grants, silent on PUBLIC (objection P3) |
| "Checked by test_app_sp_grants on a test branch, 2026-10-23. Not read live." | C6 | stale: production run 2026-10-24 (objection P1) |
| "No. The app server holds the token; the browser never gets it." | C7 | traces |
| "Checked by test_token_leak_live in a real browser, 2026-10-23." | C7 | stale: names the local-against-branch test; the deployed runs are newer (objection P2) |
| "Inside Postgres, in the function api.search_insurers. The app server only calls it." | C2, C19 | traces |

Objections from the seat (screen changes wait for Samuel; DECISIONS_PENDING 19):
- P1. The grants source line understates the proof: it says "on a test branch, 2026-10-23", and production was read on 2026-10-24.
- P2. The token source line names `test_token_leak_live`, which ran against the local server and a branch. The deployed proof is `test_deployed_responses_hold_no_secret_by_shape` (HTTP, by shape) and, from session 4, `test_token_leak_on_the_deployed_page` (browser).
- P3. "Which grants does the app's identity hold?" names the direct grants only. A skeptic who runs `has_database_privilege(<sp>, current_database(), 'TEMP')` gets true, through PUBLIC. The line is not an overclaim ("Direct grants:"), and the J1 lint passes it, but the answer is incomplete.
- P4. J13 and J14 above, fixed in tests in this session.

## K. The publish into the public repo leaks, lies or breaks (session 6, written before any file is copied)

Target: `saselvan/lakebase-payer-search`, folder `app/`, branch `app/payer-finder` off `origin/main` `a096af2`, a local worktree (DECISIONS.md 2026-10-24, final home). Samuel pushes and opens the pull request; the agent never does. "Item" is the session 6 item in `SESSION_PLAN.md`.

| # | Attack | Guard (the check that catches it) | Red proof | Item |
|---|---|---|---|---|
| K1 | A finder commit travels: the export carries `.git`, or the branch holds a commit other than one new commit on `a096af2` | export with `git archive` (tree only, no history); `git rev-list a096af2..app/payer-finder` is exactly 1 commit and `git diff --name-status a096af2` shows only `A` lines plus the allowed edits | a second commit or an `M` line outside the allowed files fails the check | 2, 5 |
| K2 | A `runs/` file, `SESSION_PLAN.md`, `REGISTER.md`, `DECISIONS*.md`, `FIRST_PROMPT.md`, `docs/STATE.md`, `diagram/runs/` or `.env` lands in `app/` | the allowlist is a list of paths; a scan of `git ls-files app/` names each forbidden path | a planted `app/runs/x.txt` turns the scan red | 2, 5 |
| K3 | A value from the finder `.env` sits in a public file or the commit message (tenant, hosts, workspace id, client ids) | J9 scan with the exact `.env` values and the shapes, over every tracked file and the commit message of the new branch | a planted value in a scratch copy turns the scan red | 5 |
| K4 | A value sits in an image or video (PNG, MP4, WOFF2), which a text scan cannot read | list every binary under `app/`; Samuel reviews each by eye (J10); a binary with no verdict blocks the push | the list is in the report | 5 |
| K5 | A test fixture holds a real value: a tenant, a host or a JWT copied from a run | the J9 scan covers `app/tests/`; the planted JWT in `test_panel.py` is synthetic and matched by shape only | the scan names the file and kind | 5 |
| K6 | The pin test still reads `sql/deployed/`, so the app's function text can drift from the repo's `sql/20_search_function.sql` | `sql/deployed/` is not exported; the server, `stage_deploy.py` and the tests read `../sql/20_search_function.sql`; a test checks that the file's blob equals the deployed blob `ec46a42` the drawer names | a one-byte drift planted in `sql/20_search_function.sql` turns the test red | 2 |
| K7 | A second copy of the function hides in `app/` (a stale `app/sql/`) and the server reads it first | the server reads one path only; a test fails if any `*.sql` file is tracked under `app/` | a planted `app/sql/20_search_function.sql` turns it red | 2 |
| K8 | App tests that need `.env`, a branch or the deployed app fail in the public repo's default test run | `live` and `deployed` tests are markers that skip without settings; `cd app && uv run pytest` passes on a clean clone with no `.env`; the root `uv run pytest` does not collect `app/tests` | a run with no `.env` is recorded; a planted unmarked live call fails it | 3 |
| K9 | The README sends blog readers to `app/` first, or the app becomes the repo's front door | the README keeps the blog series (`sql/`) first and the demo second; a check reads the README headings in order | a README with the app section first fails the check | 4 |
| K10 | A security line in the public repo says PUBLIC has fewer rights than REGISTER.md lists | the public security doc lists connect, temporary tables, use of schema `public`, 2 platform views, 4 platform functions and 91 extension functions; a test compares the doc's list with the pinned set in `test_panel.py` | removing one item from the doc turns the test red | 4 |
| K11 | A claim date on screen or in a doc is stale after the copy (for example a source line that names a branch run) | J15 test copied into `app/tests`; the public docs name dates only from the catechism rows | the J15 red proof (`runs/2026-10-25/s6/item1-red.log`) | 1, 4 |
| K12 | The public copy names internal paths: a home folder path, the private notes folder, `runs/` evidence that is not public, or the FEVM profile name | a scan of `app/` for these strings; each hit is rewritten or listed for Samuel | a planted home folder path turns the scan red | 5 |
| K13 | The deployed app and the public `app/` differ silently (the evidence page change of item 1 is not deployed) | the report and `app/README.md` say the page in the repo is newer than the deployed snapshot until Samuel deploys | process | 6 |
