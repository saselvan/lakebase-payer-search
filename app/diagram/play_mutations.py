"""Red runs for tests/test_diagram_play.py: each mutation changes one line of a temp copy of diagram/ (play-hidden-by-css: two), then the play
tests run against that copy (DIAGRAM_DIR). Every mutation must turn its test red.

    uv run python diagram/play_mutations.py          # all, log to runs/play-red-run.txt
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FILES = ("index.html", "drawer.html", "diagram.js", "diagram.css")

# name: (test it must turn red, file, old line part, new line part); a second edit may follow as (file, old, new).
MUTATIONS = {
    "autoplay": ("test_drawer_never_autoplays", "diagram.js",
                 "anims.forEach(function (a) { a.pause(); a.currentTime = atEnd ? a.effect.getComputedTiming().endTime : 0; });",
                 "anims.forEach(function (a) { a.currentTime = 0; a.play(); });"),
    "composition-autoplay": ("test_composition_has_no_button_and_never_autoplays", "diagram.js",
                             "anims.forEach(function (a) { a.pause(); a.currentTime = atEnd",
                             "anims.forEach(function (a) { if (atEnd) a.pause(); a.currentTime = atEnd"),
    "play-is-a-div": ("test_play_is_a_real_button_and_runs_from_the_start", "diagram.js",
                      'button = node("button", "pfd-play", ["Play"]);', 'button = node("div", "pfd-play", ["Play"]);'),
    "play-twice-duplicates": ("test_play_is_a_real_button_and_runs_from_the_start", "diagram.js",
                              "anims.forEach(function (a) { a.currentTime = 0; a.play(); });",
                              "anims.push.apply(anims, animate(root)); anims.forEach(function (a) { a.currentTime = 0; a.play(); });"),
    "button-under-reduce": ("test_no_play_button_under_reduced_motion", "diagram.js",
                            "if (!anims.length || !(opts && opts.controls)) return", "if (!(opts && opts.controls)) return"),
    "reduce-change-ignored": ("test_reduced_motion_switched_on_mid_play_stops_and_removes_play", "diagram.js",
                              'if (reduce.addEventListener) reduce.addEventListener("change", sync);', ""),
    # round 5 (T4-67, T4-68): the phone page
    "play-on-phone": ("test_no_play_at_390_px", "diagram.js",
                      'var NARROW = "(max-width: 719px)";', 'var NARROW = "(max-width: 1px)";'),
    "play-hidden-by-css": ("test_no_play_at_390_px", "diagram.js",
                           'var NARROW = "(max-width: 719px)";', 'var NARROW = "(max-width: 1px)";',
                           ("diagram.css", ".pfd-play:hover {", "@media (max-width: 719px) { .pfd-play { opacity: 0; } }\n.pfd-play:hover {")),
    "resize-ignored": ("test_resize_to_390_while_playing_stops_and_removes_play", "diagram.js",
                       'if (narrow.addEventListener) narrow.addEventListener("change", sync);', ""),
    "composition-button": ("test_composition_has_no_button_and_never_autoplays", "index.html",
                           '<div data-hf-id="hf-b9ut" id="pfd"></div>', '<div data-hf-id="hf-b9ut" id="pfd" data-controls></div>'),
}


def main() -> int:
    log, missed = [], []
    for name, (test, fname, old, new, *more) in MUTATIONS.items():
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for f in FILES:
                shutil.copy(HERE / f, d / f)
            shutil.copytree(HERE / "fonts", d / "fonts")
            src = (d / fname).read_text()
            assert src.count(old) == 1, f"{name}: anchor not found once in {fname}"
            (d / fname).write_text(src.replace(old, new))
            for f2, o2, n2 in more:
                s2 = (d / f2).read_text()
                assert s2.count(o2) == 1, f"{name}: anchor not found once in {f2}"
                (d / f2).write_text(s2.replace(o2, n2))
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                f"tests/test_diagram_play.py::{test}"],
                               cwd=REPO, env={**os.environ, "DIAGRAM_DIR": str(d)}, capture_output=True, text=True)
        out = r.stdout.strip().splitlines()
        reason = next((ln.strip() for ln in out if ln.strip().startswith("E ")), "")
        red = r.returncode != 0
        missed += [] if red else [name]
        log.append(f"=== {name}: {fname}: {old.strip()[:70]!r} -> {new.strip()[:70]!r}\n"
                   f"--> {test}: {'RED' if red else 'NOT CAUGHT'} (exit {r.returncode}) {reason}\n    {out[-1] if out else ''}")
    text = "\n".join(log) + f"\n\nsummary: {len(MUTATIONS) - len(missed)}/{len(MUTATIONS)} red" + \
        (f"; missed {missed}" if missed else "") + "\n"
    print(text)
    (HERE / "runs" / "play-red-run.txt").write_text(text)
    return 0 if not missed else 2


if __name__ == "__main__":
    sys.exit(main())
