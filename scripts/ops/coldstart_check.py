"""Cold-start check: what does the FIRST Data API call see after the compute has suspended?

Sets the production endpoint suspend timeout to 60 s, runs 4 trials (wait until the compute is no
longer ACTIVE, then 1 search with no client retries, then 5 warm searches), and ALWAYS restores the
suspend timeout to 86400 s at the end. Writes results/coldstart-raw.json.
Usage: uv run python scripts/ops/coldstart_check.py
"""
import json
import os
import subprocess
import sys
import time

sys.path[:0] = ["src", "scripts"]
from uc_sql import load_env  # noqa: E402

load_env()
from insurer_search.client import InsurerSearchClient, SearchError  # noqa: E402

PROFILE = os.environ["DATABRICKS_PROFILE"]
EP = f"projects/{os.environ['LAKEBASE_PROJECT']}/branches/production/endpoints/primary"
MASK = "spec.suspension"   # the mask that worked live (2026-10-04); applies in ~1 s, open sessions stay


def cli(*args):
    return subprocess.run(["databricks", "postgres", *args, "-p", PROFILE, "-o", "json"],
                          capture_output=True, text=True)


def state():
    return json.loads(cli("get-endpoint", EP).stdout)["status"]["current_state"]


def set_suspend(seconds):
    r = cli("update-endpoint", EP, MASK, "--json",
            json.dumps({"spec": {"suspend_timeout_duration": seconds}}))
    if r.returncode:
        print("update error:", r.stderr.strip()[:300])
    return r.returncode == 0


client = InsurerSearchClient.from_env()
client.max_retries = 0          # see the raw first response


def one():
    t = time.time()
    try:
        rows = client.search("bcbs ga", "coldstart-check")
        return ["200", len(rows), round((time.time() - t) * 1000)]
    except SearchError as e:
        return [str(e.status), str(e)[:120], round((time.time() - t) * 1000)]
    except Exception as e:  # noqa: BLE001  timeouts and resets are results too
        return [type(e).__name__, str(e)[:120], round((time.time() - t) * 1000)]


print("warm check:", one(), "state:", state())
if not set_suspend("60s"):
    sys.exit(1)
print("suspend set to 60 s", flush=True)
results = []
try:
    for trial in range(1, 5):
        t0 = time.time()
        while state() == "ACTIVE":
            time.sleep(5)
        waited, before = round(time.time() - t0), state()
        first = one()
        warm = [one()[2] for _ in range(5)]
        results.append({"trial": trial, "state_before": before, "waited_s": waited,
                        "first": first, "warm_ms": warm, "state_after": state()})
        print(results[-1], flush=True)
finally:
    print("restored to 86400 s:", set_suspend("86400s"), "state:", state())
    json.dump(results, open("results/coldstart-raw.json", "w"), indent=1)
