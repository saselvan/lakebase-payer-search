"""Offline: the single write-guard decision (tests/conftest.py throwaway_branch_refusal). No database."""
import pytest

from conftest import throwaway_branch_refusal


@pytest.mark.parametrize("branch", [None, "", "production", "main", "t", "t-", "tx-abc", "T-abc", "t- x", " t-abc", "t-a b", "t-a\tb"])
def test_refuses(branch):
    assert throwaway_branch_refusal(branch)


@pytest.mark.parametrize("branch", ["t-h5-xxx", "t-1", "t-a-b"])
def test_allows(branch):
    assert throwaway_branch_refusal(branch) is None
