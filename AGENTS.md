# AGENTS.md

Guide for coding agents (Claude Code, Cursor, Codex, Copilot) working in this repo.
What the repo is: [llms.txt](llms.txt). Field names the API really returns: [DATA_CONTRACT.md](DATA_CONTRACT.md).

## Commands
```bash
uv sync                                  # install
uv run pytest -q -m 'not live'           # offline tests, no workspace needed (must pass before any commit)
uv run pytest -q -m live                 # live API tests; needs a filled .env and a deployed project
ENV_FILE=.env scripts/ci_proof.sh        # full proof: new project, deploy, all tests, verified teardown (~5 min)
```
Setup in a workspace: `cp .env.example .env`, fill it in ([docs/deploy.md](docs/deploy.md#env-values)), then
`databricks bundle deploy -p <profile>` and `scripts/setup.sh`.

## Where things live
- Ranking and search behaviour: `sql/20_search_function.sql`. Index and synonyms: `sql/10_search_index.sql`.
- Caller permissions: `sql/40_caller_grants.sql`. The caller must keep EXECUTE on one function and nothing else.
- Synonyms: `data/seed/synonyms.csv` (hand-checked) and `src/insurer_search/synonyms.py` (generated).
- Client behaviour (retries, token refresh): `src/insurer_search/client.py`, tested in `tests/test_client.py`.

## Rules
- Never commit `.env`, tokens, secrets, workspace hosts or workspace IDs. The data is synthetic; keep it that way.
- Tests assert behaviour (response content, row counts, refused access), not status codes alone.
- A change to ranking or synonyms needs the evaluator before and after:
  `uv run python -m insurer_search.evaluate --sample 150`. Do not drop below the numbers in `results/`.
- Every number in the docs links to a file in `results/`. Add the result file when you add a number.
- `setup.sh` steps must stay idempotent (safe to run again).
- Do not change the Lakebase compute range while a load test runs: a range change drops open connections.
- Teardown order: delete the project, then the synced table, then the UC schemas. The other order fails or
  leaves an orphan sync pipeline.
