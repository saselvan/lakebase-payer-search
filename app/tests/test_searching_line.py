"""I18 (session 4, item 1): the front status line says `Searching for "<q>"…` only when a search takes more than
300 ms, and the old results stay in place (owner decision, DECISIONS.md 2026-10-24).

A search is "fast" when the test answers it at once, and "slow" when the test holds its /api/search request
and answers it later. A MutationObserver records every text the status line held, so a flash is caught too.
"""
from __future__ import annotations

import time

import pytest

from tests.test_drawer import BASE, _router, app, browser  # noqa: F401 (fixtures)

LINE = "#status"
RECORD = """() => { window.__statusTexts = [];
  const s = document.querySelector('#status');
  new MutationObserver(() => window.__statusTexts.push(s.textContent))
    .observe(s, {childList: true, characterData: true, subtree: true}); }"""


@pytest.fixture
def page(browser, app):  # noqa: F811
    p = browser.new_page(viewport={"width": 1440, "height": 900})
    p.held, p.request_at = [], []
    p.hold = False
    handle = _router(app)

    def route(r):
        if r.request.method == "POST":
            p.request_at.append(time.monotonic())
            if p.hold:
                p.held.append(r)
                return None
        return handle(r)

    def release():
        for r in p.held:
            handle(r)
        p.held.clear()

    p.release = release
    p.route(f"{BASE}/**", route)
    p.goto(f"{BASE}/")
    p.evaluate(RECORD)
    yield p
    p.close()


def _type(page, q):
    """Type q and press Enter; return the time the request reached the route."""
    n = len(page.request_at)
    page.fill("#q", q)
    page.press("#q", "Enter")
    deadline = time.monotonic() + 3
    while len(page.request_at) == n:
        assert time.monotonic() < deadline, "the search request never left the page"
        page.wait_for_timeout(10)
    return page.request_at[-1]


def _wait_until(page, t0, seconds):
    left = t0 + seconds - time.monotonic()
    if left > 0:
        page.wait_for_timeout(left * 1000)


def _texts(page):
    return page.evaluate("() => window.__statusTexts")


def _groups(page):
    return page.eval_on_selector_all("#groups .group", "gs => gs.map(g => g.textContent)")


def _answered(page, q):
    _type(page, q)
    page.wait_for_function("q => document.querySelector('#status').textContent.includes(`for “${q}” ·`)", arg=q)


def test_a_fast_search_never_shows_the_searching_line(page):
    _answered(page, "bcbs georgia")
    _answered(page, "united helth")
    page.wait_for_timeout(500)
    assert not [t for t in _texts(page) if "Searching" in t], f"a fast search showed: {_texts(page)}"


def test_a_slow_search_shows_the_line_after_300_ms_and_keeps_the_old_results(page):
    _answered(page, "bcbs georgia")
    before_groups, before_line = _groups(page), page.text_content(LINE)
    page.hold = True
    t0 = _type(page, "united helth")
    _wait_until(page, t0, 0.15)
    assert page.text_content(LINE) == before_line, "the line changed before 300 ms"
    _wait_until(page, t0, 0.6)
    assert page.text_content(LINE) == "Searching for “united helth”…", f"line after 300 ms: {page.text_content(LINE)!r}"
    assert page.is_visible("#results"), "the results section is hidden during a slow search"
    assert _groups(page) == before_groups, "the old results did not stay during the search"
    page.release()
    page.wait_for_function("() => document.querySelector('#status').textContent.includes('for “united helth” ·')")
    assert "Searching" not in page.text_content(LINE), "the searching line stayed after the answer"


def test_a_slow_first_search_shows_the_line_with_no_results_under_it(page):
    page.hold = True
    t0 = _type(page, "bcbs georgia")
    _wait_until(page, t0, 0.6)
    assert page.is_visible("#results"), "no status line on a slow first search"
    assert page.text_content(LINE) == "Searching for “bcbs georgia”…"
    assert _groups(page) == [], "rows appeared before the answer"
    assert page.is_hidden("#more"), "Show 10 more is visible before the answer"
    page.release()
    page.wait_for_selector("#results:not([hidden]) .group")


def test_clearing_the_box_during_a_slow_search_shows_no_searching_line(page):
    page.hold = True
    t0 = _type(page, "bcbs georgia")
    page.fill("#q", "")
    _wait_until(page, t0, 0.6)
    assert not [t for t in _texts(page) if "Searching" in t], f"a cancelled search showed: {_texts(page)}"
