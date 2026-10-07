# Search quality: synonyms, exact-name matching, and evaluation

This document explains how the search maintains accuracy when handling abbreviations, misspellings, and hypernyms.

## Synonym rules: replace vs. expand

Synonyms are applied at index time (when search keys are built) and at query time (when BM25is computed). The system uses two kinds:

### Replace: equivalent forms lose the brand
A tokenizer synonym **replaces** the word with a canonical lexeme. Use this for forms of the same name:

- `bcbs` → `blue_cross_blue_shield` (abbreviation)
- `unitedhealth` → `unitedhealth` (spelling variant of `united health`)
- `ga` → `georgia` (state code)

Replacement is correct here: all these forms are the same entity.

### Expand: hypernyms keep the brand
A word that means "is a kind of" another must **expand** (add a wider word to the document) instead of replacing:

- `badgercare` (a brand) → also add `medicaid` (the program it participates in)
- `ambetter` (a brand) → also add `centene` (its parent company)

If `badgercare → medicaid` were a replacement, every CHIP query would look for "medicaid chip", erasing the BadgerCare brand. Instead, expansion keeps "badgercare" in the key and adds "medicaid" alongside it.

**Rule**: A row with kind `program_name`, `brand_family`, or `former_name` whose word is still a live brand name in the payer list will expand. Everything else replaces. This is determined at index build time in `sql/10_search_index.sql` (lines 40–43). Measured effect ([source](../results/exact-name-deepdive-2026-10-06.md#synonym-fix)):

| Approach | Brand in top 10 | MRR |
|---|---|---|
| No synonyms | 85.7 % | 0.925 |
| Expand all "is a kind of" rows | 94.2 % | 0.930 |
| **Expand only live brands** | **98.4 %** | **0.973** |

Synonyms are sourced from `data/seed/synonyms.csv` (hand-checked, 35 rows) and `data/seed/synonyms_generated.csv` (computed by `python -m insurer_search.synonyms`; initials, bcbs+state, joined words, with collision rules).

## Exact-name sweep

A separate test validates that exact payer names always return the payer in the result set. Run:

```bash
python -m insurer_search.search --kind exact_name
```

This generates a query from each distinct payer name in the corpus and counts the names whose payer is not in the top 10. The sweep falsified a candidate optimization that cut the BM25 candidate set from 300 to 100 ([detailed analysis](../results/exact-name-deepdive-2026-10-06.md)). A buggy scorer would score strong trigram matches as 0 if they fell outside the BM25 top-100 (because the old code only scored BM25 for candidates it had already ranked). The fix: score BM25 for every candidate with standalone scoring (free, the index carries corpus statistics).

Measured: **0 exact-name misses** across all 6,291 payers after the fix ([source](../results/exact-name-deepdive-2026-10-06.md), table row 4).

## Quality evaluator

The evaluator generates queries from the payer names themselves to simulate realistic typos, abbreviations, and word variations:

```bash
uv run python -m insurer_search.evaluate --sample 150
```

This generates 899 queries (in default mode; `--sample 150` sends a subset) and scores against the live API:

| Query kind | Query count | Brand@10 |
|---|---|---|
| Exact name | 150 | 99.3 % |
| One character typo | 150 | 98.0 % |
| Brand name with typo | 150 | 99.3 % |
| Initials (e.g., `ibc`, `bcbsga`) | 90 | 98.9 % |
| Joined words (e.g., `bluecross` for `blue cross`) | 119 | 100 % |
| One word dropped | 90 | 91.1 % |
| State first (e.g., `ga bcbs`) | 150 | 100 % |
| **Total: 899 queries** | — | **98.4 %** |

Measurement from [v2 evaluator](../results/quality-fn-v2-synfix.json), 2026-10-06, live API. Exact-name misses: 0 of 6,291 payers. Wider-word queries: 80 of 88 in the top 10 ([results](../results/wider-word-2026-10-07.md)).

Each generated query is sent to the API and the result set is checked: does the brand's top-level payer name appear in the top 10 results? A miss is logged with the query, expected payer, and the actual top 10.

The evaluator also runs all live tests in `tests/test_api_live.py` (must pass before scores are recorded).

## How to improve score

Score can drop if:
1. A new payer brand is added to the corpus without a corresponding synonym (e.g., a spinoff of an existing company). Add a row to `data/seed/synonyms.csv` with the new brand name and its relationship.
2. A common query pattern (e.g., a state abbreviation or acronym) is not in the synonym list. Use `scripts/ops/query_misses.py` to find low-scoring queries and add rows.
3. The expand-vs-replace rule changes. Check the exact-name sweep and re-generate synonyms before committing.

Re-generate synonyms with:
```bash
python -m insurer_search.synonyms > data/seed/synonyms_generated.csv
```

Then re-run the evaluator to confirm the score improves.

## Related

- [Synonym generation method](research/synonym-method.md)
- [Exact-name sweep deep dive](../results/exact-name-deepdive-2026-10-06.md)
- [Mutation testing (search function variants)](../results/mutation-gate.md)
