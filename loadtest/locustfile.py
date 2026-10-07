"""Load test for the insurer search API (Locust).

Each simulated user is one EHR backend thread: it types a payer name, then sometimes pages.
The service principal token is fetched once per process and refreshed before expiry,
exactly like the reference client.

Env: WORKSPACE_HOST, SP_APPLICATION_ID, SP_CLIENT_SECRET, DATA_API_BASE
Run (headless, step load):  locust -f loadtest/locustfile.py --headless --csv results/run
"""
import os
import random
import threading
import time

import requests
from locust import FastHttpUser, LoadTestShape, between, task

QUERIES = [
    "bcbs ga", "ga bcbs", "blu cros georgai", "blue cross", "aetna", "aetna ppo", "united helth",
    "uhc", "unitedhealth care", "medicad", "masshealth", "medi-cal", "tenncare", "ma blue cross",
    "anthem blue", "kaiser", "cigna ppo", "humana medicare", "tricare", "molina", "ambetter",
    "carefirst", "highmark", "horizon bcbs nj", "florida blue", "wellcare", "caresource",
    "oscar", "umr", "meritain", "state fund", "geico pip", "excellus", "independence blue",
]
TYPO_KEYS = "abcdefghijklmnopqrstuvwxyz"


def typo(q: str) -> str:
    """Make the query realistic: 30 % get one random typo (swap, drop or replace a letter)."""
    if len(q) < 4 or random.random() > 0.3:
        return q
    i = random.randrange(1, len(q) - 1)
    op = random.choice("sdr")
    if op == "s":
        return q[:i] + q[i + 1] + q[i] + q[i + 2:]
    if op == "d":
        return q[:i] + q[i + 1:]
    return q[:i] + random.choice(TYPO_KEYS) + q[i + 1:]


class _Token:
    _lock = threading.Lock()
    _tok, _exp = None, 0.0

    @classmethod
    def get(cls) -> str:
        with cls._lock:
            if cls._tok is None or time.time() > cls._exp - 300:
                r = requests.post(f"{os.environ['WORKSPACE_HOST'].rstrip('/')}/oidc/v1/token",
                                  auth=(os.environ["SP_APPLICATION_ID"], os.environ["SP_CLIENT_SECRET"]),
                                  data={"grant_type": "client_credentials", "scope": "all-apis"}, timeout=10)
                r.raise_for_status()
                cls._tok, cls._exp = r.json()["access_token"], time.time() + r.json()["expires_in"]
            return cls._tok


class EhrBackend(FastHttpUser):
    host = os.environ.get("DATA_API_BASE", "http://localhost").rstrip("/")
    wait_time = between(float(os.environ.get("WAIT_MIN", "0")), float(os.environ.get("WAIT_MAX", "0")))

    def _search(self, q: str, off: int, name: str):
        with self.client.post("/api/rpc/search_insurers", name=name, catch_response=True,
                              json={"q": q, "customer_id": f"tenant-{random.randrange(500):03d}", "lim": 10, "off": off},
                              headers={"Authorization": f"Bearer {_Token.get()}"}) as r:
            if r.status_code != 200:
                r.failure(f"HTTP {r.status_code}: {r.text[:120]}")

    @task(8)
    def search_page1(self):
        self._search(typo(random.choice(QUERIES)), 0, "search p1")

    @task(2)
    def search_next_page(self):
        self._search(random.choice(QUERIES), 10 * random.randrange(1, 5), "search p2-5")


class Steps(LoadTestShape):
    """Step load: hold each user count for STEP_SECONDS. STEPS="10,25,50,100,200,400"."""
    steps = [int(s) for s in os.environ.get("STEPS", "10,25,50,100,200").split(",")]
    hold = int(os.environ.get("STEP_SECONDS", "60"))

    def tick(self):
        i = int(self.get_run_time() // self.hold)
        if i >= len(self.steps):
            return None
        return self.steps[i], self.steps[i]
