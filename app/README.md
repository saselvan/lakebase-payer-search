# Payer finder: one Lakebase search, step by step

A small web app on Databricks Apps that runs the repo's payer search and shows how one search ran: the request the browser sent, the call the app server made to the Lakebase Data API with its own service principal's token, the Postgres role the call switched into, and the text of the SQL function that ranked the rows. The browser talks only to the app server and never receives a token.

The search itself is the repo's own [`sql/20_search_function.sql`](../sql/20_search_function.sql). The app keeps no copy of it: the server reads that file at start-up and shows it in the drawer.

## What is in this folder

| Path | What it is |
|---|---|
| `server/` | FastAPI app server: `GET /`, `GET /static/*` and `POST /api/search`. Contract: [`DATA_CONTRACT.md`](../DATA_CONTRACT.md), section 4 |
| `static/` | The page: plain HTML, CSS and JavaScript, no build step, strict Content Security Policy |
| `diagram/` | The drawer's motion diagram (a HyperFrames composition) and its checks; `static/diagram.*` are byte copies |
| `tests/` | Offline tests (default), and `live` and `deployed` tests that need settings |
| `scripts/` | `stage_deploy.py` builds the upload copy; `mutate.py` shows each test red; `run_local.sh` runs against a test branch |
| `docs/` | `CATECHISM.md` (every claim on the page, its grade and its evidence), `OBJECTIONS.md` (the attacks and their checks), `BRIEF.md` |
| [`SECURITY.md`](SECURITY.md) | The rights the app's identity holds, including the standard PUBLIC defaults |

## Run the tests

```bash
cd app
uv sync
uv run playwright install chromium
uv run pytest
```

The default run is offline: no `.env`, no workspace and no network. The checks that look for deployment values in tracked files skip when there is no `.env`. `uv run pytest -m live` needs a test branch and the app's service principal; `uv run pytest -m deployed` needs the running app and a CLI login. Both read their settings from `app/.env`, which git ignores.

## The function pin

The drawer names the commit production runs: `b60a151`, where `sql/20_search_function.sql` is blob `ec46a42`. The server refuses to start if the file is a different blob, and `tests/test_deployed_sql.py` fails. To change the search, deploy the new function, check it on production, then move both values in `server/main.py` and the test.

## Deploy

`uv run python scripts/stage_deploy.py` writes an upload copy to `/tmp/lakebase-payer-finder-deploy`: the server, the page, `app.yaml` with production's Data API base read live, and `sql/20_search_function.sql`. It never copies `.env`. The owner runs `databricks apps deploy` on that folder.

## Status

The page in this folder is newer than the deployed app: the evidence-panel text of 2026-10-25 (source lines and the PUBLIC defaults clause) is not deployed yet. All data is synthetic or public: public payer brand names, synthetic IDs and plans, no patient data.
