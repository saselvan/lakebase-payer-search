"""Offline tests for the shared top-hit list (data/test_top_hit_cases.json).

The live tests and the flywheel gate (ticket #8) both read it through load_top_hits().
Run offline (no database): uv run pytest tests/test_top_hit_cases_offline.py -q
"""
import json

import pytest

from insurer_search.top_hits import load_top_hits

# Pins the move from the inline list at commit 9a94280: same pairs, same order, same count.
INLINE_AT_9A94280 = [
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
]


def test_the_file_holds_exactly_the_inline_list_it_replaced():
    assert load_top_hits() == INLINE_AT_9A94280


def _write(tmp_path, rows):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(rows))
    return p


GOOD = {"query": "ma blue cross", "expected_brand": "Blue Cross Blue Shield of Massachusetts"}


# Hostile rows: each would let a lazy loader skip a case or pass a case for nothing.
@pytest.mark.parametrize("bad", [
    {"query": "uhc", "expected_brand": ""},              # empty brand matches every payer name
    {"query": "uhc", "expected_brand": "   "},           # whitespace brand
    {"query": "uhc"},                                    # missing brand
    {"query": "", "expected_brand": "UnitedHealthcare"},  # empty query
    {"query": "uhc", "expected_brand": 7},               # brand of another type
    {"query": "aetna ", "expected_brand": "Aetna"},      # outer space
])
def test_a_bad_row_fails_the_load_with_its_row_number(tmp_path, bad):
    with pytest.raises(ValueError, match=r"row 1"):
        load_top_hits(_write(tmp_path, [GOOD, bad]))


@pytest.mark.parametrize("dup", ["ma blue cross", "MA Blue Cross", "ma  blue   cross"])
def test_a_duplicate_query_fails_even_if_case_or_spacing_differs(tmp_path, dup):
    with pytest.raises(ValueError, match="duplicate"):
        load_top_hits(_write(tmp_path, [GOOD, {"query": dup, "expected_brand": "Aetna"}]))


@pytest.mark.parametrize("content", ["[]", "{}", "not json"])
def test_an_empty_or_broken_file_fails_instead_of_collecting_nothing(tmp_path, content):
    p = tmp_path / "cases.json"
    p.write_text(content)
    with pytest.raises(ValueError):
        load_top_hits(p)
