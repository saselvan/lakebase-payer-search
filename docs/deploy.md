# Deploy

From an empty workspace to a working search API in two commands: a bundle for the Lakebase
project, then one setup script for everything that is not a bundle resource.

## What you need

Tools on your laptop: Databricks CLI 1.19 or later, [`uv`](https://docs.astral.sh/uv/), and
`psql` (PostgreSQL 16+ client).

Ask for these before you start. Most of them need a workspace admin.

| You need | Who can give it | How to check |
|---|---|---|
| A workspace with Lakebase (Autoscaling) and Unity Catalog | workspace admin | the left menu has **Lakebase** |
| Permission to create a Lakebase project | workspace admin | the **Lakebase** page shows **Create project** |
| `USE CATALOG` + `CREATE SCHEMA` on one catalog | catalog owner or metastore admin | `databricks schemas list <catalog> -p <profile>` works |
| `CAN USE` on a SQL warehouse | warehouse owner or admin | the warehouse shows in **SQL Warehouses** |
| A service principal with an OAuth secret, for the calling app | workspace admin | **Settings > Identity and access > Service principals** |
| A CLI profile logged in as you | you | `databricks current-user me -p <profile>` |

Then copy `.env.example` to `.env` and fill it in ([values](#env-values)). Leave `DATA_API_BASE`
empty until step 3.

<a id="env-values"></a>
## .env values

| Name | What it is | Example | Where to find it |
|---|---|---|---|
| `DATABRICKS_PROFILE` | the CLI profile that runs setup (you) | `my-profile` | `databricks auth login --host https://your-workspace-host --profile my-profile` |
| `WORKSPACE_HOST` | the workspace URL | `https://dbc-1234abcd-5678.cloud.databricks.com` | the browser address bar |
| `LAKEBASE_PROJECT` | the Lakebase project id | `lakebase-payer-search` | must equal the bundle variable `lakebase_project` |
| `LAKEBASE_BRANCH`, `LAKEBASE_ENDPOINT`, `PG_DATABASE` | made with the project | `production`, `primary`, `databricks_postgres` | keep the defaults |
| `UC_CATALOG` | a catalog where you can create schemas | `main` | **Catalog** in the left menu |
| `UC_SCHEMA` | the schema for the source Delta table; setup creates it | `payer_search` | your choice. Not `payer_serving`: the synced table uses that name |
| `SQL_WAREHOUSE_ID` | the warehouse that loads the Delta table | `1a2b3c4d5e6f7a8b` | **SQL Warehouses** > the warehouse > **Connection details** > the id after `/warehouses/` |
| `SP_APPLICATION_ID` | the calling app's service principal | a UUID | **Settings > Identity and access > Service principals** > **Application ID** |
| `SP_CLIENT_SECRET` | its OAuth secret | (secret) | same page > **Secrets** > **Generate secret**. It shows once |
| `DATA_API_BASE` | the REST base URL | `https://<endpoint-host>/api/2.0/workspace/<id>/rest/databricks_postgres` | printed by `uv run python scripts/enable_data_api.py` |

Do not put spaces, quotes, `<` or `>` in a value: `setup.sh` reads `.env` with bash.

## 1. Project and compute: the bundle
```bash
databricks bundle deploy -p <profile> --var lakebase_project=<project-id>
```
[`databricks.yml`](../databricks.yml) declares the Lakebase project (Postgres 17) and its
read-write compute: 2–16 CU, no scale to zero. Change the size with `--var pg_min_cu=` /
`--var pg_max_cu=` (the range can be at most 16 CU wide). Do not go below 2 CU: 1 CU returned
HTTP 500 "out of memory" under load ([results](../results/loadtest-cu32-2026-10-06.md)).

## 2. Everything else: `scripts/setup.sh`
```bash
scripts/setup.sh            # ROWS=200000 scripts/setup.sh for a small test corpus
```
Every step can run again safely.

| Step | What | How |
|---:|---|---|
| 0 | Turn on Lakebase Search for the project | `POST /api/2.0/postgres/projects/<id>/search-extensions` (see below) |
| 1–2 | Make the synthetic payer aliases and load the Delta table | Python + SQL warehouse |
| 3 | Sync the Delta table into Postgres schema `payer_serving` | synced table, snapshot mode |
| 4 | Search index | [`sql/10_search_index.sql`](../sql/10_search_index.sql) |
| 5 | Search function | [`sql/20_search_function.sql`](../sql/20_search_function.sql) |
| 6 | Data API, schema `api` only | [`scripts/enable_data_api.py`](../scripts/enable_data_api.py) |
| 7 | Caller grants: EXECUTE on one function, nothing else | [`sql/40_caller_grants.sql`](../sql/40_caller_grants.sql) |

**Step 0, Lakebase Search.** A new project does not load `lakebase_text` (it is a preload
library), so `CREATE EXTENSION lakebase_text` fails with "must be loaded via
shared_preload_libraries". The UI button is Project settings > Lakebase Search > "Enable Lakebase
Search". `setup.sh` makes the same call through the REST API:
```bash
databricks api post /api/2.0/postgres/projects/<project-id>/search-extensions --json '{}'
```
It returns an operation to poll. The first call restarts the project's computes (open
connections drop), so it runs before anything else. It cannot be turned off. A second call returns
done at once. This call is not in the CLI command list; we found it from the UI and checked it on
new projects (2026-10-07).

## 3. Point the client at the API
```bash
uv run python scripts/enable_data_api.py    # prints the Data API URL
```
Put that URL in `.env` as `DATA_API_BASE`, then:
```bash
uv run pytest -q -m live
```

## Prove it from zero
[`scripts/ci_proof.sh`](../scripts/ci_proof.sh) does all of the above on a new project
`lps-ci-<random>`, runs every test, and then deletes the project, synced table, pipeline and
schemas. `KEEP=1` leaves them up to debug.

Teardown order matters: delete the project first, then the synced table. Deleting the synced table
first fails while the search views depend on it, and deleting only the project leaves the UC synced
table and its sync pipeline behind.

## Remove
```bash
databricks bundle destroy -p <profile> --var lakebase_project=<project-id>
databricks postgres delete-synced-table synced_tables/<catalog>.payer_serving.insurer_alias
```
Then drop the UC schemas you made.
