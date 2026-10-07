"""Offline tests for the reference client: token cache and refresh, retries, 401 handling.

A fake requests.Session plays back scripted HTTP statuses and records every POST, so each test
asserts how many requests were sent and with which token. No network, no sleeps.
"""
from __future__ import annotations

import pytest

from insurer_search import client as client_mod
from insurer_search.client import InsurerSearchClient, SearchError, TokenProvider


class FakeResponse:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Returns the scripted responses in order and records (url, headers) of every POST."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, **kw):
        self.posts.append((url, kw.get("headers") or {}))
        return self.responses.pop(0)


def token_session(*tokens: str, expires_in: int = 3600) -> FakeSession:
    return FakeSession([FakeResponse(200, {"access_token": t, "expires_in": expires_in}) for t in tokens])


ROWS = [{"payer_id": "SYN000001", "rank": 1}]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", sleeps.append)
    return sleeps


def make_client(api_responses, tokens=("tok-1", "tok-2", "tok-3"), max_retries=3):
    tp = TokenProvider("https://host.example", "id", "secret", session=token_session(*tokens))
    api = FakeSession(api_responses)
    return InsurerSearchClient("https://api.example/base", tp, session=api, max_retries=max_retries), tp, api


# --- token cache and refresh ---------------------------------------------------------------

def test_token_is_cached_while_far_from_expiry():
    tp = TokenProvider("https://host.example", "id", "secret", session=token_session("tok-1", "tok-2"))
    assert tp.token() == "tok-1"
    assert tp.token() == "tok-1"
    assert len(tp.session.posts) == 1


def test_token_is_refetched_within_five_minutes_of_expiry():
    # Hostile row: expires_in 299 s puts the first token inside the 300 s refresh window at once.
    tp = TokenProvider("https://host.example", "id", "secret",
                       session=token_session("tok-1", "tok-2", expires_in=299))
    assert tp.token() == "tok-1"
    assert tp.token() == "tok-2"
    assert len(tp.session.posts) == 2


def test_token_force_refetches_even_when_cached():
    tp = TokenProvider("https://host.example", "id", "secret", session=token_session("tok-1", "tok-2"))
    tp.token()
    assert tp.token(force=True) == "tok-2"


def test_token_endpoint_error_raises_with_its_status():
    tp = TokenProvider("https://host.example", "id", "secret",
                       session=FakeSession([FakeResponse(400, text="invalid_client")]))
    with pytest.raises(SearchError) as e:
        tp.token()
    assert e.value.status == 400
    assert "invalid_client" in e.value.body


# --- call(): retries and 401 ---------------------------------------------------------------

def test_401_refreshes_once_and_resends_even_with_no_retries():
    # The bug Isaac Review found: with max_retries=0 the refresh used the only attempt.
    c, tp, api = make_client([FakeResponse(401, text="expired"), FakeResponse(200, ROWS)], max_retries=0)
    assert c.call("search_insurers", {}) == ROWS
    assert len(api.posts) == 2
    assert api.posts[0][1]["Authorization"] == "Bearer tok-1"
    assert api.posts[1][1]["Authorization"] == "Bearer tok-2"


def test_401_refresh_does_not_use_up_a_retry():
    # 401, then max_retries (2) server errors, then success: all retries are still available.
    c, _, api = make_client([FakeResponse(401), FakeResponse(503), FakeResponse(500), FakeResponse(200, ROWS)],
                            max_retries=2)
    assert c.call("search_insurers", {}) == ROWS
    assert len(api.posts) == 4


def test_second_401_is_raised_not_refreshed_again():
    c, tp, api = make_client([FakeResponse(401), FakeResponse(401, text="still bad")])
    with pytest.raises(SearchError) as e:
        c.call("search_insurers", {})
    assert e.value.status == 401
    assert len(api.posts) == 2
    assert len(tp.session.posts) == 2      # first fetch + one forced refresh, no more


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_status_is_retried_then_succeeds(status, no_sleep):
    c, _, api = make_client([FakeResponse(status), FakeResponse(200, ROWS)])
    assert c.call("search_insurers", {}) == ROWS
    assert len(api.posts) == 2
    assert len(no_sleep) == 1              # backed off once


@pytest.mark.parametrize("status", [400, 403, 404])
def test_client_errors_are_never_retried(status):
    c, _, api = make_client([FakeResponse(status, text="nope"), FakeResponse(200, ROWS)])
    with pytest.raises(SearchError) as e:
        c.call("search_insurers", {})
    assert e.value.status == status
    assert len(api.posts) == 1


@pytest.mark.parametrize("max_retries", [0, 2, 5])
def test_server_errors_stop_after_max_retries_plus_one_sends(max_retries, no_sleep):
    # Varied budget (not only the default 3) so a hardcoded count cannot pass.
    c, _, api = make_client([FakeResponse(500, text="boom")] * (max_retries + 3), max_retries=max_retries)
    with pytest.raises(SearchError) as e:
        c.call("search_insurers", {})
    assert e.value.status == 500           # the real last status, not a made-up code
    assert len(api.posts) == max_retries + 1
    assert len(no_sleep) == max_retries
