# FAQ: Frequently asked questions

## Getting started

**Q: Do I need to run my own Lakebase instance or can I call this search as a service?**

A: This repo is a template. You run it in your own Databricks workspace on your own Lakebase project. There is no hosted service. To use it: follow the [README setup steps](../README.md#run-it-in-your-workspace).

**Q: What if I don't have Unity Catalog or a Lakebase project?**

A: You need:
- A Databricks workspace with **Unity Catalog** enabled.
- A **Lakebase Autoscaling project** with Postgres 16 or later.
- A SQL warehouse (for data generation and sync setup).
- The Databricks CLI and `psql` installed locally.

If you don't have these, talk to your Databricks account team about provisioning them.

**Q: Can I run this on Azure instead of AWS?**

A: Yes, the Lakebase API and Data API are the same. The load tests were on AWS (us-east-1), so AWS numbers are confirmed. Azure numbers would need their own run (same code, different region). The code should work as-is, but test your peak load.

**Q: Do I need a private Lakebase project or is a shared one okay?**

A: You can share a project if you control the data. Do not put PHI on a shared project without encryption and row-level security (RLS).

## Search behavior

**Q: What does the score mean? Can I show it to users?**

A: The score is a relevance ranking, not a probability or percentage. Do not show it to users or compare it across different searches. Use it only to order results on one search.

**Q: What if my query returns no results?**

A: Some possible reasons:
- The query is too short (< 2 characters) or too long (> 100 characters). Search rejects it with HTTP 400.
- The payer name is very different from the query (e.g., querying "insurer" will not match "Blue Cross"). Search handles typos and abbreviations, not general synonyms.
- The payer was recently added and is not in the search index. Run `SKIP_DATA=1 scripts/setup.sh` to refresh.
- The payer's name changed and old variants are not in synonyms. Add them to `data/seed/synonyms.csv` and refresh.

Try the exact payer name to test.

**Q: Can I filter results by state or plan type?**

A: No, the API does not expose filter parameters. The search returns all matches ranked by relevance. If you need to filter, do it on the client side (see the `matched_alias` field to understand why each row matched).

**Q: What if I have multiple national payers with the same brand in different states?**

A: A national payer (e.g., UnitedHealthcare) often has one row per state in the index. Several rows of the same brand on one page are expected. You can group by `payer_name` on the client side if you want to show one row per brand.

**Q: Can I cache results or pre-compute common queries?**

A: Yes. The search is deterministic (same input → same output). You can cache by query and bust the cache when synonyms are updated. See `docs/quality-flywheel.md` for how the quality flywheel uses MLflow to optimize common queries.

## Performance and scaling

**Q: How many requests per second can the search handle?**

A: Depends on compute size. With the shipped search (v2) at 2–16 CU (AWS): about 315–331 requests/s, 0 failures ([results](../results/exact-name-deepdive-2026-10-06.md)). The original function (v1) reached about 440–465 requests/s at 16–32 CU ([results](../results/loadtest-cu32-2026-10-06.md)); v2 was not measured at 32 CU. See [Performance](../README.md#performance).

**Q: What compute size should I start with?**

A: Start with **2–16 CU for pilot/testing**. Do NOT use 1 CU—it runs out of memory under load. For production, size to your peak load and set a minimum CU accordingly. See [CU sizing](operations.md#compute-sizing).

**Q: What is the cold start penalty?**

A: The first API call after scale-to-zero takes ~1.6 s (not an error). The next calls took about 130–170 ms from a laptop client ([results](../results/coldstart-2026-10-06.md)). For production, keep the compute on (long suspend timeout or suspension off). See [Cold start](operations.md#cold-start).

**Q: Why does latency grow linearly after 25 users?**

A: The ceiling is reached when CPU is saturated (about 315–331 requests/s at 2–16 CU with v2). Above that, requests queue for CPU; each additional user adds queueing latency, not throughput. To raise the ceiling, increase CU (or add read-only replicas for logging, a future optimization).

**Q: Can I add read replicas to spread load?**

A: Only for reads. The search function writes a log row to every search, so it runs on read-write compute. Async logging would unlock read replicas (not yet implemented).

## Operations

**Q: How do I update the payer list?**

A: Update the source Delta table in Unity Catalog, then run:
```bash
SKIP_DATA=1 scripts/setup.sh
```

This syncs the latest data from UC and refreshes the search index with zero downtime.

**Q: How long does a full sync take?**

A: ~60 s for 3M rows. The sync is a SNAPSHOT materialized view (initial load). Refresh is concurrent, so searches keep running.

**Q: What happens if the sync fails?**

A: Check the Lakeflow pipeline in Databricks UI (it appears in the project details). Common causes:
- The source Delta table was deleted or the UC permission changed. Re-run setup with the right table name.
- The destination schema does not exist. Re-run setup (it creates schemas as needed).

The search index is not updated until the sync completes, so old results stay online.

**Q: Can I refresh just the search index without re-syncing?**

A: Yes:
```bash
psql -f sql/10_search_index.sql
```

This refreshes the materialized views only (no sync). Use if you updated synonyms in `data/seed/synonyms.csv`.

**Q: What if I need to roll back to an older version?**

A: The system is stateless. To restore:
1. Point the source Delta table back to its old version (UC time travel).
2. Run `SKIP_DATA=1 scripts/setup.sh` to re-sync.

For PITR (point-in-time restore) of the Postgres database, use Lakebase branching (see [Backup and recovery](operations.md#backup-and-recovery)).

## Security and compliance

**Q: Is my payer data encrypted?**

A: In transit, yes (HTTPS). At rest on Lakebase, it depends on your workspace encryption settings. The Lakebase project inherits the workspace's encryption (CMK if configured). See [Databricks security docs](https://docs.databricks.com/security/).

**Q: Can I use this with HIPAA?**

A: Yes, Lakebase is supported in HIPAA workspaces. The search does not process PHI (only payer names and synthetic IDs). SQL audit (`pgaudit`) is automatically enabled for HIPAA workspaces. See [HIPAA compliance](security.md#hipaa-compliance).

**Q: Do I need Private Link?**

A: Only if your network requires all inbound traffic to go through a private endpoint. The Data API supports inbound Private Link. See [Private Link](security.md#private-link).

**Q: What is logged when someone searches?**

A: The search log records: tenant ID, caller identity, query, timestamp, duration, result count. The query text itself is logged (not hidden), so avoid embedding sensitive context in queries.

In a HIPAA workspace, SQL audit (`pgaudit`) also logs statements to `system.access.audit`.

**Q: Can different tenants see each other's search queries?**

A: No, only your application sees the `audit.search_log`. Other tenants cannot query it. The `customer_id` parameter is logged for audit but does NOT filter results (all tenants see all payers). If you need per-tenant result filtering, add a WHERE clause in your application.

## Troubleshooting

**Q: I'm getting HTTP 400 "q must be 2 to 100 characters".**

A: The query is too short (< 2 chars) or too long (> 100 chars). Check your input before sending.

**Q: I'm getting HTTP 403 "permission denied to set role".**

A: Your service principal was not granted `EXECUTE` on the search function. Run `scripts/setup.sh` and make sure your SP's application ID is in `.env`. The setup script grants it automatically.

**Q: I'm getting HTTP 401 "missing authentication credentials".**

A: You did not send a Bearer token or the token is malformed. Use:
```bash
curl -H "Authorization: Bearer $TOKEN" ...
```

Not:
```bash
curl -H "Authorization: $TOKEN" ...
```

**Q: Searches are slow (p50 > 100 ms inside Postgres).**

A: Possible causes:
1. **Low CU**: If you set min CU to 1, upgrade to 2+. If you're at 2 CU and have high concurrency, increase to 4+ CU.
2. **Heavy concurrent load**: Check the connection count and load. More users = more queueing (expected). Add CU to handle it.
3. **Stale index**: Run `SKIP_DATA=1 scripts/setup.sh` to refresh the search keys.
4. **Stale synonyms**: If new payers were added and you haven't updated synonyms, queries might score lower and take longer. Regenerate and refresh.

Check the search log:
```sql
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms
FROM audit.search_log
WHERE searched_at > now() - interval '1 hour';
```

**Q: The evaluator is showing lower scores than before. What changed?**

A: The payer list, synonyms, or search function changed. Regenerate synonyms and re-run:
```bash
python -m insurer_search.synonyms > data/seed/synonyms_generated.csv
SKIP_DATA=1 scripts/setup.sh
uv run python -m insurer_search.evaluate --sample 150
```

If scores stayed low, check the exact-name sweep for missed brands:
```bash
python -m insurer_search.search --kind exact_name
```

**Q: I want to optimize for a specific query that returns wrong results.**

A: Use `scripts/ops/wider_word_ab.py` to A/B-test two endpoints. Add a synonym row and measure the effect before committing.

## Related

- [Architecture](architecture.md)
- [Operations](operations.md)
- [Security](security.md)
- [Search quality](search-quality.md)
- [README](../README.md)
