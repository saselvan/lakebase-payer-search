# Exact-name deep dive, 2026-10-06

## The miss
With 100 candidates in place of 300 (`sql/proposed/20_search_function_fast.sql`), `badgercare chip` no
longer returned BadgerCare CHIP in the top 10.

## Mechanism (traced read-only on production)
1. **The synonym list erases the brand word for BM25.** `badgercare -> medicaid` and
   `chip -> childrens_health_insurance_program`, so BM25 searches "medicaid chip". Every CHIP product
   gets the same BM25 score (7.932); BadgerCare CHIP is #103 in that tie.
2. **A candidate got a BM25 score only if it was also in the BM25 top-k.** The key `badgercare chip`
   is #1 by trigram (word similarity 1.0), but at 100 it was not in the BM25 top-100, so its BM25 part
   was 0: score 1.10 against 1.67 for Soonercare CHIP. At 300 it was in the BM25 list and won (2.10).
   The original function has the same bug; 300 only hid it.

## Fix (v2, `sql/proposed/20_search_function_v2.sql`)
Score BM25 for **every** candidate with standalone `<@>` scoring (the index still supplies corpus
statistics; documented on the lakebase_text page), and keep 100 trigram + 100 BM25 candidates.
Standalone scoring of 100 rows cost ~0 ms; the trigram KNN and the BM25 top-k are the expensive parts.

## Results (branch of production, 2–16 CU)

| Version | Exact-name misses (all 6,291 payers) | ms/search p50 / p95 (exact sweep) | brand@10 | product@10 | MRR | Live tests |
|---|---:|---:|---:|---:|---:|---:|
| Original (300/300) | 0 | 17.5 / 29.6 | 0.982 | 0.844 | 0.970 | 38/38 |
| Fast (100/100, no fix) | **1** (BadgerCare CHIP) | 7.1 / 13.7 | 0.980 | 0.840 | 0.969 | 38/38 |
| v2 with BM25 300 | 0 | 15.6 / 26.3 | | | | |
| **v2 (100/100 + fix)** | **0** | **9.8 / 16.5** | **0.986** | **0.852** | **0.973** | **38/38** |
| v2 + "did you mean" 3 | 0 | 9.7 / 16.3 | | | | |

- The exact-name sweep (`sql/proposed/exact_name_sweep.sql`) is falsifiable: it caught the fast
  version's defect on the exact payer that the evaluator found.
- "Did you mean" at 3 words saves nothing measurable, so v2 keeps 10.
- Quality metrics: `evaluate --sample 150` (899 generated queries) through the Data API on the branch: `results/quality-fn-v2.json`.

## Load test: v2 at 2–16 CU (same branch, 4 serverless tasks, same shape as the original's run)

| Users | Requests/s | p50 ms | p95 ms | p99 ms | Failures |
|---:|---:|---:|---:|---:|---:|
| 10 | 262.3 | 27 | 58 | 76 | 0 |
| 25 | 331.4 | 64 | 140 | 190 | 0 |
| 50 | 330.3 | 120 | 300 | 370 | 0 |
| 100 | 326.5 | 290 | 460 | 580 | 0 |
| 200 | 315.8 | 620 | 780 | 910 | 0 |
| 400 | 323.4 | 1,300 | 1,500 | 1,600 | 0 |

**About 315–331 requests/s with v2, against about 200–245 with the original at the same CU range
(+40%).** At 10 users p95 is 58 ms (original: 64 ms). 0 failures.

## Follow-up (not done)
- The synonym design can erase a brand word (`badgercare -> medicaid`). v2 makes the trigram part
  carry the brand, but a synonym should add a lexeme, not replace the brand. Review the brand-to-generic
  rows in `data/seed/synonyms.csv`.

## Synonym fix: "is a kind of" rows add a word, they do not replace the brand (branch, 2026-10-06)
A Lakebase tokenizer synonym replaces the word, like Postgres' own synonym dictionary (one word ->
one lexeme; the table key is `(name, word)`). That is right for equivalents and wrong for hypernyms:
`badgercare -> medicaid` erased the brand. New rule in `sql/10_search_index.sql`: a `program_name`,
`brand_family` or `former_name` row whose word is still a live brand in the payer names EXPANDS: the
product keeps its brand and gets the wider word added to its document. Everything else replaces.

| | old synonyms | first rule (any word in a name expands) | final rule (kind + live brand) |
|---|---:|---:|---:|
| brand@10 / MRR (899 generated queries) | 0.986 / 0.973 | 0.942 / 0.930 | 0.984 / 0.973 |
| Wider-word queries ("medicaid wi" -> BadgerCare, "centene ak" -> Ambetter), 241 | 215 | | **233** |
| Exact-name misses (6,291) / live tests | 0 / 38 of 38 | 0 / 38 of 38 | 0 / 38 of 38 |

- The first rule also expanded equivalents that appear in names (`bluecross`, state codes) and broke
  joined-word queries (1.0 -> 0.748). Only the evaluator caught it; the sweep and live tests did not.
- 3 product-line rows (`cigna_healthcare`, `humana_medicare`, `anthem_medicare`) are equivalents, now kind
  `product_line`. One generated query is lost (`oregon plan`); 18 wider-word queries are gained.
- Wider-word A/B: `scripts/ops/wider_word_ab.py` (queries built from the expand rows; same API, two endpoints).

> **2026-10-07 update:** the 241-query set above was built before 3 rows became `product_line`. With the shipped rule the set is 88 queries: 80 hit. See [wider-word-2026-10-07.md](wider-word-2026-10-07.md).
