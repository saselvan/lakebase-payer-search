-- Exact-name sweep: search the exact name of EVERY payer and count the names that are not in the top 10.
-- Run as the project owner: BRANCH=<id> scripts/psql.sh -v label=<run-name> -f sql/proposed/exact_name_sweep.sql
SET max_parallel_workers_per_gather = 0;
CREATE TEMP TABLE sweep AS
SELECT p.payer_id, p.payer_name,
       EXISTS (SELECT 1 FROM search_idx.search_core(p.payer_name, 'exact-sweep', 10, 0, :'label') r
               WHERE r.payer_id = p.payer_id) AS hit
FROM (SELECT DISTINCT payer_id, payer_name FROM search_idx.alias_key) p;
SELECT count(*) AS payers, count(*) FILTER (WHERE NOT hit) AS misses FROM sweep;
SELECT payer_name FROM sweep WHERE NOT hit ORDER BY payer_name LIMIT 25;
SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)::numeric, 2) AS p50_ms,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::numeric, 2) AS p95_ms
FROM audit.search_log WHERE customer_id = 'exact-sweep' AND caller = :'label';
