# Architecture: typo-tolerant payer search on Lakebase

A Postgres-based search engine running on Databricks Lakebase, exposed through the REST Data API. The system combines trigram similarity (for typo tolerance) with BM25 ranking (for multi-word queries) to find the best-matching insurance payer from a closed list of names.

## Data flow

```mermaid
graph LR
    A["Unity Catalog<br/>insurer_alias table<br/>3M rows"] -->|SNAPSHOT sync| B["Lakebase Postgres<br/>payer_serving.insurer_alias<br/>synced table"]
    B -->|Group & normalize| C["search_idx.alias_key<br/>33k search keys<br/>Materialized View"]
    D["Tokenizer synonyms<br/>payer_synonyms"] -->|BM25 index| C
    E["search_idx.vocab<br/>Corpus word list"] -->|'Did you mean'"] F["search_idx.search_core<br/>SECURITY DEFINER"]
    C -->|1. Trigram KNN<br/>2. BM25 top-k<br/>3. Score both| F
    F -->|result + audit| G["audit.search_log<br/>Per-search trail"]
    F -->|100% hit rate| H["api.search_insurers<br/>SECURITY INVOKER wrapper"]
    H -->|HTTPS Bearer token| I["Data API<br/>REST endpoint"]
    I -->|Service Principal<br/>OAuth 2.0| J["EHR backend"]
```

## Search function pipeline

The search flow runs in `search_idx.search_core()`:

1. **Input validation**: query length 2–100 characters, `customer_id` format check, pagination bounds.

2. **"Did you mean" correction**: Each word of 4+ letters not found in `search_idx.vocab` is replaced with the closest corpus word (edit distance ≤ ⌈word_length/3⌉). Adjacent words are joined if the joined form is a corpus word (`united health` → `unitedhealth`, which maps to UnitedHealthcare).

3. **Candidate generation**:
   - **Trigram path**: 100 nearest keys by `word_similarity()` using GiST index on `alias_key.key_norm` (no full-text parsing).
   - **BM25 path**: 100 best keys by `lakebase_bm25()` on the full-text vector after synonym expansion.

4. **Scoring**: For every candidate from either path:
   - Trigram relevance = `word_similarity(query, key_norm) + 0.1 × similarity(query, key_norm)`
   - BM25 relevance: standalone `<@>` scoring on every candidate (not just top-100), scaled by the best BM25 score in the result set. This ensures perfect trigram matches are never lost to BM25 ranking.
   - Combined score = trigram + scaled BM25.

5. **Deduplication**: One row per payer, keeping the best-scoring alias. Drop candidates below 0.3 word similarity and 0 BM25 (weak trigram-only matches). Hard cap: 50 results (5 pages of 10).

6. **Audit**: Insert one row into `audit.search_log` (query, tenant, caller, duration, result count).

## Why each component

### Trigram + BM25 union
Trigram distance (`<<->`) handles typos by finding the 100 nearest keys regardless of whether the query is a known word. BM25 handles multi-word queries and applies synonym expansion. Using both ensures a perfect name match (100% trigram similarity) is never lost if it falls outside the BM25 top-100 ([detailed analysis](../results/exact-name-deepdive-2026-10-06.md)).

### Synonym expand-vs-replace rule
A `lakebase_tokenizer` synonym replaces the word (one lexeme) like Postgres' own synonym dictionary. This is correct for equivalent forms (`bluecross` → `blue cross`, `bcbs` → `blue cross blue shield`) but wrong for hypernyms (`badgercare` → `medicaid` would erase the brand). 

Rule: if a `program_name`, `brand_family`, or `former_name` row's word is still a live brand in payer names, it **expands** (the brand is kept, the wider word added to the document). Everything else **replaces**. Measured: brand@10 accuracy 0.984 vs 0.942 with the first rule ([synonym fix](../results/exact-name-deepdive-2026-10-06.md#synonym-fix)).

### Normalized search keys instead of raw aliases
Raw aliases (3M rows) would require a per-row search cost. Grouping by normalized form (lowercase, punctuation → spaces, group numbers removed) collapses to ~33k unique search keys. Search cost now follows the corpus size, not the alias row count.

### SECURITY DEFINER + NOLOGIN owner
The wrapper `api.search_insurers` is `SECURITY INVOKER` (runs as the caller) and records `current_user` in the audit log. The core function `search_idx.search_core` is `SECURITY DEFINER` owned by a `NOLOGIN` role (`search_owner`) that has no login privilege and can only read the index + append to the log. The caller gets `EXECUTE` only on the wrapper, with no `SELECT` or `INSERT` grants. This isolates data access to the one function and prevents direct table access.

### Zero-downtime refresh
The index is a Materialized View. `REFRESH MATERIALIZED VIEW CONCURRENTLY` refreshes in the background without blocking reads; the old index remains visible until the new one is ready. Initial population is not concurrent (first run creates the index). Structural changes (new index type, new columns) need a rebuild (pass `-v rebuild=1` to rebuild from scratch, which requires a short write lock).

### Search log as one row per search
Every search is logged with tenant, query, caller, timestamp, duration, and result count. This enables audit trails, per-tenant billing, and slow query investigation. The log write is inside the same transaction as the search, so the tuple is atomic (results and audit row are always consistent).

## Performance characteristics (v2)

On 3M alias rows → 33k search keys, CU range 2–16, AWS us-east-1:

- **Throughput**: ~315–331 requests/s at 16 CU (v2). At 32 CU: ~440–465 requests/s (original function). 
- **Single search latency**: p50 9.8 ms inside the database (exact-name sweep). p95 16.5 ms. Client adds ~16 ms for Data API gateway + network.
- **Bottleneck**: Database CPU. The trigram KNN is the dominant cost.
- **Scaling**: Throughput grows with CU. Above saturation, more users only add queueing latency.

Full results: [v2 load test](../results/exact-name-deepdive-2026-10-06.md#load-test-v2-at-216-cu-same-branch-4-serverless-tasks-same-shape-as-the-originals-run) (315–331 rps), [v1 load test](../results/loadtest-2026-10-06.md) (200–245 rps), [32 CU](../results/loadtest-cu32-2026-10-06.md) (single-endpoint maximum).

## Data sync

The synced table `payer_serving.insurer_alias` is a SNAPSHOT materialized view from Unity Catalog. A full refresh (~60 s for 3M rows) copies the source Delta table into Postgres. The `search_idx.alias_key` materialized view is then refreshed `CONCURRENTLY` while searches continue reading the old index. See [Operations](operations.md).

## Related

- [Lakebase Search reference](https://docs.databricks.com/aws/en/oltp/lakebase-search/) (TODO: confirm public URL)
- [Architecture decisions](adr/)
- [Search quality (synonyms, exact names, evaluation)](search-quality.md)
