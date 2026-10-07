"""Run the Locust load test on serverless compute in the workspace region and print the summary.

While it runs, samples Lakebase CPU (neon_utils.num_cpus) and in-database search time
(audit.search_log.duration_ms) every 15 s, to tell a database limit from a gateway limit.

Load generation scales out as --tasks parallel serverless tasks, each one Locust process with the
same step shape; the users per step in the table are the TOTAL across tasks.
Totals: rps, requests, failures add up; p50 = median of task p50s; p95/p99 = worst task (conservative).

Usage: uv run python scripts/run_loadtest.py --steps 10,25,50,100 --step-seconds 60 --tasks 4 --name run1
Results: results/<name>.json (+ CSVs in the UC volume <catalog>.<schema>.loadtest).
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import compute, jobs

sys.path.insert(0, os.path.dirname(__file__))
from uc_sql import load_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOCUST = "locust==2.46.6"


def sample_db(stop: threading.Event, samples: list) -> None:
    sql = ("CREATE EXTENSION IF NOT EXISTS neon_utils; "
           "SELECT num_cpus(), (SELECT count(*) FROM pg_stat_activity WHERE state = 'active'), "
           "(SELECT count(*) FROM pg_stat_activity)")
    while not stop.is_set():
        try:
            out = subprocess.run([str(ROOT / "scripts/psql.sh"), "-At", "-F", ",", "-c", sql],
                                 capture_output=True, text=True, timeout=60).stdout.strip().splitlines()
            cpus, active, conns = out[-1].split(",")
            samples.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "num_cpus": int(cpus), "active": int(active), "connections": int(conns)})
        except Exception as e:  # noqa: BLE001
            samples.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "error": str(e)[:100]})
        stop.wait(15)


def db_durations(start: str, end: str) -> list:
    sql = ("SELECT count(*), round(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms)::numeric, 1), "
           "round(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)::numeric, 1), "
           "round(percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms)::numeric, 1), "
           "date_trunc('minute', searched_at) FROM audit.search_log "
           f"WHERE searched_at BETWEEN '{start}' AND '{end}' GROUP BY 5 ORDER BY 5")
    out = subprocess.run([str(ROOT / "scripts/psql.sh"), "-At", "-F", ",", "-c", sql],
                         capture_output=True, text=True).stdout.strip().splitlines()
    return [dict(zip(["searches", "p50_ms", "p95_ms", "p99_ms", "minute"], l.split(","))) for l in out]


def combine(parts: list[dict], steps: list[int]) -> list[dict]:
    import statistics
    rows = []
    for i, users in enumerate(steps):
        seg = [p["summary"][i] for p in parts if len(p["summary"]) > i]
        if not seg:
            continue
        num = [r for r in seg if r["p50_ms"] == r["p50_ms"]]  # drop NaN
        rows.append({"users": users, "rps": round(sum(r["rps"] or 0 for r in seg), 1),
                     "p50_ms": statistics.median(r["p50_ms"] for r in num) if num else None,
                     "p95_ms": max(r["p95_ms"] for r in num) if num else None,
                     "p99_ms": max(r["p99_ms"] for r in num) if num else None,
                     "failures": sum(r["failures"] for r in seg), "requests": sum(r["requests"] for r in seg)})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="10,25,50,100,200")
    ap.add_argument("--step-seconds", default="60")
    ap.add_argument("--tasks", type=int, default=1, help="parallel load generators")
    ap.add_argument("--wait-min", default="0")
    ap.add_argument("--wait-max", default="0")
    ap.add_argument("--name", default="run")
    a = ap.parse_args()
    load_env()
    e = os.environ
    w = WorkspaceClient(profile=e["DATABRICKS_PROFILE"])
    me = w.current_user.me().user_name
    folder = f"/Workspace/Users/{me}/lakebase-payer-search/loadtest"
    out_dir = f"/Volumes/{e['UC_CATALOG']}/{e['UC_SCHEMA']}/loadtest"
    def params(k: int) -> list[str]:
        per_task = ",".join(str(max(1, round(int(u) / a.tasks))) for u in a.steps.split(","))
        return ["--data-api-base", e["DATA_API_BASE"], "--workspace-host", e["WORKSPACE_HOST"],
                "--sp-application-id", e["SP_APPLICATION_ID"], "--steps", per_task,
                "--step-seconds", a.step_seconds, "--wait-min", a.wait_min, "--wait-max", a.wait_max,
                "--out-dir", out_dir, "--run-name", f"{a.name}-t{k}", "--locustfile", f"{folder}/locustfile.py"]

    stop, samples = threading.Event(), []
    start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sampler = threading.Thread(target=sample_db, args=(stop, samples), daemon=True)
    sampler.start()
    try:
        run = w.jobs.submit(
            run_name=f"lakebase-payer-search-load-{a.name}",
            environments=[jobs.JobEnvironment(environment_key="locust", spec=compute.Environment(
                environment_version="4", dependencies=[LOCUST, "pandas"]))],
            tasks=[jobs.SubmitTask(task_key=f"load{k}", environment_key="locust",
                                   spark_python_task=jobs.SparkPythonTask(
                                       python_file=f"{folder}/run_loadtest_job.py", parameters=params(k)))
                   for k in range(a.tasks)],
        ).result(timeout=__import__("datetime").timedelta(minutes=60))
        state = f"{run.state.result_state} {run.state.state_message}"
    except Exception as ex:  # noqa: BLE001
        state = f"FAILED: {str(ex)[:300]}"
    finally:
        stop.set()
    end = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print("state:", state)
    parts = []
    for k in range(a.tasks):
        try:
            parts.append(json.loads(w.files.download(f"{out_dir}/{a.name}-t{k}_summary.json").contents.read()))
        except Exception as ex:  # noqa: BLE001
            print(f"task {k}: no summary ({ex})")
    if not parts:
        raise SystemExit("no task summaries")
    res = {"tasks": len(parts), "cpu_count": parts[0]["cpu_count"], "workers": len(parts),
           "summary": combine(parts, [int(u) for u in a.steps.split(",")]),
           "failures": [f for p in parts for f in p["failures"]],
           "cpu_warnings": sum("CPU usage" in p.get("master_tail", "") for p in parts),
           "master_tail": parts[0].get("master_tail"), "worker_tail": "", "per_task": parts}
    res["db_samples"] = samples
    res["db_durations"] = db_durations(start, end)
    res["window"] = [start, end]
    Path(ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / f"{a.name}.json").write_text(json.dumps(res, indent=1, default=str))
    print(f"tasks={res['tasks']} cpu_count/task={res['cpu_count']} locust_cpu_warnings={res['cpu_warnings']}")
    print(f"{'users':>6} {'rps':>8} {'p50_ms':>7} {'p95_ms':>7} {'p99_ms':>7} {'fail':>6}")
    for r in res["summary"]:
        print(f"{r['users']:>6} {r['rps']:>8} {r['p50_ms']:>7} {r['p95_ms']:>7} {r['p99_ms']:>7} {r['failures']:>6}")
    if not any(r["requests"] for r in res["summary"]):
        print("NO REQUESTS. master:", res.get("master_tail"), "\nworker:", res.get("worker_tail"))
    for f in res["failures"][:10]:
        print("failure:", f)
    print("db cpus:", sorted({s.get("num_cpus") for s in samples if "num_cpus" in s}),
          "max active:", max([s.get("active", 0) for s in samples] or [0]))
    for d in res["db_durations"]:
        print("db:", d)


if __name__ == "__main__":
    main()
