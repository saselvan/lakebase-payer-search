"""The app server's only way to the database: the Lakebase Data API, as the app's own service principal.

The token is fetched here, kept here, and sent only to the Data API. Nothing in this module returns
the token, a header, or an upstream body to a caller: errors carry a status and a Postgres code only.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("finder.dataapi")

RPC_PATH = "/api/rpc/search_insurers"
PAGE_SIZE = 10
MAX_PAGES = 5            # the API returns at most 50 results (DATA_CONTRACT.md)
CONTRACT_FIELDS = ("rank", "payer_id", "payer_name", "matched_alias", "state", "plan_type", "score")


class UpstreamError(Exception):
    """A failed call. Holds the HTTP status and the Postgres SQLSTATE, never the body text."""

    def __init__(self, status: int, code: str | None = None):
        super().__init__(f"upstream status {status}" + (f" code {code}" if code else ""))
        self.status = status
        self.code = code


def _pg_code(r: httpx.Response) -> str | None:
    try:
        code = r.json().get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) and len(code) <= 8 and code.isalnum() else None


@dataclass
class TokenProvider:
    """OAuth client credentials (M2M) for the app's service principal. Refreshes 5 min before expiry."""

    host: str
    client_id: str
    client_secret: str
    http: httpx.AsyncClient
    _token: str | None = None
    _expires_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def token(self, force: bool = False) -> str:
        async with self._lock:
            if force or self._token is None or time.time() > self._expires_at - 300:
                r = await self.http.post(
                    f"{self.host}/oidc/v1/token",
                    auth=(self.client_id, self.client_secret),
                    data={"grant_type": "client_credentials", "scope": "all-apis"},
                    timeout=10,
                )
                if r.status_code != 200:
                    log.warning("token exchange failed: status=%s", r.status_code)
                    raise UpstreamError(r.status_code, "token")
                body = r.json()
                self._token = body["access_token"]
                self._expires_at = time.time() + int(body.get("expires_in", 3600))
            return self._token


@dataclass
class SearchResult:
    rows: list[dict]
    elapsed_ms: float
    request_body: dict
    status: int = 200


@dataclass
class DataApiClient:
    api_base: str
    tokens: TokenProvider
    http: httpx.AsyncClient
    timeout_s: float = 8.0   # a call that woke a suspended compute took 2.2 s (runs/2026-10-24/c22)

    @property
    def rpc_url(self) -> str:
        return f"{self.api_base.rstrip('/')}{RPC_PATH}"

    async def search(self, q: str, customer_id: str, page: int) -> SearchResult:
        body = {"q": q, "customer_id": customer_id, "lim": PAGE_SIZE, "off": (page - 1) * PAGE_SIZE}
        refreshed = False
        retried = False
        token = await self.tokens.token()
        # From the first request to the answer, including a retry and a token refresh (the drawer's note says so).
        t0 = time.perf_counter()
        while True:
            headers = {"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json", "Accept": "application/json"}
            r = await self.http.post(self.rpc_url, json=body, headers=headers, timeout=self.timeout_s)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if 200 <= r.status_code < 300:
                rows = r.json()
                if not isinstance(rows, list):
                    raise UpstreamError(502, "shape")
                return SearchResult([project(row) for row in rows], round(elapsed_ms, 1), body, r.status_code)
            if r.status_code == 401 and not refreshed:
                token = await self.tokens.token(force=True)
                refreshed = True
                continue
            if (r.status_code == 429 or r.status_code >= 500) and not retried:
                retried = True
                await asyncio.sleep(0.3)
                continue
            code = _pg_code(r)
            log.warning("search failed: status=%s code=%s", r.status_code, code)
            raise UpstreamError(r.status_code, code)


def project(row: dict) -> dict:
    """Keep only the contract's fields, so nothing else the API might add reaches the browser."""
    return {k: row.get(k) for k in CONTRACT_FIELDS}
