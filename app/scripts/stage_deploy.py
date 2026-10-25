"""Build a clean upload tree for the finder app (I7, I11). Never copies .env.

Usage:
  uv run python scripts/stage_deploy.py                 # writes /tmp/lakebase-payer-finder-deploy
  uv run python scripts/stage_deploy.py /tmp/other

Reads production's Data API URL live (WorkspaceClient, profile from .env) and refuses a t-finder URL.
Fills FINDER_TENANT from .env into the copy of app.yaml only. The tracked app.yaml stays a placeholder.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "<set by scripts/stage_deploy.py>"
ALLOW = ("app.yaml", "requirements.txt")
ALLOW_DIRS = ("server", "static")
# The repo's own search function, one level up; the upload copy has no parent repo, so it goes in at sql/.
FROM_REPO = {"sql/20_search_function.sql": "../sql/20_search_function.sql"}
SKIP_DIR = {"__pycache__", ".git"}
SKIP_FILE = {".DS_Store"}


def allowlist(root: Path) -> list[str]:
    out = []
    for name in ALLOW:
        if (root / name).is_file():
            out.append(name)
    for folder in ALLOW_DIRS:
        base = root / folder
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR for part in path.relative_to(root).parts):
                continue
            if path.name in SKIP_FILE or path.suffix == ".pyc":
                continue
            out.append(path.relative_to(root).as_posix())
    for name, source in FROM_REPO.items():
        if (root / source).is_file():
            out.append(name)
    return out


def _fill_yaml(text: str, data_api_base: str, tenant: str) -> str:
    if PLACEHOLDER not in text:
        raise RuntimeError("tracked app.yaml has no placeholder to fill")
    text = text.replace(
        f'  - name: DATA_API_BASE\n    value: "{PLACEHOLDER}"',
        f'  - name: DATA_API_BASE\n    value: "{data_api_base}"',
        1,
    )
    text = text.replace(
        f'  - name: FINDER_TENANT\n    value: "{PLACEHOLDER}"',
        f'  - name: FINDER_TENANT\n    value: "{tenant}"',
        1,
    )
    if PLACEHOLDER in text:
        raise RuntimeError("a placeholder remains in the staged app.yaml")
    return text


def assert_production_base(data_api_base: str, production_base: str | None = None) -> str:
    value = (data_api_base or "").strip().rstrip("/")
    if not value.startswith("https://"):
        raise RuntimeError("DATA_API_BASE must be an https:// URL")
    if "t-finder-" in value:
        raise RuntimeError("DATA_API_BASE is not production: it names a t-finder branch")
    if production_base and value.rstrip("/") != production_base.rstrip("/"):
        raise RuntimeError("DATA_API_BASE is not production")
    return value


def stage(root: Path, dest: Path, *, data_api_base: str, tenant: str,
          production_base: str | None = None) -> Path:
    tenant = (tenant or "").strip()
    if not tenant or tenant.startswith("<"):
        raise RuntimeError("FINDER_TENANT is not set")
    base = assert_production_base(data_api_base, production_base)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for rel in allowlist(root):
        src, out = root / FROM_REPO.get(rel, rel), dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if rel == "app.yaml":
            out.write_text(_fill_yaml(src.read_text(), base, tenant))
        else:
            shutil.copy2(src, out)
    return dest


def _dotenv() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return {**env, **os.environ}


def production_data_api_url(profile: str, project: str) -> str:
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=profile)
    path = f"/api/2.0/postgres/projects/{project}/branches/production/databases/databricks-postgres/data-api"
    url = w.api_client.do("GET", path)["status"]["url"]
    return url.rstrip("/")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("dest", nargs="?", default="/tmp/lakebase-payer-finder-deploy")
    args = p.parse_args(argv)
    env = _dotenv()
    live = production_data_api_url(env["DATABRICKS_PROFILE"], env["LAKEBASE_PROJECT"])
    recorded = env.get("DATA_API_BASE_PRODUCTION", "").rstrip("/")
    dest = stage(ROOT, Path(args.dest), data_api_base=live, tenant=env["FINDER_TENANT"],
                 production_base=recorded or live)
    print(f"staged {dest} files={len(allowlist(ROOT))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
