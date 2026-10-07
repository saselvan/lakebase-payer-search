"""Run the search-quality flywheel (FM propose -> validate -> evaluate -> MLflow -> gate) on serverless.

Stages the notebook with a read-only copy of src/insurer_search and data/seed, uploads it to the
workspace, submits a one-time serverless run, and writes:
  results/synonyms_fm_candidates.csv   FM synonym candidates that passed the safety rules
  results/flywheel-run.json            all numbers, misses and MLflow run URLs
Nothing is applied to the live search index.

Usage: uv run python scripts/run_quality_job.py [--rule-sample 600] [--model databricks-claude-sonnet-4-5]
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs

sys.path.insert(0, os.path.dirname(__file__))
from uc_sql import load_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def stage(dst: Path) -> None:
    shutil.copy(ROOT / "jobs/quality_flywheel/flywheel_notebook.py", dst / "flywheel_notebook.py")
    shutil.copytree(ROOT / "src/insurer_search", dst / "lib/insurer_search",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "data/seed", dst / "data/seed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule-sample", default="600")
    ap.add_argument("--model", default="databricks-claude-sonnet-4-5")
    ap.add_argument("--concurrency", default="10")
    ap.add_argument("--refresh-proposals", action="store_true")
    a = ap.parse_args()
    load_env()
    e = os.environ
    w = WorkspaceClient(profile=e["DATABRICKS_PROFILE"])
    me = w.current_user.me().user_name
    ws_dir = f"/Users/{me}/lakebase-payer-search/quality_flywheel"
    with tempfile.TemporaryDirectory() as tmp:
        stage(Path(tmp))
        subprocess.run(["databricks", "workspace", "import-dir", tmp, ws_dir, "--overwrite",
                        "-p", e["DATABRICKS_PROFILE"]], check=True, capture_output=True)
    params = {"data_api_base": e["DATA_API_BASE"], "workspace_host": e["WORKSPACE_HOST"],
              "sp_application_id": e["SP_APPLICATION_ID"], "uc_catalog": e["UC_CATALOG"],
              "uc_schema": e["UC_SCHEMA"], "model": a.model, "rule_sample": a.rule_sample,
              "concurrency": a.concurrency, "refresh_proposals": str(a.refresh_proposals).lower()}
    waiter = w.jobs.submit(run_name="lakebase-payer-search-quality-flywheel", tasks=[jobs.SubmitTask(
        task_key="flywheel", notebook_task=jobs.NotebookTask(notebook_path=f"{ws_dir}/flywheel_notebook",
                                                             base_parameters=params))])
    print("submitted run", waiter.run_id, flush=True)
    try:
        run = waiter.result(timeout=timedelta(minutes=75))
    except Exception as ex:  # noqa: BLE001
        run = w.jobs.get_run(waiter.run_id)
        out = w.jobs.get_run_output(run.tasks[0].run_id)
        print("FAILED:", ex, "\n", out.error, (out.error_trace or "")[-3000:])
        raise SystemExit(1)
    out = w.jobs.get_run_output(run.tasks[0].run_id)
    res = json.loads(out.notebook_output.result)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results/flywheel-run.json").write_text(json.dumps(res, indent=1))
    with open(ROOT / "results/synonyms_fm_candidates.csv", "w", newline="") as f:
        cw = csv.DictWriter(f, fieldnames=["word", "canonical", "kind", "brand", "source"])
        cw.writeheader()
        cw.writerows(res["candidates"])
    print(json.dumps({k: res[k] for k in ["fm", "fm_seconds", "dropped_reasons", "dropped_shape", "mlflow"]}, indent=1))
    print("rule:", res["rule"]["total"])
    print("candidates:", len(res["candidates"]), "would-fix misses:", len(res["would_fix"]))


if __name__ == "__main__":
    main()
