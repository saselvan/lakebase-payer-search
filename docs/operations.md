# Operations: refresh, scaling, and troubleshooting

## Index refresh (zero downtime)

The search index is a materialized view over the synced table. Refresh it after the source Delta table is updated:

```bash
SKIP_DATA=1 scripts/setup.sh
```

This runs SQL without regenerating the synthetic payer data. It:
1. Syncs the SNAPSHOT synced table from Unity Catalog (~60 s for 3M rows).
2. Refreshes `search_idx.alias_key` **concurrently** (no read lock, searches stay online).
3. Recreates the search function (zero downtime: functions are `CREATE OR REPLACE`, existing callers do not disconnect).
4. Refreshes `search_idx.vocab` (corpus word list for "did you mean").

**Proof of zero downtime**: 7,486 of 7,486 live searches succeeded during a full sync + index refresh on 2026-10-06 ([detailed results](../results/zero-downtime-2026-10-06.md)). No searches failed or returned empty results.

### Structural changes (rare)

If you add a new column, change index type, or rebuild from scratch:

```bash
psql -v rebuild=1 -f sql/10_search_index.sql
```

This drops and recreates the materialized views (requires a write lock; searches will block for ~30 s). Plan this during low traffic.

## Compute sizing

### For development and testing
- **2–4 CU**: Sufficient for a few requests/minute (e.g., integration tests, one developer).
- **Test step, no concurrency**: "Scale to zero" is safe if you set a 5 s client timeout.

### For production
- **Minimum 2 CU**: Do not use 1 CU. An earlier test at 1 CU returned HTTP 500 "out of memory" under burst load (200+ users). Lesson: undersized compute fails, it does not slow down gracefully ([detailed results](../results/loadtest-cu32-2026-10-06.md#a-failed-earlier-attempt-kept-for-the-lesson)).

- **Pilot size (v2): 2–16 CU**: Tested on AWS. **~315–331 requests/s** (v2 function), p50 latency ~27–64 ms at 10–25 users. [Detailed results](../results/exact-name-deepdive-2026-10-06.md#load-test-v2-at-216-cu-same-branch-4-serverless-tasks-same-shape-as-the-originals-run).
  - **For reference (v1): 16 CU** returned ~200–245 requests/s. **32 CU** returned ~440–465 requests/s (single-endpoint maximum). [v1 results](../results/loadtest-2026-10-06.md), [32 CU results](../results/loadtest-cu32-2026-10-06.md).

- **Scaling**: Throughput grows with CU. Above saturation, more users add queueing latency, not throughput.

### CU ceiling
The autoscaling maximum is 32 CU per endpoint. To exceed that, add read-only endpoints and distribute reads (see [Caveats](#caveats)).

## Cold start

The first API call after the compute sleeps (autoscaling idle timeout) takes ~1.6 s (not an error). Subsequent calls take ~140–160 ms.

**Endpoint settings for production**:
- Do not rely on scale-to-zero for a user-facing search. Keep the compute on.
- Set a long suspend timeout (`suspend_timeout_duration` 86400 s = 24 h) or disable suspension entirely.
- Set the minimum CU for normal peak load (minimum 2 CU, not 1).

**REST client**: Use a 5 s timeout (not 3 s) for the first request after scale-to-zero.

Details: [Cold start results](../results/coldstart-2026-10-06.md).

## Monitoring and alerting

### Query performance
The search log `audit.search_log` records every search:

```sql
SELECT
  searched_at, customer_id, caller, q, result_count,
  duration_ms, round(duration_ms::numeric / 1000, 2) AS duration_s
FROM audit.search_log
WHERE searched_at > now() - interval '1 hour'
ORDER BY duration_ms DESC
LIMIT 20;
```

Alert on:
- **Slow queries**: p50 duration > 50 ms (inside the database) suggests heavy load or a bug.
- **Zero results**: result_count = 0 for a query that should match (e.g., a typo of a known brand). Check if synonyms are stale.
- **Caller errors**: A caller that logs 403 (permission denied) or 400 (bad input) frequently needs investigation.

### Index staleness
Check when the index was last refreshed:

```sql
SELECT pg_stat_get_live_tuples('search_idx.alias_key'::regclass) AS key_count;
```

If this is below 30,000 or has not changed since yesterday, run `SKIP_DATA=1 scripts/setup.sh`.

### Connection count
Postgres connections are limited by CU (roughly 1 + 64 × min_CU, typically 225 at 2–16 CU). Check:

```sql
SELECT count(*) FROM pg_stat_activity;
```

Most connections should be from the search workload (`application_name` like `PostgREST`). If open connections stay elevated after traffic drops, check for idle transactions (e.g., a client with an unclosed result set).

## Backup and recovery

The system is stateless:
- The synced table is read-only and synced from Unity Catalog (rebuild with `SKIP_DATA=1 scripts/setup.sh`).
- The search index is derived from the synced table (rebuild by running `sql/10_search_index.sql`).
- The search log is append-only for audit (keep the most recent N days, then delete).

**Disaster recovery**: A point-in-time restore of the Lakebase branch gives you an old copy of everything. To go back:
1. `databricks postgres create-branch <project> <branch-id> --json '{"spec":{"source_branch":"<parent>","source_branch_time":"<ISO-8601>"}}'` (PITR).
2. Point the Data API at the restored branch.
3. Restore the source Delta table from UC time travel if needed.

## FAQ

**Q: Can I run multiple endpoints to exceed 32 CU?**  
A: Only read-only endpoints (no log writes). The search function writes a log row to every search, so it must run on read-write compute. Moving the log to a separate async queue would unlock read replicas. See [ADR 0004](adr/0004-definer-function-is-the-only-door.md) for the security reason logs live inside the function.

**Q: How often should I refresh synonyms?**  
A: When new payers are added to the corpus or when evaluation scores drop. Regenerate with `python -m insurer_search.synonyms`. Re-run the evaluator to confirm the score improves.

**Q: What does "no think time" mean in the load test?**  
A: Closed-loop load: each user sends the next request as soon as the last returns, no pause. This is a stress test, not a realistic user shape. Real users with 1 s think time fit lower throughput numbers.

**Q: The cold start takes 1.6 s. Is that a bug?**  
A: No. The Lakebase compute needs to wake from idle (internal restore of memory state, buffer pool warmup). Plan for 5 s client timeout and retry on timeout. Keep the compute on for production.

**Q: How many searches/s can one payer support?**  
A: See [Performance](../README.md#performance). **v2 at 16 CU**: ~315–331 requests/s. **32 CU** (single-endpoint max): ~440–465 requests/s. This is for all tenants combined. Per-tenant rate limiting is not implemented.

**Q: My queries are returning no results. What went wrong?**  
A: Check:
1. Is the search log showing `result_count = 0`? If so, the query might not match any payer name (search is case-insensitive and handles typos, but very broken queries will miss). Try the exact payer name.
2. Is the query longer than 100 characters? Search rejects it with HTTP 400.
3. Is the query only 1 character? Search rejects it with HTTP 400 (minimum 2 characters).
4. Did the evaluator show `brand@10 = 0.98`? If not, run it again and file a bug with the missed query.
