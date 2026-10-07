# CPU tuning, 2026-10-06: smaller candidate sets (PROPOSED, NOT APPLIED)

`sql/proposed/20_search_function_fast.sql` = `sql/20_search_function.sql` with 3 numbers changed:
trigram candidates 300 -> 100, BM25 candidates 300 -> 100, "did you mean" words 10 -> 3.

| | Original | Fast |
|---|---:|---:|
| Server time per search (branch, mixed queries) | 17.5 ms | 11.6 ms |
| Estimated ceiling at 2–16 CU | ~235 rps (measured) | ~350 rps (estimated, not load tested) |
| brand@10 (899 generated queries) | 0.982 | 0.980 |
| product@10 | 0.844 | 0.840 |
| MRR | 0.970 | 0.969 |
| Live tests (`tests/test_api_live.py`) | 38/38 | 38/38 |
| `aetna`, results over 5 pages | 50/50 | 50/50 |

**Decision: not applied.** The fast version adds 2 misses, and one is an exact name:
`badgercare chip` no longer finds BadgerCare CHIP (and `badgecare chip` misses with it). Many products
end in "CHIP"; with 100 candidates the right key is pushed out of the set. An exact-name miss is the
worst visible failure, so the original stays. Follow-up: make exact and near-exact name matches always
part of the candidate set, then re-measure.

Method: `scripts/ops/try_fast_search.sh` (install fast, evaluate, live tests, restore original). The
baseline was re-run the same night with the same evaluator: `results/quality-original-recheck.json`.
