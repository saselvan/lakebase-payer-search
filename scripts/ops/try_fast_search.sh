#!/usr/bin/env bash
# Try the proposed faster search function on the live endpoint, measure it, then put the original back.
# Revert by hand at any time: scripts/psql.sh -q -f sql/20_search_function.sql
set -uo pipefail
cd "$(dirname "$0")/../.."
trap 'echo "== put the original back"; scripts/psql.sh -q -f sql/20_search_function.sql' EXIT
echo "== install fast function"; scripts/psql.sh -q -f sql/proposed/20_search_function_fast.sql || exit 1
echo "== quality (fast); baseline v4: brand@10 0.982, product@10 0.844, MRR 0.970"
uv run python - <<'PY' | tail -15
import runpy, sys; sys.path[:0] = ["src", "scripts"]
from uc_sql import load_env; load_env()          # evaluate.py reads settings from the environment
sys.argv = ["evaluate", "--sample", "150", "--out", "results/quality-fast.json"]
runpy.run_module("insurer_search.evaluate", run_name="__main__")
PY
echo "== live tests (fast)"
uv run pytest -q tests/test_api_live.py 2>&1 | tail -5
echo "== aetna rows across 5 pages (fast)"
uv run python - <<'PY'
import sys; sys.path[:0] = ["src", "scripts"]
from uc_sql import load_env; load_env()
from insurer_search.client import InsurerSearchClient
c = InsurerSearchClient.from_env()
print(sum(len(c.search("aetna", "fast-check", page=p)) for p in range(1, 6)), "of 50")
PY
