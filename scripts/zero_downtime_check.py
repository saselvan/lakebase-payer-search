"""Search the live API in a loop for N seconds and count failures. Run it while the index refreshes.
Usage: uv run python scripts/zero_downtime_check.py 120
"""
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from uc_sql import load_env  # noqa: E402

load_env()
from insurer_search.client import InsurerSearchClient, SearchError  # noqa: E402

c = InsurerSearchClient.from_env()
c.max_retries = 0          # count every failure; no hiding behind retries
end = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 60)
codes, slow, lat = Counter(), 0, []
while time.time() < end:
    t = time.time()
    try:
        rows = c.search("bcbs ga", "zero-downtime-check")
        codes["200" if rows else "200-empty"] += 1
    except SearchError as e:
        codes[str(e.status)] += 1
    except Exception as e:  # noqa: BLE001  timeouts and resets count as failures too
        codes[type(e).__name__] += 1
    lat.append((time.time() - t) * 1000)
lat.sort()
print(f"requests={sum(codes.values())} codes={dict(codes)} p50={lat[len(lat)//2]:.0f}ms "
      f"p99={lat[int(len(lat)*.99)]:.0f}ms max={lat[-1]:.0f}ms (laptop, includes network)")
