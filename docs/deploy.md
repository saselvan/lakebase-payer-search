# Deploy

From an empty workspace to a working search API in two commands: a bundle for the Lakebase
project, then one setup script for everything that is not a bundle resource.

## What you need
- A Databricks workspace with Lakebase (Autoscaling) and Unity Catalog. Databricks CLI 1.19 or later.
- `uv`, `psql` (PostgreSQL 16+ client).
- A catalog you can create schemas in, and a SQL warehouse (for the Delta load).
- A service principal for the calling app (OAuth client id + secret). The app calls the API as it.
- Copy `.env.example` to `.env` and fill it in. Leave `DATA_API_BASE` empty until step 3.

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
