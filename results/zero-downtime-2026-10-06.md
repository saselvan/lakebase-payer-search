# Zero-downtime check, 2026-10-06

**Result: 7,486 of 7,486 searches returned 200. No errors, no empty results.**

Method: `scripts/zero_downtime_check.py 1200` searched `bcbs ga` in a loop for 20 minutes with no
client retries, from a laptop (latency includes the internet path). During the loop:

| Step | Start (UTC) | End (UTC) |
|---|---|---|
| Full refresh of the SNAPSHOT synced table (3M rows) | 21:07:30 | 21:08:40 |
| Index refresh (`SKIP_DATA=1 scripts/setup.sh`: REFRESH MATERIALIZED VIEW CONCURRENTLY + function re-create) | 21:08:40 | 21:10:02 |

Latency (laptop): p50 138 ms, p99 722 ms, max 2,736 ms. The max was during the refresh window;
no request failed. Endpoint CU range 2–16, unchanged during the run.
