"""Reference client for the insurer search API: what the EHR backend does, in Python.

- Gets a Databricks OAuth token for the service principal (client credentials, M2M).
- Caches it and refreshes it 5 minutes before it expires (tokens last about 1 hour).
- Calls POST <data-api-base>/api/rpc/search_insurers.
- Retries 429 and 5xx with backoff; refreshes the token once on 401; never retries 400/403.

Config comes from the environment so the same code points at any workspace (AWS or Azure):
WORKSPACE_HOST, SP_APPLICATION_ID, SP_CLIENT_SECRET, DATA_API_BASE.
"""
from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass, field

import requests

PAGE_SIZE = 10
MAX_RESULTS = 50


class SearchError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


@dataclass
class TokenProvider:
    workspace_host: str
    client_id: str
    client_secret: str
    session: requests.Session = field(default_factory=requests.Session)
    _token: str | None = None
    _expires_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def token(self, force: bool = False) -> str:
        with self._lock:
            if force or self._token is None or time.time() > self._expires_at - 300:
                r = self.session.post(
                    f"{self.workspace_host.rstrip('/')}/oidc/v1/token",
                    auth=(self.client_id, self.client_secret),
                    data={"grant_type": "client_credentials", "scope": "all-apis"},
                    timeout=10,
                )
                if r.status_code != 200:
                    raise SearchError(r.status_code, r.text)
                body = r.json()
                self._token = body["access_token"]
                self._expires_at = time.time() + int(body.get("expires_in", 3600))
            return self._token


@dataclass
class InsurerSearchClient:
    api_base: str               # https://<host>/api/2.0/workspace/<workspace-id>/rest/<database>
    tokens: TokenProvider
    session: requests.Session = field(default_factory=requests.Session)
    max_retries: int = 3
    timeout_s: float = 5.0      # a call that wakes a suspended compute takes ~1.6 s (results/coldstart-2026-10-06.md)

    @classmethod
    def from_env(cls) -> "InsurerSearchClient":
        e = os.environ
        return cls(e["DATA_API_BASE"], TokenProvider(e["WORKSPACE_HOST"], e["SP_APPLICATION_ID"],
                                                     e["SP_CLIENT_SECRET"]))

    def search(self, q: str, customer_id: str, page: int = 1, page_size: int = PAGE_SIZE) -> list[dict]:
        """Page 1 = results 1-10, page 2 = 11-20 ... page 5 = 41-50. Past page 5 returns []."""
        body = {"q": q, "customer_id": customer_id, "lim": page_size, "off": (page - 1) * page_size}
        return self.call("search_insurers", body)

    def call(self, fn: str, body: dict) -> list[dict]:
        url = f"{self.api_base.rstrip('/')}/api/rpc/{fn}"
        refreshed = False
        for attempt in range(self.max_retries + 1):
            r = self.session.post(url, json=body, timeout=self.timeout_s, headers={
                "Authorization": f"Bearer {self.tokens.token()}", "Content-Type": "application/json"})
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401 and not refreshed:
                self.tokens.token(force=True)
                refreshed = True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt * 0.2, 2.0) + random.random() * 0.1)
                    continue
            raise SearchError(r.status_code, r.text)
        raise SearchError(599, "retries exhausted")
