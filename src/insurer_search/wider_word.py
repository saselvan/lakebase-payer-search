"""Wider-word synonym expansion for search: turn generic words into live brand queries.

A synonym row with kind 'program_name', 'brand_family', or 'former_name' whose word is
a live brand in payer names expands (e.g. badgercare -> medicaid adds "medicaid" to documents
for products that mention "badgercare"). This module picks those expand rows and builds queries
to test the feature.

Usage:
    expand_rows = pick_expand_rows(synonym_rows, payer_names)
    queries = build_wider_word_queries(expand_rows, payer_names, payer_id_for_name)
"""

import re
from dataclasses import dataclass


@dataclass
class SynonymRow:
    """One row from the synonym seed table."""
    word: str
    canonical: str
    kind: str | None = None
    source: str | None = None


def pick_expand_rows(
    synonym_rows: list[SynonymRow] | list[dict],
    payer_names: list[str],
) -> list[SynonymRow]:
    """Pick synonym rows that should expand (add a word to documents).

    A row expands if:
    - kind is 'program_name', 'brand_family', or 'former_name'
    - word appears as a word boundary in at least one payer name (LIKE '% word %' after normalization)

    Args:
        synonym_rows: list of SynonymRow or dict-like objects
        payer_names: list of payer names (not normalized)

    Returns:
        list of SynonymRow objects that meet the expand criteria
    """
    # Normalize payer names once: lowercase, replace non-alnum with space, add word boundaries
    normalized_names = {
        " " + re.sub(r"[^a-z0-9]+", " ", name.lower()).strip() + " "
        for name in payer_names
    }

    expand = []
    for row in synonym_rows:
        # Convert dict to SynonymRow if needed
        if isinstance(row, dict):
            row = SynonymRow(
                word=row["word"],
                canonical=row["canonical"],
                kind=row.get("kind"),
                source=row.get("source"),
            )

        # Check if this row should expand
        if row.kind not in ("program_name", "brand_family", "former_name"):
            continue

        # Normalize the word: replace underscores with spaces, lowercase
        normalized_word = " " + row.word.replace("_", " ").lower() + " "

        # Check if word appears as word boundary in any payer name
        for name in normalized_names:
            if normalized_word in name:
                expand.append(row)
                break

    return expand


def build_wider_word_queries(
    expand_rows: list[SynonymRow] | list[dict],
    payer_rows: list[dict],
) -> dict[str, set[str]]:
    """Build wider-word queries from expand rows and payer data.

    For each expand row, find payers (with state) whose names contain the word.
    Returns {query: set(payer_ids)} where query = "<canonical with spaces> <state lower>".
    Skips rows with no state.

    Args:
        expand_rows: list of SynonymRow or dict objects (from pick_expand_rows)
        payer_rows: list of dicts with keys: payer_id, payer_name, state

    Returns:
        dict mapping query string to set of payer_ids.
    """
    queries: dict[str, set[str]] = {}

    for row in expand_rows:
        if isinstance(row, dict):
            row = SynonymRow(
                word=row["word"],
                canonical=row["canonical"],
                kind=row.get("kind"),
                source=row.get("source"),
            )

        # Normalize the word for matching payer names
        normalized_word = " " + row.word.replace("_", " ").lower() + " "
        wider_word = row.canonical.replace("_", " ")

        # Find payers that contain this word
        for payer_row in payer_rows:
            payer_name = payer_row["payer_name"]
            payer_id = payer_row["payer_id"]
            state = payer_row.get("state")

            # Skip rows with no state
            if not state:
                continue

            normalized_name = (
                " " + re.sub(r"[^a-z0-9]+", " ", payer_name.lower()).strip() + " "
            )
            if normalized_word in normalized_name:
                # Build query: "<wider word> <state lower>"
                query = f"{wider_word} {state.lower()}"
                if query not in queries:
                    queries[query] = set()
                queries[query].add(payer_id)

    return queries
