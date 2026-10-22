"""The upgrade guard in sql/10_search_index.sql: a fresh install works, an old index view is never left behind.

Live, and run ONLY on a throwaway branch (BRANCH=t-...). It drops and rebuilds search_idx.alias_key, so it
refuses to run on anything else. Run: BRANCH=t-xxx uv run pytest tests/test_index_upgrade_live.py
Tests run in file order and each sets up its own state: (a) no alias_key, (b)/(c) an old alias_key without
alias_types, (d) an up-to-date one.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX_SQL = ROOT / "sql" / "10_search_index.sql"
PSQL = str(ROOT / "scripts" / "psql.sh")

pytestmark = pytest.mark.live

# the guard: from the to_regclass probe through the two closing \endif lines (before the rebuild drops)
GUARD = re.search(r"SELECT to_regclass\(.*?\n\\endif\n\\endif\n", INDEX_SQL.read_text(), re.S)

HAS_TYPES = ("SELECT count(*) FROM pg_attribute WHERE attrelid = to_regclass('search_idx.alias_key') "
             "AND attname = 'alias_types' AND NOT attisdropped")
OLD_VIEW = ("DROP MATERIALIZED VIEW IF EXISTS search_idx.alias_key CASCADE;"
            "DROP MATERIALIZED VIEW IF EXISTS search_idx.vocab;"
            "CREATE MATERIALIZED VIEW search_idx.alias_key AS SELECT 1 AS key_id, 'x'::text AS key_norm;")


def _psql(branch, *args):
    return subprocess.run([PSQL, "-q", *args], capture_output=True, text=True, env={**os.environ, "BRANCH": branch})


def _sql(branch, sql):
    r = _psql(branch, "-c", sql)
    assert r.returncode == 0, r.stderr
    return r


def _count(branch):
    r = subprocess.run([PSQL, "-At", "-c", HAS_TYPES], capture_output=True, text=True,
                       env={**os.environ, "BRANCH": branch})
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip())


def _run_guard(branch, *flags):
    """Run only the guard, then print what it decided."""
    assert GUARD, "guard block not found in sql/10_search_index.sql"
    with tempfile.NamedTemporaryFile("w", suffix=".sql") as f:
        f.write(GUARD.group(0) + "\\echo KEYS_OUTDATED=:keys_outdated\n")
        f.flush()
        return _psql(branch, *flags, "-f", f.name)


def test_a_fresh_install_does_not_error(throwaway_branch):
    """No alias_key at all: the guard must not resolve the missing view, and keys_outdated is f (never NULL)."""
    _sql(throwaway_branch, "DROP MATERIALIZED VIEW IF EXISTS search_idx.alias_key CASCADE;"
                           "DROP MATERIALIZED VIEW IF EXISTS search_idx.vocab;")
    r = _run_guard(throwaway_branch)
    assert r.returncode == 0, r.stderr
    assert "KEYS_OUTDATED=f" in r.stdout


def test_b_old_view_without_rebuild_stops(throwaway_branch):
    _sql(throwaway_branch, OLD_VIEW)
    r = _psql(throwaway_branch, "-f", str(INDEX_SQL))
    assert r.returncode != 0
    assert "rebuild=1" in r.stdout + r.stderr
    assert _count(throwaway_branch) == 0, "the old view must be left as it was"


def test_c_old_view_with_rebuild_is_rebuilt(throwaway_branch):
    _sql(throwaway_branch, OLD_VIEW)
    r = _psql(throwaway_branch, "-v", "rebuild=1", "-f", str(INDEX_SQL))
    assert r.returncode == 0, r.stderr
    assert _count(throwaway_branch) == 1


def test_d_up_to_date_view_passes_the_guard(throwaway_branch):
    assert _count(throwaway_branch) == 1
    r = _run_guard(throwaway_branch)
    assert r.returncode == 0, r.stderr
    assert "KEYS_OUTDATED=f" in r.stdout
