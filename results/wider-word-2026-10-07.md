# Wider-word queries, 2026-10-07

**Result: 80 of 88 queries find the right product in the top 10 (0.909).** Same result on a
long-lived project (3M alias rows) and on a new project built from zero by `scripts/setup.sh`
(200,000 rows). Same API, same SQL.

## What is tested
A synonym row of kind `program_name`, `brand_family` or `former_name` whose word is still a live
brand in the payer names **expands**: the product keeps its brand and gets the wider word added
(`sql/10_search_index.sql`, step 1b). 18 rows expand. A query is the wider word plus a state code
("medicaid wi", "centene ak"). It is a hit if any product with that brand in that state is in
the top 10. Code: `src/insurer_search/wider_word.py`; test:
`tests/test_wider_word.py::test_wider_word_queries_hit_matching_payers_at_top_10` (floor 0.90).

## The 8 misses
| Query | Expected brand | Products expected |
|---|---|---:|
| centene va | wellcare | 6 |
| elevance health oh / ok / pa / wi | amerigroup | 3 each |
| molina az / ma / va | magellan | 3 each |

All 8 are parent-company queries. The parent's own products ("Molina ... AZ") fill the top 10
first. We think that order is right: a user who types the parent name wants the parent first.

## Why the 2026-10-06 number was 233 of 241
That A/B ([exact-name-deepdive-2026-10-06.md](exact-name-deepdive-2026-10-06.md)) built its
queries before 3 rows (`cigna_healthcare`, `humana_medicare`, `anthem_medicare`) changed to kind
`product_line`, which is an equivalent, not a wider word. Those 3 rows gave 765 of its 1,194
(query, product) pairs. With the shipped rule, the set is the 88 queries above.
