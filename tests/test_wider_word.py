"""Offline and live tests for wider-word synonym expansion.

Offline tests check the pure function logic with hostile fixtures.
Live tests verify the feature through the real API with a hit-rate floor.
"""

import os

import pytest

from insurer_search.wider_word import SynonymRow, build_wider_word_queries, pick_expand_rows


# ---- Offline tests: pure function logic ----


class TestPickExpandRows:
    """Offline: identify rows that should expand (kind + word in live brands)."""

    def test_program_name_row_with_word_in_payer_name_expands(self):
        """Happy path: program_name row whose word appears in a payer name."""
        rows = [
            SynonymRow(word="badgercare", canonical="medicaid", kind="program_name")
        ]
        names = ["BadgerCare CHIP", "Some Other Plan"]
        result = pick_expand_rows(rows, names)
        assert len(result) == 1
        assert result[0].word == "badgercare"

    def test_only_specific_kinds_expand(self):
        """HOSTILE: only program_name, brand_family, former_name expand.
        Even if state_code word appears as word boundary in a payer name."""
        rows = [
            SynonymRow(word="ga", canonical="georgia", kind="state_code"),
            SynonymRow(word="badgercare", canonical="medicaid", kind="program_name"),
            SynonymRow(word="ambetter", canonical="centene", kind="brand_family"),
        ]
        # Use payer names where "ga" appears as standalone word (e.g., "ga plan")
        names = ["GA Plan", "BadgerCare CHIP", "Ambetter Arizona"]
        result = pick_expand_rows(rows, names)
        words = {r.word for r in result}
        # ga should NOT expand even though it matches " ga " in " ga plan "
        assert words == {"badgercare", "ambetter"}

    def test_word_must_be_whole_word_not_substring(self):
        """HOSTILE: 'ba' should not match 'badgercare' as substring."""
        rows = [SynonymRow(word="ba", canonical="medicaid", kind="program_name")]
        names = ["Badgercare Plan"]
        result = pick_expand_rows(rows, names)
        assert len(result) == 0

    def test_word_at_payer_name_start_requires_boundary(self):
        """HOSTILE: word at payer name start requires word-boundary padding."""
        rows = [
            SynonymRow(word="care", canonical="medicaid", kind="program_name")
        ]
        names = ["Care Plan Group"]
        result = pick_expand_rows(rows, names)
        assert len(result) == 1

    def test_case_insensitive_matching(self):
        """Test: matching should be case insensitive."""
        rows = [
            SynonymRow(word="BADGERCARE", canonical="MEDICAID", kind="program_name")
        ]
        names = ["badgercare chip"]
        result = pick_expand_rows(rows, names)
        assert len(result) == 1


class TestBuildWiderWordQueries:
    """Offline: build queries with state from expand rows."""

    def test_build_queries_with_state_code(self):
        """Happy path: build query as '<canonical> <state>' with set of payer_ids."""
        rows = [
            SynonymRow(word="badgercare", canonical="medicaid", kind="program_name")
        ]
        payer_rows = [
            {"payer_id": "p1", "payer_name": "BadgerCare CHIP", "state": "WI"},
        ]
        result = build_wider_word_queries(rows, payer_rows)
        assert "medicaid wi" in result
        assert result["medicaid wi"] == {"p1"}

    def test_multiple_payers_same_word_different_states(self):
        """Test: same word in different states produces different queries."""
        rows = [
            SynonymRow(word="badgercare", canonical="medicaid", kind="program_name")
        ]
        payer_rows = [
            {"payer_id": "p1", "payer_name": "BadgerCare CHIP", "state": "WI"},
            {"payer_id": "p2", "payer_name": "BadgerCare Family", "state": "IL"},
        ]
        result = build_wider_word_queries(rows, payer_rows)
        assert "medicaid wi" in result
        assert "medicaid il" in result
        assert result["medicaid wi"] == {"p1"}
        assert result["medicaid il"] == {"p2"}

    def test_multiple_payers_same_word_and_state(self):
        """Test: same word and state produces 1 query with multiple payer_ids."""
        rows = [
            SynonymRow(word="ambetter", canonical="centene", kind="brand_family")
        ]
        payer_rows = [
            {"payer_id": "p1", "payer_name": "Ambetter AZ", "state": "AZ"},
            {"payer_id": "p2", "payer_name": "Ambetter Arizona Health", "state": "AZ"},
        ]
        result = build_wider_word_queries(rows, payer_rows)
        assert "centene az" in result
        assert result["centene az"] == {"p1", "p2"}

    def test_skip_rows_with_no_state(self):
        """HOSTILE: rows with None or missing state are skipped."""
        rows = [
            SynonymRow(word="badgercare", canonical="medicaid", kind="program_name")
        ]
        payer_rows = [
            {"payer_id": "p1", "payer_name": "BadgerCare", "state": None},
            {"payer_id": "p2", "payer_name": "BadgerCare WI", "state": "WI"},
        ]
        result = build_wider_word_queries(rows, payer_rows)
        assert "medicaid wi" in result
        assert result["medicaid wi"] == {"p2"}
        assert len(result) == 1

    def test_canonical_with_underscores_becomes_spaces(self):
        """Test: underscores in canonical are replaced with spaces."""
        rows = [
            SynonymRow(
                word="medi_cal",
                canonical="medi_caid",
                kind="program_name",
            )
        ]
        payer_rows = [
            {"payer_id": "p999", "payer_name": "Medi-Cal", "state": "CA"}
        ]
        result = build_wider_word_queries(rows, payer_rows)
        assert "medi caid ca" in result
        assert result["medi caid ca"] == {"p999"}

    def test_dict_input_works(self):
        """Test: function accepts dict-like rows."""
        rows = [
            {
                "word": "ambetter",
                "canonical": "centene",
                "kind": "brand_family",
                "source": "test",
            }
        ]
        payer_rows = [
            {"payer_id": "p456", "payer_name": "Ambetter AZ", "state": "AZ"}
        ]
        result = build_wider_word_queries(rows, payer_rows)
        assert "centene az" in result
        assert result["centene az"] == {"p456"}


# ---- Live tests: API queries ----


@pytest.mark.live
def test_wider_word_queries_hit_matching_payers_at_top_10(client, admin_sql):
    """Live test: queries from expand rows should find matching payers in top 10.

    Asserts hit rate >= 0.90. Measured 80/88 (0.909) on 2026-10-07, the same on a long-lived
    project and on a new one (results/wider-word-2026-10-07.md). The 8 misses are parent-company
    queries ("molina az" for a Magellan product): the parent's own products rank first.
    A hit = any expected payer_id for a query is in the top 10 results.
    """
    if not os.environ.get("DATA_API_BASE"):
        pytest.skip("live: DATA_API_BASE not set")

    # Load synonym seed table
    synonym_rows = admin_sql(
        """
        SELECT word, canonical, kind, source FROM search_idx.synonym_seed
        WHERE kind IN ('program_name', 'brand_family', 'former_name')
    """
    )
    rows = [
        SynonymRow(word=r[0], canonical=r[1], kind=r[2], source=r[3])
        for r in synonym_rows
    ]

    # Load payer names for expand detection
    payer_names_result = admin_sql(
        "SELECT DISTINCT payer_name FROM payer_serving.insurer_alias"
    )
    payer_names = [r[0] for r in payer_names_result]

    # Load payer rows with state (skip NULL states)
    payer_rows_result = admin_sql(
        "SELECT DISTINCT payer_id, payer_name, state FROM payer_serving.insurer_alias WHERE state IS NOT NULL"
    )
    payer_rows = [
        {"payer_id": r[0], "payer_name": r[1], "state": r[2]}
        for r in payer_rows_result
    ]

    # Pick expand rows and build queries
    expand = pick_expand_rows(rows, payer_names)
    assert len(expand) > 0, "No expand rows found"

    queries = build_wider_word_queries(expand, payer_rows)
    assert len(queries) > 0, "No queries generated"

    # Run queries through the API
    tenant = "wider-word-test"
    hits = 0

    for query, expected_ids in queries.items():
        results = client.search(query, tenant, page=1)
        result_ids = {r["payer_id"] for r in results}

        if expected_ids & result_ids:  # Any intersection = hit
            hits += 1

    hit_rate = hits / len(queries) if queries else 0.0
    print(f"\nWider-word test: {hits}/{len(queries)} queries hit ({hit_rate:.2%})")

    assert hit_rate >= 0.90, f"Hit rate {hit_rate:.2%} below floor 0.90"
