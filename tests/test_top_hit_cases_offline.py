"""Offline tests for top-hit test cases data file.

These tests verify the shared top-hit list (data/test_top_hit_cases.json) that is read by
test_api_live.py and will be read by the flywheel gate (ticket #8).

Run offline (no database): uv run pytest tests/test_top_hit_cases_offline.py -q
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "test_top_hit_cases.json"


def load_cases():
    """Load and validate the top-hit cases from the data file."""
    with open(DATA_FILE) as f:
        cases = json.load(f)

    # Validate structure and required fields
    for idx, case in enumerate(cases):
        if "query" not in case:
            raise ValueError(f"Row {idx}: missing 'query' field")
        if "expected_brand" not in case:
            raise ValueError(f"Row {idx}: missing 'expected_brand' field")

    return cases


def test_data_file_exists():
    """The data file must exist and be readable."""
    assert DATA_FILE.exists(), f"Data file not found: {DATA_FILE}"


def test_data_file_is_valid_json():
    """The data file must be valid JSON."""
    with open(DATA_FILE) as f:
        json.load(f)  # Will raise if invalid


def test_all_cases_have_required_fields():
    """Each case must have 'query' and 'expected_brand'."""
    cases = load_cases()
    assert len(cases) > 0, "No cases loaded"

    for idx, case in enumerate(cases):
        assert isinstance(case, dict), f"Row {idx}: not a dict"
        assert "query" in case, f"Row {idx}: missing 'query'"
        assert "expected_brand" in case, f"Row {idx}: missing 'expected_brand'"
        assert isinstance(case["query"], str), f"Row {idx}: 'query' is not a string"
        assert isinstance(case["expected_brand"], str), f"Row {idx}: 'expected_brand' is not a string"


def test_no_empty_or_whitespace_expected_brand():
    """expected_brand must never be empty or whitespace-only."""
    cases = load_cases()

    for idx, case in enumerate(cases):
        brand = case["expected_brand"]
        assert brand, f"Row {idx}: expected_brand is empty"
        assert brand.strip(), f"Row {idx}: expected_brand is whitespace-only"
        assert brand == brand.strip(), f"Row {idx}: expected_brand has leading/trailing whitespace"


def test_no_duplicate_queries():
    """Queries must be unique (case-sensitive)."""
    cases = load_cases()
    queries = [case["query"] for case in cases]

    # Check for exact duplicates
    seen = set()
    for idx, query in enumerate(queries):
        assert query not in seen, f"Row {idx}: duplicate query {query!r}"
        seen.add(query)


def test_parity_with_inline_list():
    """The file must contain exactly the same (query, expected_brand) pairs as the inline list.

    This test pins the move: if the file is changed without updating the inline list
    (or vice versa), this test will fail.
    """
    cases = load_cases()

    # Expected pairs from test_api_live.py lines 29-41 (baseline at commit 9a94280)
    EXPECTED_PAIRS = {
        ("bcbs georgia", "Blue Cross and Blue Shield of Georgia"),
        ("ga bcbs", "Blue Cross and Blue Shield of Georgia"),
        ("blue cross blue shield of georgia", "Blue Cross and Blue Shield of Georgia"),
        ("united helth", "UnitedHealthcare"),
        ("unitedhealth care", "UnitedHealthcare"),
        ("uhc", "UnitedHealthcare"),
        ("medicad", "Medicaid"),
        ("masshealth", "MassHealth"),
        ("ma blue cross", "Blue Cross Blue Shield of Massachusetts"),
        ("aetna", "Aetna"),
        ("tricare prime", "TRICARE Prime"),
    }

    # Check count first
    assert len(cases) == 11, f"Expected 11 cases, got {len(cases)}"

    # Check that all pairs exist in the file
    file_pairs = {(case["query"], case["expected_brand"]) for case in cases}
    assert file_pairs == EXPECTED_PAIRS, (
        f"Mismatch in (query, expected_brand) pairs.\n"
        f"Expected: {EXPECTED_PAIRS}\n"
        f"Got: {file_pairs}\n"
        f"Missing: {EXPECTED_PAIRS - file_pairs}\n"
        f"Extra: {file_pairs - EXPECTED_PAIRS}"
    )
