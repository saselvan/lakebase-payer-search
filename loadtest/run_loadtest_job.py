"""Serverless job entry point: run one Locust process (one of N parallel tasks) against the insurer search API.

Runs in the workspace region, so latency is the API, not a laptop's network.
Writes <out_dir>/<run_name>_{summary.json,stats.csv,stats_history.csv,failures.csv}.
Args: --locustfile --data-api-base --workspace-host --sp-application-id --steps --step-seconds
      --wait-min --wait-max --out-dir --run-name
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import pandas as pd


def secret() -> str:
    from databricks.sdk.runtime import dbutils
    return dbutils.secrets.get("lakebase-payer-search", "sp_client_secret")


def summarise(csv_prefix: str, steps: list[int], hold: int) -> list[dict]:
    """One row per step: rps and the median of the per-second percentiles, ramp excluded."""
    hist = pd.read_csv(f"{csv_prefix}_stats_history.csv")
    hist = hist[hist["Name"] == "Aggregated"].copy()
    hist = hist[hist["Total Request Count"] > 0]
    if hist.empty:
        return []
    hist["t"] = hist["Timestamp"] - hist["Timestamp"].min()
    rows = []
    for i, users in enumerate(steps):
        seg = hist[(hist["t"] >= i * hold + 10) & (hist["t"] < (i + 1) * hold)]  # skip 10 s ramp
        if len(seg) < 2:
            continue
        req = seg["Total Request Count"].iloc[-1] - seg["Total Request Count"].iloc[0]
        fail = seg["Total Failure Count"].iloc[-1] - seg["Total Failure Count"].iloc[0]
        secs = seg["t"].iloc[-1] - seg["t"].iloc[0]

        def num(c):
            return pd.to_numeric(seg[c], errors="coerce").median()

        rows.append({"users": users, "rps": round(req / secs, 1) if secs else None,
                     "p50_ms": num("50%"), "p95_ms": num("95%"), "p99_ms": num("99%"),
                     "failures": int(fail), "requests": int(req)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ["data-api-base", "workspace-host", "sp-application-id", "out-dir", "run-name"]:
        ap.add_argument(f"--{a}", required=True)
    ap.add_argument("--steps", default="10,25,50,100,200")
    ap.add_argument("--step-seconds", type=int, default=60)
    ap.add_argument("--wait-min", default="0")
    ap.add_argument("--wait-max", default="0")
    ap.add_argument("--locustfile", required=True, help="path of locustfile.py (no __file__ on serverless)")
    a = ap.parse_args()
    cpus = os.cpu_count() or 1
    n_workers = 1
    print(f"cpu_count={cpus} python={sys.version.split()[0]}", flush=True)
    env = dict(os.environ, DATA_API_BASE=a.data_api_base, WORKSPACE_HOST=a.workspace_host,
               SP_APPLICATION_ID=a.sp_application_id, SP_CLIENT_SECRET=secret(),
               STEPS=a.steps, STEP_SECONDS=str(a.step_seconds), WAIT_MIN=a.wait_min, WAIT_MAX=a.wait_max)
    locustfile = a.locustfile
    csv_prefix = f"/tmp/{a.run_name}"
    steps = [int(s) for s in a.steps.split(",")]
    total = len(steps) * a.step_seconds + 120
    base = [sys.executable, "-m", "locust", "-f", locustfile]
    # One gevent process per task. Serverless blocks the localhost link Locust's master/worker
    # and --processes modes need, so scale-out is several tasks, one machine each (see run_loadtest.py).
    proc = subprocess.Popen(base + ["--headless", "--csv", csv_prefix, "--csv-full-history", "--only-summary"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        out, _ = proc.communicate(timeout=total)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        out += "\nTIMEOUT: locust killed"
    print(out[-4000:], flush=True)
    rows = summarise(csv_prefix, steps, a.step_seconds) if os.path.exists(f"{csv_prefix}_stats_history.csv") else []
    fpath = f"{csv_prefix}_failures.csv"
    failures = pd.read_csv(fpath).head(20).to_dict("records") if os.path.exists(fpath) else []
    res = {"cpu_count": cpus, "workers": n_workers, "steps": a.steps, "step_seconds": a.step_seconds,
           "wait": [a.wait_min, a.wait_max], "summary": rows, "failures": failures,
           "master_tail": out[-1500:], "worker_tail": ""}
    os.makedirs(a.out_dir, exist_ok=True)
    with open(f"{a.out_dir}/{a.run_name}_summary.json", "w") as f:
        json.dump(res, f, indent=1, default=str)
    for suffix in ["stats", "stats_history", "failures"]:
        if os.path.exists(f"{csv_prefix}_{suffix}.csv"):
            shutil.copy(f"{csv_prefix}_{suffix}.csv", f"{a.out_dir}/{a.run_name}_{suffix}.csv")
    print(json.dumps(rows, default=str))


if __name__ == "__main__":
    main()
