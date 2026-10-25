"""H1, K6, K7: the definition the lens shows is the repo's own sql/20_search_function.sql, and that file is the blob
production runs (claim C15). One copy of the function exists: the app keeps none of its own.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from server.main import SEARCH_SQL, git_blob_hash, load_definition

# Claim C15 and DECISIONS_PENDING entry 9: production runs this commit's file. Typed here, not imported, so a change
# to the server's constants is caught.
DEPLOYED_COMMIT = "b60a1512fd2f43332960ed543021b9e3f20d4114"
DEPLOYED_BLOB = "ec46a4228f567064654b12f70f249c128ab75f3c"

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent


def test_the_app_reads_the_repo_function_file():
    assert SEARCH_SQL.resolve() == (REPO / "sql/20_search_function.sql").resolve()


def test_the_repo_function_file_is_the_deployed_blob():
    """The drawer names commit b60a151. If sql/20 changes, deploy it, check it, then move this pin."""
    assert git_blob_hash(SEARCH_SQL.read_bytes()) == DEPLOYED_BLOB


def test_the_lens_source_names_the_deployed_commit_and_the_file_blob():
    assert load_definition()["source"] == {"repo": "https://github.com/saselvan/lakebase-payer-search",
                                           "path": "sql/20_search_function.sql",
                                           "commit": DEPLOYED_COMMIT, "blob": DEPLOYED_BLOB}


def test_a_drifted_function_file_stops_start_up(tmp_path):
    drifted = tmp_path / "20_search_function.sql"
    drifted.write_bytes(SEARCH_SQL.read_bytes().replace(b"current_user;", b"current_user; ", 1))
    with pytest.raises(RuntimeError, match="not the deployed blob"):
        load_definition(drifted)


def _tracked(*args: str) -> list[str]:
    p = subprocess.run(["git", "-C", str(REPO), "ls-files", *args], capture_output=True, text=True)
    if p.returncode:
        pytest.skip("not a git checkout")
    return p.stdout.splitlines()


def test_no_second_copy_of_the_function_in_the_app():
    assert [f for f in _tracked("app/") if f.endswith(".sql") or "/sql/" in f] == []


def test_deployed_commit_holds_the_blob():
    p = subprocess.run(["git", "-C", str(REPO), "rev-parse", f"{DEPLOYED_COMMIT}:sql/20_search_function.sql"],
                       capture_output=True, text=True)
    if p.returncode:
        pytest.skip("the deployed commit is not in this clone (shallow clone)")
    assert p.stdout.strip() == DEPLOYED_BLOB
