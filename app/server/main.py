"""Payer finder: the app server. The browser talks only to this server; this server talks to the Data API.

Routes: GET / (the page), GET /static/* (fixed files, no build step, no env templating),
POST /api/search {q, page}. Nothing else. No docs routes, no debug routes.
"""
from __future__ import annotations

import hashlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .dataapi import MAX_PAGES, PAGE_SIZE, RPC_PATH, DataApiClient, TokenProvider, UpstreamError

APP_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = APP_DIR / "static"
# The repo's own file. scripts/stage_deploy.py puts it at sql/ inside the upload copy, which has no parent repo.
SEARCH_SQL = next((p for p in (APP_DIR.parent / "sql/20_search_function.sql", APP_DIR / "sql/20_search_function.sql")
                   if p.is_file()), APP_DIR.parent / "sql/20_search_function.sql")
REPO_URL = "https://github.com/saselvan/lakebase-payer-search"
# Production runs this commit's sql/20_search_function.sql (claim C15). Move both only after a deploy check.
DEPLOYED_COMMIT = "b60a1512fd2f43332960ed543021b9e3f20d4114"
DEPLOYED_BLOB = "ec46a4228f567064654b12f70f249c128ab75f3c"
SHOWN_FUNCTIONS = ("api.search_insurers", "search_idx.search_core")
log = logging.getLogger("finder")

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
AUTH_NOTE = "OAuth token of the app's service principal. The browser never gets it."


def _host(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    return raw if raw.startswith("https://") else f"https://{raw.removeprefix('http://')}"


def setting(env, name: str) -> str:
    """A value from the app's environment. No default: app.yaml holds a placeholder until stage_deploy fills it."""
    value = (env.get(name) or "").strip()
    if not value or value.startswith("<"):
        raise RuntimeError(f"{name} is not set in the app's environment")
    return value


def data_api_base(env) -> str:
    value = setting(env, "DATA_API_BASE")
    if not value.startswith("https://"):
        raise RuntimeError("DATA_API_BASE must be an https:// URL")
    return value


def build_client(http: httpx.AsyncClient, env: dict[str, str] | None = None) -> DataApiClient:
    e = os.environ if env is None else env
    tokens = TokenProvider(_host(e["DATABRICKS_HOST"]), e["DATABRICKS_CLIENT_ID"],
                           e["DATABRICKS_CLIENT_SECRET"], http)
    return DataApiClient(data_api_base(e), tokens, http)


def git_blob_hash(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_definition(path: Path = SEARCH_SQL) -> dict:
    """The repo's search function file, read once at start-up. A missing file or function stops start-up, and so
    does a file that is not the deployed blob: the drawer names the deployed commit."""
    data = path.read_bytes()
    if git_blob_hash(data) != DEPLOYED_BLOB:
        raise RuntimeError(f"{path.name} is not the deployed blob {DEPLOYED_BLOB[:7]}; deploy and check it first")
    text = data.decode()
    functions = []
    for name in SHOWN_FUNCTIONS:
        start = text.index(f"CREATE OR REPLACE FUNCTION {name}(\n")
        body_start = text.index("AS $fn$", start) + len("AS $fn$")
        end = text.index("$fn$;", body_start) + len("$fn$;")
        functions.append({"name": name, "statement": text[start:end]})
    return {"source": {"repo": REPO_URL, "path": "sql/20_search_function.sql", "commit": DEPLOYED_COMMIT,
                       "blob": DEPLOYED_BLOB}, "functions": functions}


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    q: str = Field(max_length=200)
    page: int = Field(default=1, ge=1, le=MAX_PAGES)


def create_app(client: DataApiClient | None = None, tenant: str | None = None,
               env: dict[str, str] | None = None) -> FastAPI:
    e = os.environ if env is None else env
    tenant = tenant or setting(e, "FINDER_TENANT")
    if client is None:
        data_api_base(e)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if client is None:
            async with httpx.AsyncClient() as http:
                app.state.client = build_client(http, env)
                yield
        else:
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.tenant = tenant
    app.state.definition = load_definition()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "bad_request"}, status_code=400)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.post("/api/search")
    async def search(body: SearchIn, request: Request):
        q = " ".join(body.q.split())
        if not 2 <= len(q) <= 100:
            return JSONResponse({"error": "query_length"}, status_code=400)
        c: DataApiClient = request.app.state.client
        try:
            res = await c.search(q, request.app.state.tenant, body.page)
        except UpstreamError as e:
            status = 400 if e.status == 400 and e.code == "22023" else 502
            return JSONResponse({"error": "search_failed", "upstream_status": e.status,
                                 "upstream_code": e.code}, status_code=status)
        except httpx.HTTPError as e:
            log.warning("search transport error: %s", type(e).__name__)
            return JSONResponse({"error": "search_unreachable"}, status_code=502)
        # Owner decision 15: the browser gets the body keys as sent, but customer_id (the tenant label) by key only,
        # and the Data API by path only: no tenant value and no host in any answer.
        shown = {**res.request_body, "customer_id": None}
        return {
            "query": q,
            "page": body.page,
            "rows": res.rows,
            "end": len(res.rows) < PAGE_SIZE or body.page >= MAX_PAGES,
            "elapsed_ms": res.elapsed_ms,
            "lens": {
                "browser_request": {"method": "POST", "path": "/api/search",
                                    "body": {"q": body.q, "page": body.page}},
                "server_request": {"method": "POST", "url": RPC_PATH, "body": shown, "authorization": AUTH_NOTE},
                "status": res.status,
                "function": {"name": SHOWN_FUNCTIONS[0], "arguments": shown},
                "definition": request.app.state.definition,
            },
        }

    return app
