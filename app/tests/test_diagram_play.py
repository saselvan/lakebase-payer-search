"""The drawer's motion diagram: it opens on the final frame, nothing moves until the reader presses Play, and under
prefers-reduced-motion: reduce there is no Play button (ticket 04, round 4, item 7; OBJECTIONS T4-53..T4-58).

DIAGRAM_DIR points the tests at another copy of diagram/ (the red runs in diagram/play_mutations.py use it).
"""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

DIAGRAM = Path(os.environ.get("DIAGRAM_DIR") or Path(__file__).resolve().parent.parent / "diagram")

# The final frame, read from the page: no packet, no tag, box 1 filled, the answer and its label shown.
FINAL_STATE_JS = r"""
() => {
  const q = s => document.querySelector(s);
  const alpha = el => { let o = 1; for (let e = el; e && e !== document.documentElement; e = e.parentElement)
                          o *= parseFloat(getComputedStyle(e).opacity); return o; };
  return { packet: alpha(q('.pfd-pk-sq')), tag: alpha(q('.pfd-tag')), fill1: alpha(q('.pfd-fill')),
           answer: alpha(q('.pfd-ans')), label: alpha(q('.pfd-answer-k')),
           box1: alpha(q('[data-hop="browser"] > .pfd-body')), box2: alpha(q('[data-hop="app"] > .pfd-body')),
           anims: document.getAnimations().map(a => ({ state: a.playState, t: a.currentTime,
                                                      end: a.effect.getComputedTiming().endTime })) };
}
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_page(browser, name="drawer.html", reduced="no-preference"):
    pg = browser.new_page(viewport={"width": 720, "height": 1100}, reduced_motion=reduced)
    pg.goto((DIAGRAM / name).as_uri())
    pg.evaluate("document.fonts.ready")
    return pg


def test_drawer_never_autoplays(browser):
    """For 5 s after load: no animation runs, and the page shows the final frame."""
    pg = open_page(browser)
    first = pg.screenshot()
    for _ in range(11):
        st = pg.evaluate(FINAL_STATE_JS)
        assert st["anims"], "the drawer made no animations, so Play could not run"
        running = [a for a in st["anims"] if a["state"] == "running"]
        assert not running, f"{len(running)} animations run with no click"
        assert all(abs(a["t"] - a["end"]) < 1 for a in st["anims"]), "an animation is not at its last frame"
        assert st["packet"] < 0.01 and st["tag"] < 0.01, "packet or tag on screen"
        assert st["fill1"] >= 0.99 and st["answer"] >= 0.99 and st["label"] >= 0.99, "not the final frame"
        pg.wait_for_timeout(500)
    assert pg.screenshot() == first, "the page changed in 5 s with no click"
    pg.close()


def test_play_is_a_real_button_and_runs_from_the_start(browser):
    pg = open_page(browser)
    play = pg.get_by_role("button", name="Play", exact=True)
    assert play.count() == 1
    assert play.evaluate("b => b.tagName + ' ' + b.type") == "BUTTON button"
    n = pg.evaluate("document.getAnimations().length")
    play.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(300)
    st = pg.evaluate(FINAL_STATE_JS)
    assert all(a["state"] == "running" for a in st["anims"]), "Enter on Play did not start the motion"
    assert max(a["t"] for a in st["anims"]) < 2000, "the motion did not start from the beginning"
    # Round 6: the motion opens on box 1 alone, lit; box 2 appears as the packet leaves (1.2 s on).
    assert st["box1"] >= 0.99 and st["fill1"] >= 0.99 and st["box2"] < 0.01, "the motion did not open on box 1 alone"
    pg.wait_for_timeout(1500)
    play.click()
    pg.wait_for_timeout(100)
    st = pg.evaluate(FINAL_STATE_JS)
    assert len(st["anims"]) == n, f"Play twice made {len(st['anims'])} animations, not {n}"
    assert max(a["t"] for a in st["anims"]) < 1000, "a second Play did not restart the motion"
    pg.close()


def test_no_play_button_under_reduced_motion(browser):
    pg = open_page(browser, reduced="reduce")
    assert pg.locator("button").count() == 0
    assert pg.evaluate("document.getAnimations().length") == 0
    st = pg.evaluate(FINAL_STATE_JS)
    assert st["packet"] < 0.01 and st["answer"] >= 0.99
    pg.close()


def test_reduced_motion_switched_on_mid_play_stops_and_removes_play(browser):
    pg = open_page(browser)
    pg.get_by_role("button", name="Play", exact=True).click()
    pg.wait_for_timeout(500)
    pg.emulate_media(reduced_motion="reduce")
    pg.wait_for_timeout(200)
    assert pg.locator("button").count() == 0
    assert pg.evaluate("document.getAnimations().length") == 0
    st = pg.evaluate(FINAL_STATE_JS)
    assert st["packet"] < 0.01 and st["tag"] < 0.01 and st["answer"] >= 0.99, "not the final frame"
    pg.close()


def test_no_play_at_390_px(browser):
    """At 390 px the drawer is a page (DECISIONS 2026-10-24): the still only. Play is not in the DOM, not in the
    accessibility tree and not a Tab stop; nothing animates."""
    pg = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="no-preference")
    pg.goto((DIAGRAM / "drawer.html").as_uri())
    pg.evaluate("document.fonts.ready")
    assert pg.locator("button, .pfd-play").count() == 0, "a Play control is in the DOM at 390 px"
    assert pg.get_by_role("button").count() == 0, "a button is in the accessibility tree at 390 px"
    tree = pg.locator("body").aria_snapshot()
    assert "Play" not in tree, "Play is in the accessibility tree at 390 px"
    stops = []
    for _ in range(12):
        pg.keyboard.press("Tab")
        stops.append(pg.evaluate("(document.activeElement && document.activeElement.textContent || '').trim()"))
    assert not [s for s in stops if s == "Play"], f"Play is a Tab stop at 390 px: {stops}"
    assert pg.evaluate("document.getAnimations().length") == 0, "the phone page animates"
    st = pg.evaluate(FINAL_STATE_JS)
    assert st["packet"] < 0.01 and st["tag"] < 0.01 and st["answer"] >= 0.99, "not the final frame"
    pg.close()


def test_resize_to_390_while_playing_stops_and_removes_play(browser):
    pg = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="no-preference")
    pg.goto((DIAGRAM / "drawer.html").as_uri())
    pg.evaluate("document.fonts.ready")
    pg.get_by_role("button", name="Play", exact=True).click()
    pg.wait_for_timeout(500)
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.wait_for_timeout(300)
    assert pg.locator("button, .pfd-play").count() == 0, "Play stays after the page narrows to 390 px"
    assert pg.evaluate("document.getAnimations().length") == 0, "the motion runs on at 390 px"
    st = pg.evaluate(FINAL_STATE_JS)
    assert st["packet"] < 0.01 and st["tag"] < 0.01 and st["answer"] >= 0.99, "not the final frame"
    pg.close()


def test_composition_has_no_button_and_never_autoplays(browser):
    """The video and the blog stills come from index.html: no Play there, and nothing runs until HyperFrames seeks."""
    pg = open_page(browser, name="index.html")
    assert pg.locator("button").count() == 0
    pg.wait_for_timeout(500)
    st = pg.evaluate(FINAL_STATE_JS)
    assert st["anims"] and not [a for a in st["anims"] if a["state"] == "running"], "the composition plays by itself"
    pg.close()
