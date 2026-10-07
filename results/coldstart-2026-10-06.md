# Cold start, 2026-10-06: the first Data API call after the compute sleeps

**Result: slow, not an error.** In 4 of 4 trials the first search after scale-to-zero returned 200 with
the right results in **1.55–1.75 s**. The next calls took 131–172 ms. No 5xx, no timeout, no reset.

Method: `scripts/ops/coldstart_check.py` on the production endpoint. Suspend timeout set to 60 s, then
always restored to 86400 s (confirmed). Per trial: wait until the endpoint is no longer ACTIVE (IDLE in
all 4), send 1 search with no client retries (10 s timeout), then 5 warm searches. Laptop client, so
times include the internet path; the same OAuth token and HTTP session were reused across the sleep.

| Trial | Waited for IDLE (s) | First call | First call ms | Warm calls ms |
|---:|---:|---|---:|---|
| 1 | 177 | 200, 10 rows | 1,748 | 172, 158, 145, 131, 141 |
| 2 | 399 | 200, 10 rows | 1,577 | 165, 140, 164, 144, 135 |
| 3 | 240 | 200, 10 rows | 1,549 | 155, 304, 135, 135, 139 |
| 4 | 70 | 200, 10 rows | 1,591 | 161, 159, 153, 136, 140 |

## What this means for a REST client
- No database connections or database tokens live in the client, so the "stale pool after a day idle"
  failure of a Postgres-connected app cannot happen here. The token and keep-alive session held across
  the sleep and still worked.
- **Client timeout: 5 s** (not 3 s). The wake-up takes about 1.6 s plus the client's own network path.
- Retry on 429/5xx and timeouts with backoff (the reference client does this); never on 400/403.

## Endpoint settings for production
- Do not rely on scale-to-zero for a user-facing search. Keep the compute on (long suspend timeout, or
  suspension off) with a minimum CU sized for normal peak; see the 1 CU out-of-memory lesson in
  `results/loadtest-cu32-2026-10-06.md`.
